"""Tests for gesture-to-speech timing.

The timeline is what makes a wave land on the word "hello" instead of a
second and a half before it, so these cases describe the intended feel.
"""

from mesh_server.expression.timeline import GestureTimeline, PendingGesture


def gesture(action: str, word_index: int) -> PendingGesture:
    """Build a gesture cued at a word position.

    Args:
        action: Action name.
        word_index: Word after which it should fire.

    Returns:
        A pending gesture with a typical expressive deadline.
    """
    return PendingGesture(action, None, word_index, 4.0)


def test_nothing_fires_before_its_word():
    timeline = GestureTimeline()
    timeline.schedule(gesture("wave", 3))
    assert timeline.due(0) == []
    assert timeline.due(2) == []


def test_gesture_fires_on_its_word():
    timeline = GestureTimeline()
    timeline.schedule(gesture("wave", 3))
    assert [g.action for g in timeline.due(3)] == ["wave"]


def test_a_gesture_fires_only_once():
    timeline = GestureTimeline()
    timeline.schedule(gesture("wave", 1))
    assert timeline.due(1)
    assert timeline.due(5) == []


def test_gestures_keep_the_order_they_were_written():
    timeline = GestureTimeline()
    timeline.schedule(gesture("look_left", 2))
    timeline.schedule(gesture("look_right", 2))
    assert [g.action for g in timeline.due(4)] == ["look_left", "look_right"]


def test_a_gesture_at_index_zero_fires_at_the_first_word():
    timeline = GestureTimeline()
    timeline.schedule(gesture("wave", 0))
    assert [g.action for g in timeline.due(1)] == ["wave"]


def test_trailing_gestures_fire_at_end_of_turn():
    # A tag after the final word would otherwise never come due, because no
    # further spoken words arrive to trigger it.
    timeline = GestureTimeline()
    timeline.schedule(gesture("bow", 99))
    assert timeline.due(10) == []
    assert [g.action for g in timeline.drain()] == ["bow"]
    assert len(timeline) == 0


def test_interruption_discards_everything_pending():
    timeline = GestureTimeline()
    timeline.schedule(gesture("wave", 1))
    timeline.schedule(gesture("bow", 9))
    timeline.clear()
    assert timeline.due(100) == []
    assert timeline.drain() == []
