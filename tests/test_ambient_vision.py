"""Tests for ambient scene awareness.

The failure this feature has to avoid is specific: you walk the robot three
steps, say "hey Rocky", and get a paragraph about the room instead of a reply.
These cases pin down the mechanisms that prevent it -- one note at a time,
replaced rather than appended, and suppressed when nothing actually changed.
"""

import asyncio
import time

from mesh_server.vision.ambient import (
    AMBIENT_PREFIX,
    AmbientNote,
    AmbientVision,
    format_note,
    is_ambient_note,
    is_similar,
    replace_ambient_note,
)


def note_message(text: str = "a kitchen") -> dict:
    """Build a context message holding an ambient note.

    Args:
        text: The scene description.

    Returns:
        A context message.
    """
    return {"role": "user", "content": format_note(text)}


def test_a_note_is_recognisable_in_context():
    assert is_ambient_note(note_message())
    assert not is_ambient_note({"role": "user", "content": "hey rocky"})
    assert not is_ambient_note({"role": "assistant", "content": "hello"})


def test_non_dict_messages_are_not_notes():
    assert not is_ambient_note(object())


def test_a_note_says_it_is_only_background():
    # The wording is load-bearing: it is what stops the model treating the
    # scene as something it was asked about.
    text = format_note("a kitchen, someone at the counter")
    assert "Background awareness only" in text
    assert text.startswith(AMBIENT_PREFIX)


def test_a_note_states_its_own_age():
    assert "just now" in format_note("a kitchen", age_secs=0)
    assert "m ago" in format_note("a kitchen", age_secs=200)


def test_notes_replace_rather_than_accumulate():
    # The whole context-cost argument rests on this.
    messages = [{"role": "user", "content": "hello"}, note_message("a kitchen")]
    updated = replace_ambient_note(messages, format_note("a workshop"))
    assert sum(1 for m in updated if is_ambient_note(m)) == 1
    assert "workshop" in updated[-1]["content"]
    assert "kitchen" not in str(updated)


def test_the_note_never_displaces_conversation():
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    updated = replace_ambient_note(messages, format_note("a kitchen"))
    assert updated[:2] == messages


def test_a_note_can_be_cleared_entirely():
    messages = [note_message(), {"role": "user", "content": "hello"}]
    updated = replace_ambient_note(messages, None)
    assert not any(is_ambient_note(m) for m in updated)
    assert len(updated) == 1


def test_the_note_is_appended_last_so_the_user_speaks_after_it():
    # Recency decides what the model replies to. The note is written between
    # turns, so the user's next utterance lands after it.
    messages = [{"role": "user", "content": "hello"}]
    updated = replace_ambient_note(messages, format_note("a kitchen"))
    assert is_ambient_note(updated[-1])


def test_an_unchanged_scene_is_recognised():
    assert is_similar("a small office with two monitors", "a small office with two monitors")
    assert is_similar("small office, two monitors on a desk", "a small office with two monitors")


def test_a_genuinely_new_place_is_not_suppressed():
    assert not is_similar("a kitchen with a kettle", "a garage with a workbench")


def test_the_first_observation_is_never_suppressed():
    assert not is_similar("a kitchen", "")


class FakeSummarizer:
    """Stands in for the one-shot model call."""

    def __init__(self, *replies):
        """Queue the descriptions to return in order.

        Args:
            *replies: Descriptions, or None to simulate an unreadable view.
        """
        self.replies = list(replies)
        self.calls = 0

    async def describe(self, image, size, image_format):
        """Return the next queued description.

        Args:
            image: Ignored.
            size: Ignored.
            image_format: Ignored.

        Returns:
            The next queued description.
        """
        self.calls += 1
        return self.replies.pop(0) if self.replies else None


def observe(vision):
    """Run one observation against a fake frame, synchronously.

    Wrapped in asyncio.run so the suite needs no async plugin.

    Args:
        vision: The AmbientVision under test.

    Returns:
        The note text, or None.
    """
    return asyncio.run(vision.observe(b"", (2, 2), "RGB"))


def test_disabled_ambient_never_looks():
    vision = AmbientVision(None)
    assert not vision.enabled
    assert not vision.should_look()


def test_a_new_scene_produces_a_note():
    vision = AmbientVision(FakeSummarizer("a kitchen with a kettle"))
    note = observe(vision)
    assert note is not None
    assert "kitchen" in note


def test_an_unchanged_scene_produces_no_update():
    # Shuffling around one room must not rewrite the note every time.
    summarizer = FakeSummarizer("a small office, two monitors", "small office with two monitors")
    vision = AmbientVision(summarizer)
    assert observe(vision) is not None
    assert observe(vision) is None


def test_an_unreadable_view_produces_no_update():
    vision = AmbientVision(FakeSummarizer(None))
    assert observe(vision) is None


def test_looking_is_rate_limited():
    vision = AmbientVision(FakeSummarizer("a kitchen", "a garage"))
    assert vision.should_look()
    observe(vision)
    # Immediately after looking, another movement should not spend a call.
    assert not vision.should_look()


def test_a_stale_note_is_forgotten():
    vision = AmbientVision(FakeSummarizer())
    vision._note = AmbientNote("a kitchen", time.monotonic() - 10_000)
    assert vision.current_note() is None


def test_a_fresh_note_is_reported():
    vision = AmbientVision(FakeSummarizer())
    vision._note = AmbientNote("a kitchen", time.monotonic())
    assert "kitchen" in vision.current_note()
