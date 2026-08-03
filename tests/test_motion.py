"""Tests for the robot-side motion dispatcher.

Covers the two behaviours that make gestures feel deliberate rather than
glitchy: lanes running independently, and stale gestures being dropped instead
of performed late.
"""

import threading
import time

import pytest

from mesh_client.motion import MotionDispatcher, steps_from


@pytest.fixture
def dispatcher():
    """Provide a started dispatcher and stop it afterwards.

    Yields:
        A running MotionDispatcher.
    """
    disp = MotionDispatcher()
    disp.start()
    yield disp
    disp.stop()


def test_registering_an_unknown_action_is_an_error():
    # Catches client/protocol drift at startup rather than mid-conversation.
    with pytest.raises(KeyError):
        MotionDispatcher().register("teleport", lambda p: None)


def test_missing_handlers_are_reported():
    disp = MotionDispatcher()
    disp.register("wave", lambda p: None)
    missing = disp.missing_handlers()
    assert "wave" not in missing
    assert "nod" in missing


def test_action_runs_with_its_parameter(dispatcher):
    seen = []
    done = threading.Event()
    dispatcher.register("walk_forward", lambda p: (seen.append(p), done.set()))

    dispatcher.submit("walk_forward", "5", None)
    assert done.wait(2.0)
    assert seen == ["5"]


def test_aliases_are_resolved_on_the_robot_too(dispatcher):
    done = threading.Event()
    dispatcher.register("walk_forward", lambda p: done.set())

    dispatcher.submit("walk", None, None)  # alias
    assert done.wait(2.0)


def test_expired_gesture_is_dropped(dispatcher):
    ran = threading.Event()
    dispatcher.register("wave", lambda p: ran.set())

    # A deadline already in the past: the robot was busy when it arrived.
    dispatcher.submit("wave", None, -1.0)
    assert not ran.wait(0.5)


def test_command_without_a_deadline_always_runs(dispatcher):
    ran = threading.Event()
    dispatcher.register("reset", lambda p: ran.set())

    dispatcher.submit("reset", None, None)
    assert ran.wait(2.0)


def test_head_gesture_does_not_wait_behind_a_body_action(dispatcher):
    # The whole point of lanes: a glance should not queue behind a walk.
    body_started = threading.Event()
    head_done = threading.Event()

    def slow_walk(_param):
        body_started.set()
        time.sleep(1.0)

    dispatcher.register("walk_forward", slow_walk)
    dispatcher.register("look_left", lambda p: head_done.set())

    dispatcher.submit("walk_forward", "5", None)
    assert body_started.wait(2.0)
    dispatcher.submit("look_left", None, 3.0)
    assert head_done.wait(0.5), "head lane blocked by body lane"


def test_a_failing_action_does_not_stop_the_lane(dispatcher):
    survived = threading.Event()

    def explode(_param):
        raise RuntimeError("servo bus fell over")

    dispatcher.register("wave", explode)
    dispatcher.register("bow", lambda p: survived.set())

    dispatcher.submit("wave", None, None)
    dispatcher.submit("bow", None, None)
    assert survived.wait(2.0)


def test_a_failing_action_runs_only_once(dispatcher):
    # The old dispatcher caught TypeError from inside the handler and retried
    # with a different signature, executing the gesture twice.
    calls = []

    def explode(_param):
        calls.append(1)
        raise TypeError("not a signature problem")

    dispatcher.register("wave", explode)
    dispatcher.submit("wave", None, None)
    time.sleep(0.5)
    assert calls == [1]


@pytest.mark.parametrize(
    "param,expected",
    [
        (None, 4),
        ("", 4),
        ("5", 5),
        ("5 steps", 5),
        ("nine", 4),
        ("500", 20),
        ("0", 1),
    ],
)
def test_step_parsing(param, expected):
    assert steps_from(param) == expected
