"""Tests for refusing to treat the robot's own voice as a person talking.

The loop these prevent, captured verbatim from a real conversation::

    rocky  I record it! You are very curious human! You want to...
    heard  I record it. You want to
    rocky  I record it! You are very dedicated to learning!...
    heard  record it.

The opposite failure matters just as much: a person is allowed to agree with
the robot, repeat it back, or use the same words, and must still be heard.
"""

import time

from mesh_server.expression.echo import (
    ECHO_GRACE_SECS,
    MIN_WORDS_TO_JUDGE,
    SpokenRecord,
)


def speaking(*said: str) -> SpokenRecord:
    """Build a record of a robot mid-sentence.

    Args:
        *said: Text the robot has just handed to the voice.

    Returns:
        A record reporting itself as audible.
    """
    record = SpokenRecord()
    for text in said:
        record.add(text)
    record.set_speaking(True)
    return record


def test_the_observed_loop_is_caught():
    record = speaking("I record it! You are very curious human! You want to see more, question?")
    assert record.is_echo("I record it. You want to")


def test_a_partial_echo_is_caught():
    record = speaking("I am sorry! I talk too much because I am excited!")
    assert record.is_echo("I talk too much because")


def test_a_person_speaking_normally_is_not_suppressed():
    record = speaking("I record it! You are very curious human!")
    for said in (
        "Yeah. What is this book?",
        "Can you turn around?",
        "How is your battery life?",
        "No. Do you know what language it is?",
    ):
        assert not record.is_echo(said), said


def test_nothing_is_suppressed_while_the_robot_is_silent():
    # The whole test only applies while its own voice could be in the room.
    record = speaking("I record it! You are very curious human!")
    record.set_speaking(False)
    record._stopped_at = time.monotonic() - (ECHO_GRACE_SECS + 1)
    assert not record.is_echo("I record it. You want to")


def test_the_grace_window_covers_the_speaker_tail():
    # Recognition lags the audio, so the last words arrive after the robot has
    # stopped.
    record = speaking("I record it! You are very curious human!")
    record.set_speaking(False)
    assert record.is_echo("I record it. You want to")


def test_a_single_word_is_left_alone():
    # "Yeah" is something both parties say. Dropping a real one costs an
    # answer; letting an echoed one through costs one stutter.
    record = speaking("Yeah I hear you Friend")
    assert not record.is_echo("Yeah")
    assert 1 < MIN_WORDS_TO_JUDGE


def test_a_two_word_echo_is_caught_because_two_words_start_a_turn():
    record = speaking("I record it! You are very dedicated student!")
    assert record.is_echo("record it")


def test_a_two_word_barge_in_needs_every_word_to_match():
    # A proportional test on two words makes one coincidence a suppression,
    # so short fragments have to match exactly.
    record = speaking("I record it! You are very dedicated student!")
    assert not record.is_echo("record everything")
    assert not record.is_echo("okay stop")


def test_nothing_said_means_nothing_suppressed():
    record = SpokenRecord()
    record.set_speaking(True)
    assert not record.is_echo("I record it. You want to")


def test_old_speech_stops_matching():
    # Otherwise the robot's vocabulary from a minute ago keeps suppressing the
    # person using the same ordinary words.
    record = speaking("I record it! You are very curious human!")
    record._recent.clear()
    assert not record.is_echo("I record it. You want to")


def test_clearing_forgets_an_abandoned_reply():
    record = speaking("I record it! You are very curious human!")
    record.clear()
    assert not record.is_echo("I record it. You want to")


def test_a_person_quoting_the_robot_later_is_still_heard():
    record = speaking("I see brown fuzzy creature on wood-sheets!")
    record.set_speaking(False)
    record._stopped_at = time.monotonic() - 30
    assert not record.is_echo("You said brown fuzzy creature, that is funny")
