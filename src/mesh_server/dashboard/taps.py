"""Where dashboard events come from.

A logging handler that mirrors console output to the bus, and one pipeline
processor that reports what was said. Everything else -- latency, gestures,
status, telemetry -- is published from the code that already handles it, since
those places have the data in hand and publishing is a single call.
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from mesh_server.dashboard.events import EventBus


class DashboardLogHandler(logging.Handler):
    """Mirrors log records onto the event bus.

    Attached to the same loggers that write to the console, so the dashboard
    shows exactly what the terminal shows -- the point being that you should
    not have to be at the workstation to watch the robot think.
    """

    def __init__(self, event_bus: EventBus) -> None:
        """Create the handler.

        Args:
            event_bus: Bus to publish log records to.
        """
        super().__init__()
        self._bus = event_bus

    def emit(self, record: logging.LogRecord) -> None:
        """Publish one log record.

        Args:
            record: The record being logged.
        """
        try:
            self._bus.publish(
                "log",
                level=record.levelname,
                source=record.name,
                message=record.getMessage(),
            )
        except Exception:  # logging must never take the process down
            pass


def attach_log_bridge(event_bus: EventBus, level: int = logging.INFO) -> None:
    """Mirror this project's loggers to the dashboard.

    Args:
        event_bus: Bus to publish to.
        level: Minimum level to forward.
    """
    handler = DashboardLogHandler(event_bus)
    handler.setLevel(level)
    # get_logger() creates loggers lazily with propagate=False, so the handler
    # goes onto each one rather than onto the root.
    for name in list(logging.Logger.manager.loggerDict):
        logger = logging.getLogger(name)
        if any(isinstance(existing, DashboardLogHandler) for existing in logger.handlers):
            continue
        if logger.handlers:  # ours are the ones with a console handler attached
            logger.addHandler(handler)


class TranscriptTap(FrameProcessor):
    """Publishes both sides of the conversation to the dashboard.

    Placed after the action-tag processor so the assistant text it reports is
    the text that was actually spoken -- directives already stripped -- rather
    than the raw model output with tags still in it.
    """

    def __init__(self, event_bus: EventBus) -> None:
        """Create the tap.

        Args:
            event_bus: Bus to publish to.
        """
        super().__init__()
        self._bus = event_bus
        self._reply: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Watch for finished user and assistant turns.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            if frame.text.strip():
                self._bus.publish("transcript", role="user", text=frame.text.strip())
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._reply = []
        elif isinstance(frame, LLMTextFrame):
            self._reply.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            spoken = "".join(self._reply).strip()
            self._reply = []
            if spoken:
                self._bus.publish("transcript", role="assistant", text=spoken)

        await self.push_frame(frame, direction)
