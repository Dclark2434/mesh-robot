"""Letting the robot look at things.

The robot publishes a low-rate video track continuously, but almost none of
that reaches the model. Frames land in a one-slot buffer here and are
overwritten; the newest one is discarded a fifth of a second later. An image
only reaches Gemini when the model decides to look, by calling the ``look``
tool.

That distinction is the whole design. Streaming frames into the conversation
would cost tokens and latency on *every* turn for a capability that matters
occasionally. Buffering costs a few hundred kilobytes of RAM and nothing else.

**The second cost, and what is done about it.** An image attached to context
is re-sent on every subsequent turn, so one look would otherwise tax the rest
of the conversation forever. After each turn, older images are collapsed into
a short text stand-in built from what the robot was looking for and what it
said about it. The most recent look keeps its real image, so follow-up
questions ("what colour is it?", "is it still there?") still work; everything
before that becomes a sentence.

**Why a tool rather than an action tag.** Action tags are fire-and-forget with
no return path, so the model could ask to look but never see the result in the
same breath. A tool call is a mid-turn round trip: the model asks, the image
lands in context, inference re-runs, and it answers. That costs one extra LLM
round trip, but only on turns where it actually looks.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    UserImageRawFrame,
    UserImageRequestFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams

from mesh_common.logging import get_logger
from mesh_server.vision.ambient import replace_ambient_note
from mesh_server.vision.context import DEFAULT_KEEP_IMAGES, collapse_old_images

logger = get_logger("vision")

#: A frame older than this is not worth showing anyone -- the robot has
#: probably turned since. Better to admit the camera is stalled.
MAX_GLIMPSE_AGE_SECS = 3.0

LOOK_TOOL = FunctionSchema(
    name="look",
    description=(
        "Look through your camera at what is in front of you. Use this whenever "
        "answering depends on seeing something -- what the user is holding, what "
        "is in the room, what a thing looks like. Do not use it for questions you "
        "can already answer."
    ),
    properties={
        "question": {
            "type": "string",
            "description": (
                "What you are trying to find out by looking, in a few words. "
                "For example 'what the user is holding' or 'what colour the mug is'."
            ),
        }
    },
    required=["question"],
)


def vision_tools() -> ToolsSchema:
    """Build the tool schema exposing vision to the model.

    Returns:
        A schema containing just the ``look`` tool.
    """
    return ToolsSchema(standard_tools=[LOOK_TOOL])


@dataclass
class Glimpse:
    """One camera frame, held until the next arrives.

    Attributes:
        image: Raw pixel data.
        size: ``(width, height)``.
        format: Pixel format, as delivered by the transport.
        captured_at: Monotonic time the frame was received.
    """

    image: bytes
    size: tuple[int, int]
    format: str
    captured_at: float

    @property
    def age(self) -> float:
        """Seconds since this frame arrived."""
        return time.monotonic() - self.captured_at


class CameraFeed:
    """The one-slot buffer holding the newest frame from the robot."""

    def __init__(self) -> None:
        """Start with no frame."""
        self._latest: Glimpse | None = None
        self._frames_seen = 0

    def update(self, frame: UserImageRawFrame) -> None:
        """Replace the buffered frame.

        Args:
            frame: A frame just arrived from the robot's camera.
        """
        self._latest = Glimpse(frame.image, frame.size, frame.format, time.monotonic())
        self._frames_seen += 1
        if self._frames_seen == 1:
            logger.info(f"Camera feed live ({frame.size[0]}x{frame.size[1]})")

    def latest(self) -> Glimpse | None:
        """Return the newest frame if it is recent enough to be honest.

        Returns:
            The buffered frame, or None if there is none or it has gone stale.
        """
        if self._latest is None:
            return None
        if self._latest.age > MAX_GLIMPSE_AGE_SECS:
            return None
        return self._latest

    @property
    def has_camera(self) -> bool:
        """Whether any frame has ever arrived."""
        return self._frames_seen > 0


class CameraFeedProcessor(FrameProcessor):
    """Absorbs the video stream, and answers the model's requests to look.

    Sits just after ``transport.input()``. Two jobs:

    * Unsolicited camera frames are swallowed into the buffer rather than
      passed on, so 5fps of images does not travel the length of the pipeline
      to be discarded at the far end.
    * A ``UserImageRequestFrame`` travelling upstream from a ``look`` tool call
      is answered with the buffered frame. Pipecat's assistant aggregator then
      places that image into context *after* the tool result, which is the
      ordering Gemini's function-calling contract requires. Doing this by hand
      is what the LiveKit transport lacks -- unlike the Daily and SmallWebRTC
      transports, it has no ``request_participant_image``.
    """

    def __init__(self, feed: CameraFeed) -> None:
        """Create the processor.

        Args:
            feed: Buffer to fill, and to serve look requests from.
        """
        super().__init__()
        self._feed = feed

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Buffer camera frames and service image requests.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, UserImageRawFrame) and frame.request is None:
            self._feed.update(frame)
            return  # swallow: nothing downstream wants 5fps of stills

        if isinstance(frame, UserImageRequestFrame):
            await self._serve(frame)
            return

        await self.push_frame(frame, direction)

    async def _serve(self, request: UserImageRequestFrame) -> None:
        """Answer a request to look with the buffered frame.

        Args:
            request: The request raised by the ``look`` tool.
        """
        glimpse = self._feed.latest()
        if glimpse is None:
            # The handler already guards this, but a race is possible if the
            # camera drops out between the check and here.
            logger.warning("Look requested but no fresh frame available")
            if request.result_callback:
                await request.result_callback(
                    {"error": "The camera is not sending anything right now."}
                )
            return

        logger.info(f"Look: {request.text or 'no stated purpose'} ({glimpse.age * 1000:.0f}ms old)")
        await self.push_frame(
            UserImageRawFrame(
                user_id=request.user_id,
                image=glimpse.image,
                size=glimpse.size,
                format=glimpse.format,
                text=request.text,
                append_to_context=True,
                request=request,
            ),
            FrameDirection.DOWNSTREAM,
        )


def make_look_handler(feed: CameraFeed):
    """Build the handler for the ``look`` tool.

    Args:
        feed: The camera buffer to look into.

    Returns:
        An async handler suitable for ``llm.register_function("look", ...)``.
    """

    async def look(params: FunctionCallParams) -> None:
        """Request the current camera frame on the model's behalf.

        Deliberately does not call ``result_callback`` on the success path.
        The callback is invoked once the image has been delivered, which is
        what makes the model wait for the picture instead of answering
        without it.

        Args:
            params: Function call parameters supplied by the LLM service.
        """
        question = str(params.arguments.get("question") or "").strip()

        if feed.latest() is None:
            reason = (
                "Your camera has not sent a picture recently."
                if feed.has_camera
                else "You do not have a working camera right now."
            )
            await params.result_callback({"error": reason})
            return

        await params.llm.push_frame(
            UserImageRequestFrame(
                user_id="robot",
                text=question or "Camera view",
                append_to_context=True,
                function_name=params.function_name,
                tool_call_id=params.tool_call_id,
                result_callback=params.result_callback,
            ),
            FrameDirection.UPSTREAM,
        )

    return look


class AmbientVisionProcessor(FrameProcessor):
    """Applies ambient scene notes to context, at a moment that cannot hijack a turn.

    The summarizing call runs in the background as soon as the robot reports it
    has finished travelling. The resulting note is *not* applied immediately --
    it waits for a point where the conversation is between turns, so it can
    never end up as the most recent thing in context. Whatever sits nearest the
    end dominates the reply, and the whole point of this feature is that the
    scenery must not be what the robot replies about.

    Both safe points land before the user's next transcript is aggregated, so
    their actual words always come after the note.
    """

    def __init__(self, ambient, feed: CameraFeed, context) -> None:
        """Create the processor.

        Args:
            ambient: The :class:`~mesh_server.vision.ambient.AmbientVision`
                policy holder.
            feed: Camera buffer to take the observation from.
            context: The shared LLM context to write the note into.
        """
        super().__init__()
        self._ambient = ambient
        self._feed = feed
        self._context = context
        self._pending: str | None = None
        self._task: asyncio.Task | None = None

    def on_moved(self) -> None:
        """Note that the robot has travelled, and look around if it is due.

        Called from the transport's data handler. Returns immediately; the
        summarizing call happens in the background so nothing in the
        conversation waits on it.
        """
        if not self._ambient.should_look():
            return
        glimpse = self._feed.latest()
        if glimpse is None:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._observe(glimpse))

    async def _observe(self, glimpse: Glimpse) -> None:
        """Summarize a frame and hold the result until it is safe to apply.

        Args:
            glimpse: The frame to look at.
        """
        try:
            note = await self._ambient.observe(glimpse.image, glimpse.size, glimpse.format)
        except Exception as exc:
            logger.debug(f"Ambient observation failed: {exc}")
            return
        if note:
            self._pending = note

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Apply a pending note between turns.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if self._pending and isinstance(
            frame, (BotStoppedSpeakingFrame, UserStartedSpeakingFrame)
        ):
            note, self._pending = self._pending, None
            self._context.transform_messages(
                lambda messages: replace_ambient_note(messages, note)
            )

        await self.push_frame(frame, direction)


class VisionContextPruner(FrameProcessor):
    """Collapses stale images out of context at the end of each turn."""

    def __init__(self, context, keep_images: int = DEFAULT_KEEP_IMAGES) -> None:
        """Create the pruner.

        Args:
            context: The shared LLM context to prune.
            keep_images: How many recent images keep their pixels.
        """
        super().__init__()
        self._context = context
        self._keep = keep_images

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Prune once the model has finished its reply.

        Args:
            frame: Incoming frame.
            direction: Direction of travel.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseEndFrame):
            self._context.transform_messages(
                lambda messages: collapse_old_images(messages, self._keep)
            )

        await self.push_frame(frame, direction)
