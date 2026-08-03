"""What is up, what is down, and what has gone quiet.

The dashboard's status panel answers one question: if the robot is not
responding, which part of it is broken? That means the components listed here
are the ones that can independently fail (the robot's link, each cloud
service on the critical path, the camera, the echo canceller) rather than a
tidy architectural diagram.

Two states deserve explanation. ``UNKNOWN`` means a component has not been
exercised yet: Deepgram is not "down" before anyone has spoken to the robot,
and showing it red would train you to ignore red. ``STALE`` means something
that should report periodically has stopped, which is distinct from a reported
failure and usually means a link died rather than a service.

Kept free of pipeline and network imports so the rules can be tested directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    """Health of a single component."""

    OK = "ok"
    WARN = "warn"
    DOWN = "down"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass
class Component:
    """One independently-failing part of the system.

    Attributes:
        key: Stable identifier.
        label: Human-readable name for the dashboard.
        group: Which column it belongs in.
        state: Current health.
        detail: Short explanation, e.g. a latency or an error message.
        updated_at: Monotonic time of the last update.
        stale_after: Seconds of silence after which the component is treated
            as stale, or None if silence is normal. Deepgram going quiet
            overnight is fine; the robot's telemetry going quiet is not.
    """

    key: str
    label: str
    group: str
    state: State = State.UNKNOWN
    detail: str = ""
    updated_at: float = field(default_factory=time.monotonic)
    stale_after: float | None = None

    def to_dict(self, now: float) -> dict:
        """Serialize for the dashboard, applying staleness at read time.

        Args:
            now: Current monotonic time.

        Returns:
            A JSON-compatible mapping.
        """
        state = self.state
        age = now - self.updated_at
        if (
            self.stale_after is not None
            and state in (State.OK, State.WARN)
            and age > self.stale_after
        ):
            state = State.STALE
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "state": state.value,
            "detail": self.detail,
            "age": round(age, 1),
        }


class HealthTracker:
    """The set of components the dashboard shows, and their current state."""

    def __init__(self) -> None:
        """Create a tracker with the standard component set."""
        self._components: dict[str, Component] = {}
        for key, label, group, stale_after in (
            # The brain and its pipeline.
            ("brain", "Brain process", "server", None),
            ("pipeline", "Pipecat pipeline", "server", None),
            ("livekit", "LiveKit room", "server", None),
            ("stt", "Deepgram STT", "server", None),
            ("llm", "Gemini", "server", None),
            ("tts", "ElevenLabs", "server", None),
            # The robot. Everything here is reported over the data channel, so
            # silence is meaningful, hence the staleness windows.
            ("robot", "Pi client", "robot", 90.0),
            ("hardware", "Servos / I2C", "robot", None),
            ("audio", "Echo cancellation", "robot", None),
            ("camera", "Camera", "robot", 30.0),
            ("battery", "Battery", "robot", 120.0),
        ):
            self._components[key] = Component(key, label, group, stale_after=stale_after)

    def update(self, key: str, state: State, detail: str = "") -> None:
        """Set a component's state.

        Args:
            key: Component identifier. Unknown keys are ignored rather than
                raising, since this is called from error paths.
            state: New state.
            detail: Short explanation shown next to the indicator.
        """
        component = self._components.get(key)
        if component is None:
            return
        component.state = state
        component.detail = detail
        component.updated_at = time.monotonic()

    def touch(self, key: str) -> None:
        """Record that a component is still alive without changing its state.

        Args:
            key: Component identifier.
        """
        component = self._components.get(key)
        if component is not None:
            component.updated_at = time.monotonic()

    def snapshot(self) -> list[dict]:
        """Render every component for the dashboard.

        Returns:
            One mapping per component, grouped order preserved.
        """
        now = time.monotonic()
        return [component.to_dict(now) for component in self._components.values()]

    def overall(self) -> str:
        """Summarize the whole system in one word.

        Returns:
            ``"down"`` if anything is down, ``"warn"`` if anything is degraded
            or stale, ``"ok"`` if at least something is working, else
            ``"unknown"``.
        """
        states = {item["state"] for item in self.snapshot()}
        if State.DOWN.value in states:
            return "down"
        if State.WARN.value in states or State.STALE.value in states:
            return "warn"
        if State.OK.value in states:
            return "ok"
        return "unknown"
