"""The schedule that keeps gestures in time with speech.

Two processors at opposite ends of the pipeline describe the same bot turn: one
reads directives out of the LLM's text before synthesis, the other watches
words leave the speaker. This is what they share.

Kept free of pipeline imports so the timing rules can be tested on their own.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A gesture that has not fired by this long past its cue is dropped. Late
#: gestures read as a malfunction rather than as expression.
GESTURE_GRACE_SECS = 2.5


@dataclass
class PendingGesture:
    """A gesture waiting for its word to be spoken.

    Attributes:
        action: Canonical action name.
        param: Optional parameter.
        word_index: Number of spoken words after which it should fire.
        expires_in: Seconds of validity once dispatched, passed on to the
            robot. None means the action is worth doing however late it
            arrives -- "lie flat" is an instruction, not a flourish.
    """

    action: str
    param: str | None
    word_index: int
    expires_in: float | None


class GestureTimeline:
    """Gestures cued against word positions in the current bot turn."""

    def __init__(self) -> None:
        """Start with no scheduled gestures."""
        self._pending: list[PendingGesture] = []

    def __len__(self) -> int:
        """Number of gestures still waiting to fire."""
        return len(self._pending)

    def schedule(self, gesture: PendingGesture) -> None:
        """Queue a gesture for the current turn.

        Args:
            gesture: The gesture and the word it is anchored to.
        """
        self._pending.append(gesture)

    def due(self, words_spoken: int) -> list[PendingGesture]:
        """Remove and return gestures whose cue has been reached.

        Args:
            words_spoken: Count of words the robot has actually spoken.

        Returns:
            Gestures to dispatch now, in the order they were written.
        """
        ready = [g for g in self._pending if g.word_index <= words_spoken]
        if ready:
            self._pending = [g for g in self._pending if g.word_index > words_spoken]
        return ready

    def drain(self) -> list[PendingGesture]:
        """Remove and return every remaining gesture.

        Used at end of turn, when a gesture anchored past the final word would
        otherwise never fire at all.

        Returns:
            All outstanding gestures, in order.
        """
        remaining, self._pending = self._pending, []
        return remaining

    def clear(self) -> None:
        """Discard outstanding gestures, e.g. after an interruption."""
        self._pending.clear()
