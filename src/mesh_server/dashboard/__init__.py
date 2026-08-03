"""A read-only web console for watching the robot think.

Only the dependency-free pieces are re-exported. The aiohttp server and the
pipeline taps are imported directly, so the event and health rules stay
testable without a web stack or the real-time pipeline installed.
"""

from mesh_server.dashboard.events import Event, EventBus, bus
from mesh_server.dashboard.health import Component, HealthTracker, State

__all__ = ["Component", "Event", "EventBus", "HealthTracker", "State", "bus"]
