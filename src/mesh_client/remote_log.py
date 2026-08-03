"""Shipping the robot's log output to the brain, for the dashboard.

The Pi's terminal is the least convenient one in the system; it is over SSH,
on a machine that is walking around. Its logs are mirrored to the brain so both
halves can be read side by side in one place.

Two things this has to avoid. The robot logs a line per gait cycle, so a data
channel packet per line would be its own performance problem: lines are batched
on a short timer instead. And a handler that logs about its own failures
recurses forever, so it never logs at all, failures are counted and reported
in the next batch as a dropped count.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable

from mesh_common.protocol import LogMessage, encode

#: How often batches go out. Long enough to coalesce a gait cycle's worth of
#: lines, short enough to feel live.
FLUSH_INTERVAL_SECS = 0.4

#: Lines held between flushes. Beyond this the oldest are dropped, because a
#: robot that is spewing logs must not also consume its own memory.
QUEUE_LIMIT = 200

#: Lines per batch, to keep any single data channel packet small.
BATCH_LIMIT = 40


class RemoteLogHandler(logging.Handler):
    """Buffers log records for periodic shipment to the brain."""

    def __init__(self) -> None:
        """Create an idle handler."""
        super().__init__()
        self._lines: deque[dict[str, str]] = deque(maxlen=QUEUE_LIMIT)
        self._dropped = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Buffer one log record.

        Args:
            record: The record being logged.
        """
        try:
            entry = {
                "level": record.levelname,
                "source": record.name,
                "message": record.getMessage(),
            }
        except Exception:
            return

        with self._lock:
            if len(self._lines) == QUEUE_LIMIT:
                self._dropped += 1
            self._lines.append(entry)

    def take(self) -> tuple[list[dict[str, str]], int]:
        """Remove and return the buffered lines.

        Returns:
            A ``(lines, dropped)`` pair. ``dropped`` counts lines discarded
            since the last call because the robot outran the link.
        """
        with self._lock:
            lines = [self._lines.popleft() for _ in range(min(BATCH_LIMIT, len(self._lines)))]
            dropped, self._dropped = self._dropped, 0
        return lines, dropped


class RemoteLogShipper:
    """Attaches the handler to this project's loggers and flushes on a timer."""

    def __init__(self, publish: Callable[[str], "asyncio.Future | None"]) -> None:
        """Create the shipper.

        Args:
            publish: Coroutine function taking one encoded message, used to put
                a batch on the data channel.
        """
        self._publish = publish
        self._handler = RemoteLogHandler()
        self._task: asyncio.Task | None = None

    def attach(self, level: int = logging.INFO) -> None:
        """Mirror this project's loggers into the buffer.

        Args:
            level: Minimum level to ship.
        """
        self._handler.setLevel(level)
        for name in list(logging.Logger.manager.loggerDict):
            logger = logging.getLogger(name)
            if logger.handlers and not any(
                isinstance(existing, RemoteLogHandler) for existing in logger.handlers
            ):
                logger.addHandler(self._handler)

    def start(self) -> None:
        """Begin flushing batches to the brain."""
        self._task = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        """Ship buffered lines until cancelled."""
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_SECS)
            lines, dropped = self._handler.take()
            if not lines and not dropped:
                continue
            try:
                await self._publish(encode(LogMessage(lines, dropped)))
            except Exception:
                # Deliberately silent: logging about a failure to ship logs
                # would generate the very traffic that just failed.
                pass

    async def stop(self) -> None:
        """Stop shipping and detach the handler."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        for name in list(logging.Logger.manager.loggerDict):
            logging.getLogger(name).removeHandler(self._handler)
