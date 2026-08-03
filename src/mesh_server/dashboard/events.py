"""The event stream the dashboard renders.

Everything interesting that happens in the brain (a log line, a turn's
latency breakdown, a gesture firing, the robot's battery) is published here
as a small JSON-serializable event. The dashboard subscribes; if nothing is
subscribed, publishing costs a deque append and nothing else.

A process-wide bus rather than an object threaded through every constructor.
Observability is cross-cutting in the same way logging is, and making each
pipeline processor carry a dashboard reference would put presentation concerns
into code that has no other reason to know about them.

Publishing is safe from any thread. Delivery to subscribers is marshalled onto
the event loop, because the motion dispatcher and the audio callback both log
from threads of their own.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: Events retained for clients that connect late, so a dashboard opened
#: mid-conversation is not blank.
HISTORY_LIMIT = 400

#: Dropped rather than queued without bound if a client stops reading.
SUBSCRIBER_LIMIT = 200


@dataclass
class Event:
    """One thing that happened.

    Attributes:
        kind: Event type, which selects how the dashboard renders it.
        data: Payload; must be JSON-serializable.
        at: Unix timestamp.
    """

    kind: str
    data: dict[str, Any]
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the wire.

        Returns:
            A JSON-compatible mapping.
        """
        return {"kind": self.kind, "at": self.at, **self.data}


class EventBus:
    """Fan-out of events to any connected dashboards, plus a short history."""

    def __init__(self, history_limit: int = HISTORY_LIMIT) -> None:
        """Create an idle bus.

        Args:
            history_limit: How many past events to replay to new subscribers.
        """
        self._history: deque[Event] = deque(maxlen=history_limit)
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict[str, Event] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the bus to the running event loop.

        Until this is called the bus still records history, so events published
        during startup are not lost.

        Args:
            loop: The loop subscribers will be woken on.
        """
        self._loop = loop

    def publish(self, kind: str, **data: Any) -> None:
        """Record an event and deliver it to subscribers.

        Safe to call from any thread, and from code that has no idea whether a
        dashboard exists.

        Args:
            kind: Event type.
            **data: JSON-serializable payload.
        """
        event = Event(kind, data)
        self._history.append(event)
        self._latest[kind] = event

        if self._loop is None or not self._subscribers:
            return
        try:
            self._loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            pass  # loop closed during shutdown

    def _deliver(self, event: Event) -> None:
        """Push an event to every subscriber, skipping any that has stalled.

        Args:
            event: The event to deliver.
        """
        for queue in list(self._subscribers):
            if queue.qsize() >= SUBSCRIBER_LIMIT:
                continue  # a wedged browser tab must not stall the robot
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[Event]:
        """Register a new subscriber.

        Returns:
            A queue that will receive events as they are published.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """Remove a subscriber.

        Args:
            queue: The queue returned by :meth:`subscribe`.
        """
        self._subscribers.discard(queue)

    def history(self) -> list[Event]:
        """Past events, oldest first.

        Returns:
            A snapshot of the retained history.
        """
        return list(self._history)

    def latest(self, kind: str) -> Event | None:
        """The most recent event of one kind.

        Used for state the dashboard should show immediately on connect --
        current status, battery, ambient note, rather than having to wait for
        it to happen again.

        Args:
            kind: Event type.

        Returns:
            The most recent event of that kind, or None.
        """
        return self._latest.get(kind)

    @property
    def subscriber_count(self) -> int:
        """How many dashboards are currently connected."""
        return len(self._subscribers)


#: The process-wide bus. Publishing to it when no dashboard is running is a
#: deque append, so pipeline code can call it unconditionally.
bus = EventBus()
