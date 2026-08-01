"""Executes physical actions the brain sends, on the robot.

Two changes from the previous dispatcher, both about how gestures feel rather
than whether they run.

**Lanes.** Everything used to run on one worker thread, so a nod queued behind
a walk waited four seconds for a leg cycle to finish. Head, body and auxiliary
actions now have a lane each; a glance can happen while the robot walks,
because those are different servos.

**Expiry.** A gesture is tied to a moment in a sentence. If the robot is busy
when it arrives, waving three seconds after the word "hello" is worse than not
waving -- it reads as a fault. Expressive gestures carry a deadline from the
brain and are dropped once it passes. Commands ("walk forward", "lie flat")
carry no deadline, because those are instructions rather than punctuation.

The old ``except TypeError: func()`` parameter guessing is gone. It could not
tell a signature mismatch from a ``TypeError`` raised inside the action itself,
so a hardware fault mid-gesture caused the gesture to run a second time. Every
handler now takes the same single optional parameter.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from mesh_common.logging import get_logger
from mesh_common.protocol import ACTIONS, Lane, resolve_action

logger = get_logger("motion")

#: Handlers all take one optional string parameter, whatever the action.
Handler = Callable[[str | None], None]


@dataclass
class Job:
    """One queued action.

    Attributes:
        action: Canonical action name.
        param: Optional parameter.
        deadline: Monotonic time after which the job is no longer worth doing,
            or None if it should run however late it is.
    """

    action: str
    param: str | None
    deadline: float | None

    def expired(self) -> bool:
        """Whether the job's moment has passed.

        Returns:
            True if the job should be dropped rather than executed.
        """
        return self.deadline is not None and time.monotonic() > self.deadline


class MotionDispatcher:
    """Runs actions on per-lane worker threads."""

    def __init__(self, on_finished: Callable[[str], None] | None = None) -> None:
        """Create the dispatcher with one idle worker per lane.

        Args:
            on_finished: Called with the action name after each action
                completes, on the worker thread. Used to report that the robot
                has finished travelling.
        """
        self._handlers: dict[str, Handler] = {}
        self._queues: dict[Lane, queue.Queue[Job]] = {lane: queue.Queue() for lane in Lane}
        self._workers: list[threading.Thread] = []
        self._stopping = threading.Event()
        self._on_finished = on_finished

    def register(self, action: str, handler: Handler) -> None:
        """Bind an action name to the code that performs it.

        Args:
            action: Name from the shared action registry.
            handler: Callable taking the action's optional parameter.

        Raises:
            KeyError: If the action is not in the shared registry, which means
                the client and the protocol have drifted apart.
        """
        if action not in ACTIONS:
            raise KeyError(f"'{action}' is not a known action")
        self._handlers[action] = handler

    def missing_handlers(self) -> list[str]:
        """List registry actions with no handler on this robot.

        Returns:
            Action names the brain may send but this robot cannot perform.
        """
        return sorted(set(ACTIONS) - set(self._handlers))

    def start(self) -> None:
        """Start one worker thread per lane."""
        self._stopping.clear()
        for lane in Lane:
            worker = threading.Thread(
                target=self._run_lane, args=(lane,), name=f"motion-{lane.value}", daemon=True
            )
            worker.start()
            self._workers.append(worker)
        logger.info(f"Motion dispatcher running ({len(Lane)} lanes)")

    def stop(self) -> None:
        """Signal the workers to finish and wait briefly for them."""
        self._stopping.set()
        for worker in self._workers:
            worker.join(timeout=2.0)
        self._workers.clear()

    def submit(self, action: str, param: str | None, expires_in: float | None) -> None:
        """Queue an action for execution.

        Args:
            action: Action name as received from the brain; aliases are
                resolved here as a defence against protocol drift.
            param: Optional parameter.
            expires_in: Seconds from now after which the action is stale, or
                None to always run it.
        """
        spec = resolve_action(action)
        if spec is None:
            logger.warning(f"Ignoring unknown action '{action}'")
            return
        if spec.name not in self._handlers:
            logger.warning(f"No handler for '{spec.name}' on this robot")
            return

        deadline = time.monotonic() + expires_in if expires_in else None
        self._queues[spec.lane].put(Job(spec.name, param, deadline))

    def _run_lane(self, lane: Lane) -> None:
        """Process one lane's queue until stopped.

        Args:
            lane: The lane this worker owns.
        """
        work = self._queues[lane]
        while not self._stopping.is_set():
            try:
                job = work.get(timeout=0.25)
            except queue.Empty:
                continue

            try:
                if job.expired():
                    logger.info(f"Dropped stale '{job.action}' (robot was busy)")
                    continue
                logger.info(f"[{lane.value}] {job.action}" + (f" {job.param}" if job.param else ""))
                self._handlers[job.action](job.param)
                if self._on_finished is not None:
                    # Reported on completion, not dispatch: a look taken
                    # mid-stride is a picture of the floor going past.
                    self._on_finished(job.action)
            except Exception as exc:
                logger.error(f"'{job.action}' failed: {exc}")
            finally:
                work.task_done()


def steps_from(param: str | None, default: int = 4, maximum: int = 20) -> int:
    """Interpret an action parameter as a step count.

    Args:
        param: Raw parameter text, which the model may write as "5",
            "5 steps", or nothing at all.
        default: Value to use when no number is present.
        maximum: Upper bound. The model will happily ask for a hundred steps.

    Returns:
        A step count within ``1..maximum``.
    """
    if not param:
        return default
    digits = "".join(char for char in str(param) if char.isdigit())
    if not digits:
        return default
    return max(1, min(int(digits), maximum))
