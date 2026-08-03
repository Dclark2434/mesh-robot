"""The dashboard's HTTP and WebSocket server.

Runs inside the brain process on its own port, sharing the event loop. It is
strictly read-only: it observes the robot, it cannot drive it. That is a
deliberate limit for a first version; a control surface reachable by anything
on the LAN deserves its own thought about who is allowed to make a robot walk.

Camera frames are served as an ordinary JPEG endpoint and polled by the page,
rather than pushed down the WebSocket. It keeps the event stream text-only, it
lets the browser handle caching and backpressure, and a dashboard nobody is
looking at costs nothing.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from mesh_common.logging import get_logger
from mesh_server.dashboard.events import EventBus
from mesh_server.dashboard.health import HealthTracker

logger = get_logger("dashboard")

PAGE = Path(__file__).parent / "index.html"

#: Frames are re-encoded at most this often however fast the page polls.
FRAME_CACHE_SECS = 1.0

#: Dashboard preview width. Small on purpose: this is a monitoring thumbnail,
#: not what the model sees.
PREVIEW_WIDTH = 480


class DashboardServer:
    """Serves the dashboard page, its event stream, and camera previews."""

    def __init__(
        self,
        event_bus: EventBus,
        health: HealthTracker,
        feed: Any,
        state: dict[str, Any],
    ) -> None:
        """Create the server.

        Args:
            event_bus: Bus to stream events from.
            health: Component health, polled by the status panel.
            feed: The camera buffer, or None if vision is disabled.
            state: Static facts about this run (persona, model names) shown
                in the dashboard header.
        """
        self._bus = event_bus
        self._health = health
        self._feed = feed
        self._state = state
        self._runner: web.AppRunner | None = None
        self._frame_cache: tuple[float, bytes] | None = None

    async def start(self, host: str, port: int) -> None:
        """Begin serving.

        Args:
            host: Interface to bind. Defaults to all, so the dashboard is
                reachable from a phone on the same network.
            port: TCP port.
        """
        app = web.Application()
        app.add_routes(
            [
                web.get("/", self._page),
                web.get("/events", self._events),
                web.get("/api/state", self._api_state),
                web.get("/api/health", self._api_health),
                web.get("/api/frame.jpg", self._api_frame),
            ]
        )

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, host, port).start()
        logger.info(f"Dashboard on http://{host if host != '0.0.0.0' else 'localhost'}:{port}")

    async def stop(self) -> None:
        """Stop serving and release the port."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _page(self, _request: web.Request) -> web.StreamResponse:
        """Serve the dashboard page.

        Args:
            _request: The HTTP request.

        Returns:
            The HTML response.
        """
        return web.FileResponse(PAGE)

    async def _api_state(self, _request: web.Request) -> web.Response:
        """Serve the current state a dashboard needs on connect.

        Args:
            _request: The HTTP request.

        Returns:
            JSON describing this run.
        """
        latest = {
            kind: event.to_dict()
            for kind in ("status", "telemetry", "ambient", "latency", "connection")
            if (event := self._bus.latest(kind)) is not None
        }
        return web.json_response({**self._state, "latest": latest})

    async def _api_health(self, _request: web.Request) -> web.Response:
        """Serve component health.

        Polled rather than pushed: staleness is a function of elapsed time
        rather than of anything happening, so an event stream would have
        nothing to send at exactly the moment something went quiet.

        Args:
            _request: The HTTP request.

        Returns:
            JSON listing every component and an overall summary.
        """
        # Frames arriving is the only evidence the camera is alive. Its state
        # and detail come from the robot's hello, but without touching it here
        # a working camera would age into "stale" after half a minute.
        if self._feed is not None and self._feed.latest() is not None:
            self._health.touch("camera")

        return web.json_response(
            {"overall": self._health.overall(), "components": self._health.snapshot()}
        )

    async def _api_frame(self, _request: web.Request) -> web.Response:
        """Serve the most recent camera frame as a JPEG.

        Args:
            _request: The HTTP request.

        Returns:
            A JPEG response, or 503 when there is nothing to show.
        """
        jpeg = await self._encode_latest()
        if jpeg is None:
            return web.Response(status=503, text="no frame")
        return web.Response(
            body=jpeg,
            content_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    async def _encode_latest(self) -> bytes | None:
        """Compress the newest camera frame, reusing a recent encode.

        Returns:
            JPEG bytes, or None if no fresh frame exists.
        """
        if self._feed is None:
            return None
        glimpse = self._feed.latest()
        if glimpse is None:
            return None

        now = time.monotonic()
        if self._frame_cache and now - self._frame_cache[0] < FRAME_CACHE_SECS:
            return self._frame_cache[1]

        def encode() -> bytes:
            from PIL import Image

            image = Image.frombytes(glimpse.format, glimpse.size, glimpse.image)
            width, height = image.size
            if width > PREVIEW_WIDTH:
                image = image.resize(
                    (PREVIEW_WIDTH, max(1, height * PREVIEW_WIDTH // width))
                )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=70)
            return buffer.getvalue()

        try:
            jpeg = await asyncio.to_thread(encode)
        except Exception as exc:
            logger.debug(f"Preview encode failed: {exc}")
            return None

        self._frame_cache = (now, jpeg)
        return jpeg

    async def _events(self, request: web.Request) -> web.WebSocketResponse:
        """Stream events to one dashboard.

        Replays recent history first so a page opened mid-conversation is not
        blank, then follows live.

        Args:
            request: The upgrade request.

        Returns:
            The closed WebSocket response.
        """
        socket = web.WebSocketResponse(heartbeat=30)
        await socket.prepare(request)

        queue = self._bus.subscribe()
        logger.debug(f"Dashboard connected ({self._bus.subscriber_count} open)")

        try:
            for event in self._bus.history():
                await socket.send_str(json.dumps(event.to_dict()))

            pump = asyncio.create_task(self._pump(socket, queue))
            async for message in socket:
                if message.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
            pump.cancel()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._bus.unsubscribe(queue)
            logger.debug("Dashboard disconnected")

        return socket

    @staticmethod
    async def _pump(socket: web.WebSocketResponse, queue: asyncio.Queue) -> None:
        """Forward queued events to one socket until cancelled.

        Args:
            socket: The client's WebSocket.
            queue: That client's event queue.
        """
        while True:
            event = await queue.get()
            try:
                await socket.send_str(json.dumps(event.to_dict()))
            except Exception:
                return
