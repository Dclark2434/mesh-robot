"""Pipeline processors that turn the model's words into robot behaviour.

Three processors, sitting at three different points in the pipeline:

``ActionTagProcessor``
    Between the LLM and TTS. Strips directives out of the text stream so the
    voice never reads a tag aloud, records each gesture against the word it
    followed, and persists ``[MEMORY: ...]`` facts.

``GestureDispatcher``
    *After* the output transport. This placement is the point of the design.
    Pipecat's output transport holds any frame carrying a presentation
    timestamp until its moment arrives, and ElevenLabs' websocket returns
    character alignment which Pipecat converts into word-level ``TTSTextFrame``
    with exactly such a timestamp. Counting those frames downstream therefore
    counts *words actually being spoken*, so a gesture fires with its word
    rather than at LLM-token time, roughly 1.5s earlier, given measured
    ElevenLabs time-to-first-byte.

``StatusProcessor``
    Near the head of the pipeline, mirroring conversational state onto the
    robot's LEDs.
"""

from __future__ import annotations

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from mesh_common.logging import get_logger
from mesh_common.protocol import (
    ActionMessage,
    InterruptMessage,
    ParamKind,
    Status,
    StatusMessage,
    encode,
    resolve_action,
)
from mesh_server.dashboard.events import bus
from mesh_server.expression.tags import TagKind, TagStreamParser
from mesh_server.expression.timeline import (
    GESTURE_GRACE_SECS,
    GestureTimeline,
    PendingGesture,
)
from mesh_server.memory import MemoryStore

logger = get_logger("expression")


class ActionTagProcessor(FrameProcessor):
    """Strips inline directives from the LLM stream before it reaches TTS."""

    def __init__(self, timeline: GestureTimeline, memory: MemoryStore) -> None:
        """Create the processor.

        Args:
            timeline: Shared schedule written here and read by the dispatcher.
            memory: Store that ``[MEMORY: ...]`` directives are written to.
        """
        super().__init__()
        self._timeline = timeline
        self._memory = memory
        self._parser = TagStreamParser()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Rewrite text frames and pass everything else through.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            await self._handle_text(frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            # A fresh response: drop any half-parsed tag from a previous turn.
            self._parser.reset()
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self._emit(self._parser.flush(), direction)
            self._parser.reset()
        elif isinstance(frame, InterruptionFrame):
            self._parser.reset()
            self._timeline.clear()

        await self.push_frame(frame, direction)

    async def _handle_text(self, frame: LLMTextFrame, direction: FrameDirection) -> None:
        """Parse one streamed text chunk.

        Args:
            frame: The text frame from the LLM.
            direction: Direction of travel.
        """
        await self._emit(self._parser.feed(frame.text), direction)

    async def _emit(self, parsed: tuple[str, list], direction: FrameDirection) -> None:
        """Forward clean speech and act on any completed directives.

        Args:
            parsed: The ``(speech, tags)`` pair from the parser.
            direction: Direction of travel.
        """
        speech, tags = parsed

        for tag in tags:
            if tag.kind is TagKind.MEMORY:
                if self._memory.remember(tag.name):
                    bus.publish("memory", fact=tag.name)
                continue
            self._schedule_action(tag.name, tag.param, tag.word_index)

        if speech:
            await self.push_frame(LLMTextFrame(speech), direction)

    def _schedule_action(self, name: str, param: str | None, word_index: int) -> None:
        """Validate an action tag and add it to the timeline.

        Args:
            name: Action name as written by the model.
            param: Optional parameter text.
            word_index: Word position the tag followed.
        """
        spec = resolve_action(name)
        if spec is None:
            logger.warning(f"Model asked for unknown action '{name}'; ignoring")
            return

        value = param if spec.param is ParamKind.STEPS else None
        # Expressive gestures are only worth doing near their moment. Commands
        # (walk, lie flat) are worth doing whenever the robot gets to them.
        expires = spec.duration + GESTURE_GRACE_SECS if spec.speech_safe else None
        self._timeline.schedule(PendingGesture(spec.name, value, word_index, expires))
        logger.debug(f"Gesture '{spec.name}' cued at word {word_index}")


class GestureDispatcher(FrameProcessor):
    """Fires cued gestures in time with the words being spoken.

    Placed downstream of ``transport.output()`` so the ``TTSTextFrame`` frames
    it counts have already been delayed to their presentation time.
    """

    def __init__(self, timeline: GestureTimeline, send) -> None:
        """Create the dispatcher.

        Args:
            timeline: Shared schedule written by :class:`ActionTagProcessor`.
            send: Coroutine function taking one JSON string, used to put a
                message on the data channel.
        """
        super().__init__()
        self._timeline = timeline
        self._send = send
        self._words_spoken = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Track spoken words and release gestures as their cues arrive.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSTextFrame):
            self._words_spoken += 1
            await self._fire(self._timeline.due(self._words_spoken))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            # Anything anchored past the final word fires now rather than never.
            await self._fire(self._timeline.drain())
            self._words_spoken = 0
        elif isinstance(frame, InterruptionFrame):
            self._timeline.clear()
            self._words_spoken = 0

        await self.push_frame(frame, direction)

    async def _fire(self, gestures: list[PendingGesture]) -> None:
        """Send gestures to the robot.

        Args:
            gestures: Gestures whose moment has come.
        """
        for gesture in gestures:
            message = ActionMessage(
                action=gesture.action,
                param=gesture.param,
                expires_in=gesture.expires_in,
            )
            logger.info(f"-> {gesture.action}" + (f" ({gesture.param})" if gesture.param else ""))
            bus.publish("gesture", action=gesture.action, param=gesture.param)
            await self._send(encode(message))


class ListeningStatusProcessor(FrameProcessor):
    """Publishes the listening and thinking states from the input side."""

    _TRANSITIONS = {
        UserStartedSpeakingFrame: Status.LISTENING,
        UserStoppedSpeakingFrame: Status.THINKING,
    }

    def __init__(self, send) -> None:
        """Create the processor.

        Args:
            send: Coroutine function taking one JSON string, used to put a
                message on the data channel.
        """
        super().__init__()
        self._send = send
        self._current: Status | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Emit a status message when the conversational state changes.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        status = self._TRANSITIONS.get(type(frame))
        if status is not None and status is not self._current:
            self._current = status
            await self._send(encode(StatusMessage(status)))

        await self.push_frame(frame, direction)


class SpeakingStatusProcessor(FrameProcessor):
    """Publishes the speaking state from the far end of the pipeline.

    Sits with :class:`GestureDispatcher` downstream of the output transport, so
    "I am talking now" reaches the LEDs when sound actually starts rather than
    when synthesis was requested.
    """

    def __init__(self, send) -> None:
        """Create the processor.

        Args:
            send: Coroutine function taking one JSON string.
        """
        super().__init__()
        self._send = send
        self._speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Publish speaking and idle transitions.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSTextFrame) and not self._speaking:
            self._speaking = True
            await self._send(encode(StatusMessage(Status.SPEAKING)))
        elif isinstance(frame, InterruptionFrame):
            # Tell the robot to bin its queued audio. A plain idle status will
            # not do: that also arrives at the normal end of a turn, when the
            # remaining audio is the tail of a sentence and must be played.
            self._speaking = False
            await self._send(encode(InterruptMessage()))
            await self._send(encode(StatusMessage(Status.LISTENING)))
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._speaking:
            self._speaking = False
            await self._send(encode(StatusMessage(Status.IDLE)))

        await self.push_frame(frame, direction)
