"""Tests for stopping a movement that is already under way.

The behaviour that matters: a stop lands within one servo frame rather than at
the end of the action, the robot still restores a stable stance on the way out,
and a stop applies to the motion that was running when it was requested rather
than to whatever is asked for next.
"""

import threading
import time

import pytest

from mesh_client.cancellation import CancelToken, MotionCancelled
from mesh_client.motion import MotionDispatcher


# -- the token ------------------------------------------------------------


def test_sleep_returns_normally_when_not_cancelled():
    started = time.monotonic()
    CancelToken().sleep(0.05)
    assert time.monotonic() - started >= 0.04


def test_sleep_raises_when_already_cancelled():
    token = CancelToken()
    token.cancel()
    with pytest.raises(MotionCancelled):
        token.sleep(5.0)


def test_sleep_wakes_immediately_when_cancelled_mid_wait():
    # This is the whole point: a stop must not wait out the current pause.
    token = CancelToken()
    threading.Timer(0.05, token.cancel).start()
    started = time.monotonic()
    with pytest.raises(MotionCancelled):
        token.sleep(5.0)
    assert time.monotonic() - started < 1.0


def test_checkpoint_aborts_only_when_cancelled():
    token = CancelToken()
    token.checkpoint()
    token.cancel()
    with pytest.raises(MotionCancelled):
        token.checkpoint()


def test_clearing_lets_the_next_movement_run():
    token = CancelToken()
    token.cancel()
    token.clear()
    assert not token.cancelled
    token.sleep(0.01)


# -- the dispatcher -------------------------------------------------------


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


def test_a_running_action_is_stopped(dispatcher):
    stopped = threading.Event()
    started = threading.Event()

    def long_walk(_param):
        started.set()
        try:
            for _ in range(200):
                dispatcher.cancel.sleep(0.05)  # ten seconds if uninterrupted
        except MotionCancelled:
            stopped.set()
            raise

    dispatcher.register("walk_forward", long_walk)
    dispatcher.submit("walk_forward", "20", None)
    assert started.wait(2.0)

    dispatcher.abort()
    assert stopped.wait(1.0), "a walk in progress should stop promptly"


def test_stopping_also_discards_what_was_queued_behind_it(dispatcher):
    started = threading.Event()
    later_ran = threading.Event()

    def long_walk(_param):
        started.set()
        for _ in range(200):
            dispatcher.cancel.sleep(0.05)

    dispatcher.register("walk_forward", long_walk)
    dispatcher.register("bow", lambda p: later_ran.set())

    dispatcher.submit("walk_forward", "20", None)
    assert started.wait(2.0)
    dispatcher.submit("bow", None, None)
    dispatcher.abort()

    assert not later_ran.wait(0.5), "queued actions should be dropped, not run"


def test_a_stop_does_not_cancel_the_next_action(dispatcher):
    # The flag applies to the motion that was running when it was raised.
    ran = threading.Event()
    dispatcher.register("wave", lambda p: ran.set())

    dispatcher.abort()
    dispatcher.submit("wave", None, None)

    assert ran.wait(2.0), "a later action must not inherit the cancellation"


def test_stop_is_not_queued_behind_the_motion_it_interrupts():
    # Stop lives in the aux lane precisely so it never waits on the body lane.
    from mesh_common.protocol import Lane, resolve_action

    assert resolve_action("stop").lane is Lane.AUX
    assert resolve_action("walk_forward").lane is Lane.BODY


def test_stop_aliases_resolve():
    from mesh_common.protocol import resolve_action

    for word in ("stop", "halt", "freeze", "wait"):
        assert resolve_action(word).name == "stop", word


def test_cancelling_reports_what_it_stopped(dispatcher):
    dispatcher.register("wave", lambda p: None)
    assert "cancelled" in dispatcher.abort()


# -- against the real gait and animation code -----------------------------


@pytest.fixture(scope="module")
def controllers():
    """Real locomotion and animation controllers driving mocked servos.

    Module-scoped because constructing LocomotionController searches the
    filesystem for a calibration file, which costs seconds. Each test installs
    its own token, so they do not share cancellation state.

    Yields:
        A ``(legs, anim)`` pair.
    """
    from unittest.mock import MagicMock

    from mesh_client.animation_controller import AnimationController
    from mesh_client.locomotion import LocomotionController

    legs = LocomotionController(MagicMock(), None)
    anim = AnimationController(legs, MagicMock(), cancel=legs.cancel)
    yield legs, anim


