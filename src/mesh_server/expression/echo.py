"""Refusing to treat the robot's own voice as a person talking.

With the microphone open while the robot speaks, anything the echo canceller
fails to remove reaches speech recognition. A few of the robot's own words come
back as a transcript, that transcript starts a user turn, the turn broadcasts
an interruption, and the robot cuts itself off. Because it is still speaking
while that happens, the next fragment leaks too, and it stutters through
several turns before the canceller re-converges and the loop starves.

Observed, verbatim, in a real conversation::

    rocky  I record it! You are very curious human! You want to...
    heard  I record it. You want to
    rocky  I record it! You are very dedicated to learning!...
    heard  record it.

The canceller is the real defence and this does not replace it. This is the
backstop for the times it loses convergence, which it will: a buffer glitch on
the Pi is enough.

The test is deliberately narrow. Suppression only applies while the robot is
audibly speaking or immediately after, and only when most of what was heard
appears in what the robot has just said. A person repeating the robot's words
back at it a minute later is a normal thing to do and must still work.
"""

from __future__ import annotations

import re
import time
from collections import deque

#: How long after the robot stops speaking its words can still arrive. Covers
#: the speaker's own tail plus recognition latency.
ECHO_GRACE_SECS = 1.5

#: How far back to compare. Longer than any single utterance, short enough that
#: the robot's vocabulary from a minute ago is not still suppressing speech.
SPOKEN_WINDOW_SECS = 20.0

#: Proportion of the heard words that must appear in what the robot just said.
#: Recognition of an echo is lossy, so an exact match is too strict.
ECHO_OVERLAP = 0.6

#: Below this, a fragment carries too little information to judge. "Yeah" is
#: something both parties say, and dropping a real one costs an answer.
MIN_WORDS_TO_JUDGE = 2

#: At or below this length, every word must match. Two words is enough to start
#: a turn, so echoes that short have to be caught, but a proportional test on
#: two words means one coincidence is a suppression.
EXACT_MATCH_UP_TO = 2


def _words(text: str) -> list[str]:
    """Reduce text to comparable lowercase words.

    Args:
        text: Raw transcript or spoken text.

    Returns:
        Alphanumeric words, punctuation and case discarded.
    """
    return re.findall(r"[a-z0-9']+", text.lower())


class SpokenRecord:
    """What the robot has said recently, and whether it is still saying it.

    Written from two ends of the pipeline: the text as it is handed to speech
    synthesis, and the speaking state observed after the audio has left. Shared
    rather than passed through frames, since the two points are far apart and
    the reader sits upstream of both.
    """

    def __init__(self) -> None:
        """Start with nothing said."""
        self._recent: deque[tuple[float, list[str]]] = deque()
        self._speaking = False
        self._stopped_at = 0.0

    def add(self, text: str) -> None:
        """Record text on its way to the voice.

        Args:
            text: Clean speech, directives already stripped.
        """
        words = _words(text)
        if words:
            self._recent.append((time.monotonic(), words))
        self._trim()

    def set_speaking(self, speaking: bool) -> None:
        """Record whether audio is currently leaving the speaker.

        Args:
            speaking: True when the robot has started, False when it stops.
        """
        if self._speaking and not speaking:
            self._stopped_at = time.monotonic()
        self._speaking = speaking

    def clear(self) -> None:
        """Forget what was said, e.g. when a reply is abandoned."""
        self._recent.clear()

    def _trim(self) -> None:
        """Drop anything past the comparison window."""
        cutoff = time.monotonic() - SPOKEN_WINDOW_SECS
        while self._recent and self._recent[0][0] < cutoff:
            self._recent.popleft()

    @property
    def audible(self) -> bool:
        """Whether the robot's voice could still be reaching the microphone."""
        if self._speaking:
            return True
        return time.monotonic() - self._stopped_at < ECHO_GRACE_SECS

    def is_echo(self, heard: str) -> bool:
        """Whether a transcript is the robot hearing itself.

        Args:
            heard: What speech recognition produced.

        Returns:
            True if this should be discarded rather than treated as speech.
        """
        if not self.audible:
            return False

        words = _words(heard)
        if len(words) < MIN_WORDS_TO_JUDGE:
            # Too short to attribute. Leaving these through is the safer
            # error: a missed echo costs one stutter, a wrongly dropped
            # "yes" costs an answer.
            return False

        self._trim()
        spoken = {word for _, chunk in self._recent for word in chunk}
        if not spoken:
            return False

        matched = sum(1 for word in words if word in spoken)
        if len(words) <= EXACT_MATCH_UP_TO:
            return matched == len(words)
        return matched / len(words) >= ECHO_OVERLAP
