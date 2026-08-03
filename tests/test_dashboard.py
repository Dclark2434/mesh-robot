"""Tests for the dashboard's event bus and health model.

Both exist to make a broken robot diagnosable, so the cases here are mostly
about not lying: a component nobody has exercised is not "healthy", one that
has gone quiet is distinguishable from one that reported a failure, and a
dashboard nobody is reading cannot stall the pipeline that feeds it.
"""

import asyncio

from mesh_server.dashboard.events import SUBSCRIBER_LIMIT, EventBus
from mesh_server.dashboard.health import HealthTracker, State


#, event bus ------------------------------------------------------------


def test_publishing_without_a_dashboard_is_harmless():
    # Pipeline code publishes unconditionally; nothing may depend on a
    # subscriber existing.
    bus = EventBus()
    bus.publish("log", message="hello")
    assert len(bus.history()) == 1


def test_history_is_replayed_to_late_joiners():
    bus = EventBus()
    bus.publish("log", message="before you arrived")
    assert [e.data["message"] for e in bus.history()] == ["before you arrived"]


def test_history_is_bounded():
    bus = EventBus(history_limit=10)
    for i in range(50):
        bus.publish("log", message=str(i))
    history = bus.history()
    assert len(history) == 10
    assert history[-1].data["message"] == "49"


def test_latest_tracks_each_kind_separately():
    bus = EventBus()
    bus.publish("status", status="listening")
    bus.publish("log", message="noise")
    bus.publish("status", status="speaking")
    assert bus.latest("status").data["status"] == "speaking"
    assert bus.latest("telemetry") is None


def test_events_serialize_flat_for_the_wire():
    bus = EventBus()
    bus.publish("gesture", action="wave", param=None)
    payload = bus.history()[0].to_dict()
    assert payload["kind"] == "gesture"
    assert payload["action"] == "wave"
    assert "at" in payload


def test_subscribers_receive_published_events():
    async def scenario():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        queue = bus.subscribe()
        bus.publish("log", message="live")
        await asyncio.sleep(0)  # let the loop deliver
        return await asyncio.wait_for(queue.get(), timeout=1)

    assert asyncio.run(scenario()).data["message"] == "live"


def test_a_stalled_dashboard_does_not_stall_the_robot():
    # A browser tab that stops reading must be starved, not backpressure the
    # pipeline that is publishing.
    async def scenario():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        queue = bus.subscribe()
        for i in range(SUBSCRIBER_LIMIT + 50):
            bus.publish("log", message=str(i))
        await asyncio.sleep(0)
        return queue.qsize()

    assert asyncio.run(scenario()) <= SUBSCRIBER_LIMIT


def test_unsubscribing_stops_delivery():
    async def scenario():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        bus.publish("log", message="gone")
        await asyncio.sleep(0)
        return queue.qsize(), bus.subscriber_count

    assert asyncio.run(scenario()) == (0, 0)


#, health ---------------------------------------------------------------


def test_untouched_components_are_unknown_not_healthy():
    # Deepgram is not "down" before anyone has spoken, and it is not "up"
    # either. Showing red for either would train you to ignore red.
    tracker = HealthTracker()
    states = {c["key"]: c["state"] for c in tracker.snapshot()}
    assert states["stt"] == "unknown"
    assert tracker.overall() == "unknown"


def test_both_halves_of_the_system_are_represented():
    groups = {c["group"] for c in HealthTracker().snapshot()}
    assert groups == {"server", "robot"}


def test_updating_sets_state_and_detail():
    tracker = HealthTracker()
    tracker.update("llm", State.OK, "317ms")
    entry = next(c for c in tracker.snapshot() if c["key"] == "llm")
    assert entry["state"] == "ok"
    assert entry["detail"] == "317ms"


def test_unknown_keys_are_ignored_rather_than_raising():
    # update() is called from error paths; it must not add a second failure.
    HealthTracker().update("nonexistent", State.DOWN)


def test_anything_down_makes_the_whole_system_down():
    tracker = HealthTracker()
    tracker.update("brain", State.OK)
    tracker.update("robot", State.DOWN, "disconnected")
    assert tracker.overall() == "down"


def test_a_warning_degrades_but_does_not_alarm():
    tracker = HealthTracker()
    tracker.update("brain", State.OK)
    tracker.update("audio", State.WARN, "no AEC")
    assert tracker.overall() == "warn"


def test_a_component_that_goes_quiet_reads_as_stale():
    # Distinct from "down": nothing reported a failure, the reports stopped.
    tracker = HealthTracker()
    tracker.update("robot", State.OK, "connected")
    component = tracker._components["robot"]
    component.updated_at -= component.stale_after + 10
    entry = next(c for c in tracker.snapshot() if c["key"] == "robot")
    assert entry["state"] == "stale"


def test_components_that_report_only_on_change_never_go_stale():
    # Deepgram going quiet overnight is not a fault.
    tracker = HealthTracker()
    tracker.update("stt", State.OK, "141ms")
    tracker._components["stt"].updated_at -= 10_000
    entry = next(c for c in tracker.snapshot() if c["key"] == "stt")
    assert entry["state"] == "ok"


def test_touch_refreshes_liveness_without_changing_state():
    tracker = HealthTracker()
    tracker.update("camera", State.OK, "picamera2")
    tracker._components["camera"].updated_at -= 10_000
    tracker.touch("camera")
    entry = next(c for c in tracker.snapshot() if c["key"] == "camera")
    assert entry["state"] == "ok"
    assert entry["detail"] == "picamera2"


def test_a_failure_is_not_masked_by_staleness():
    tracker = HealthTracker()
    tracker.update("camera", State.DOWN, "no signal")
    tracker._components["camera"].updated_at -= 10_000
    entry = next(c for c in tracker.snapshot() if c["key"] == "camera")
    assert entry["state"] == "down"