def _rig(controllers):
    """Attach a fresh cancel token to the shared controllers.

    Args:
        controllers: The ``(legs, anim)`` pair from the fixture.

    Returns:
        A ``(token, legs, anim)`` tuple sharing that token.
    """
    legs, anim = controllers
    token = CancelToken()
    legs.cancel = anim.cancel = token
    legs.reset_posture()
    return token, legs, anim


def test_a_walk_stops_within_a_servo_frame(controllers):
    token, legs, _ = _rig(controllers)

    # Timed separately: the gait itself aborts within a frame, then the eased
    # recovery runs. Conflating the two hides which one is slow.
    aborted_at = {}
    original = legs.settle_to_neutral
    legs.settle_to_neutral = lambda *a, **k: (
        aborted_at.setdefault("t", time.monotonic()), original(*a, **k))[1]

    threading.Timer(0.3, token.cancel).start()
    started = time.monotonic()
    try:
        with pytest.raises(MotionCancelled):
            legs.move_forward(steps=20, speed=1.0)  # many seconds if uninterrupted
    finally:
        legs.settle_to_neutral = original

    # Granularity is one gait frame, not one gait cycle and not one action.
    assert aborted_at["t"] - started < 0.5
    # And the whole thing, recovery included, is still under a second.
    assert time.monotonic() - started < 1.2


def test_a_cancelled_walk_leaves_the_robot_standing(controllers):
    # A stop mid-cycle can leave a leg mid-swing. The recovery matters more
    # than the stop.
    token, legs, _ = _rig(controllers)
    threading.Timer(0.2, token.cancel).start()
    with pytest.raises(MotionCancelled):
        legs.move_forward(steps=20, speed=1.0)

    neutral = [
        [137.1, 189.4, legs.body_height], [225, 0, legs.body_height],
        [137.1, -189.4, legs.body_height], [-137.1, -189.4, legs.body_height],
        [-225, 0, legs.body_height], [-137.1, 189.4, legs.body_height],
    ]
    assert [list(p) for p in legs.body_points] == neutral


def test_a_cancelled_animation_releases_its_lock(controllers):
    # Otherwise the first cancellation would deadlock every later animation.
    token, _, anim = _rig(controllers)
    threading.Timer(0.2, token.cancel).start()
    with pytest.raises(MotionCancelled):
        anim.palp_wiggle()  # normally runs 16 seconds

    assert not anim.anim_lock.locked()
    assert anim.is_animating is False


def test_an_uninterrupted_walk_still_completes(controllers):
    _, legs, _ = _rig(controllers)
    legs.move_forward(steps=1, speed=3.0)


def test_the_recovery_is_eased_rather_than_snapped(controllers):
    # Commanding neutral in a single write after a mid-cycle abort moves some
    # servos more than 50 degrees at once, which throws the robot about. The
    # settle has to be gentler than the gait it is recovering from.
    token, legs, _ = _rig(controllers)

    frames, mark = [], {}
    original_set, original_settle = legs.set_leg_angles, legs.settle_to_neutral

    def record():
        original_set()
        frames.append(dict(legs.servo.angles) if hasattr(legs.servo, "angles") else {})

    angles = {}
    legs.servo.set_angle.side_effect = lambda ch, a: angles.__setitem__(ch, a)

    def record_angles():
        original_set()
        frames.append(dict(angles))

    def note_settle(*args, **kwargs):
        mark["at"] = len(frames)
        original_settle(*args, **kwargs)

    legs.set_leg_angles = record_angles
    legs.settle_to_neutral = note_settle
    try:
        threading.Timer(0.3, token.cancel).start()
        with pytest.raises(MotionCancelled):
            legs.move_forward(steps=20, speed=1.0)
    finally:
        legs.set_leg_angles, legs.settle_to_neutral = original_set, original_settle

    def worst(seq):
        return max(
            (abs(b[ch] - a[ch]) for a, b in zip(seq, seq[1:]) for ch in b if ch in a),
            default=0.0,
        )

    settle = frames[mark["at"] - 1:]
    assert len(settle) > 5, "the recovery should be interpolated over several frames"
    assert worst(settle) < 15.0, "recovery must not command a large single jump"
