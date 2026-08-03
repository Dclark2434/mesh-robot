"""Stopping a movement that is already under way.

Gait cycles and animations are blocking sequences of small servo writes
separated by short sleeps. Nothing about that is wrong, but it means a walk
started three seconds ago cannot be called off: interrupting the robot used to
stop its speech while its legs carried on.

The mechanism is a shared flag plus an interruptible sleep. Every wait inside a
movement goes through :meth:`CancelToken.sleep`, which returns normally on
timeout and raises :class:`MotionCancelled` if the flag was set. That turns
every sleep in a movement into a cancellation point without threading a return
value through several hundred lines of gait maths, and it unwinds through the
``finally`` blocks those routines already have, so the robot still returns to a
stable stance on the way out.

Cancellation therefore lands within one servo frame, roughly 20ms, rather than
at the end of the current action.
"""

from __future__ import annotations

import threading


class MotionCancelled(Exception):
    """Raised inside a movement when it has been asked to stop.

    Callers are expected to let this propagate. Every movement routine already
    restores a safe posture in a ``finally`` block, which is what makes
    unwinding mid-gait safe.
    """


class CancelToken:
    """A cooperative stop signal shared with blocking motion routines."""

    def __init__(self) -> None:
        """Create a token that is not cancelled."""
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        """Whether a stop has been requested."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Ask the running movement to stop at its next sleep."""
        self._event.set()

    def clear(self) -> None:
        """Reset the token so the next movement may run."""
        self._event.clear()

    def checkpoint(self) -> None:
        """Abort here if a stop has been requested.

        For loops that do real work between sleeps, or none at all.

        Raises:
            MotionCancelled: If cancellation is pending.
        """
        if self._event.is_set():
            raise MotionCancelled()

    def sleep(self, seconds: float) -> None:
        """Wait, unless asked to stop first.

        Drop-in replacement for ``time.sleep`` inside movement routines. It
        both paces the motion and provides the cancellation point, so no
        movement needs a separate check.

        Args:
            seconds: How long to wait.

        Raises:
            MotionCancelled: If cancellation is requested, either already
                pending or arriving during the wait.
        """
        if self._event.wait(seconds):
            raise MotionCancelled()
