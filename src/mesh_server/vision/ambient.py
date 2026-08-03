"""Letting the robot notice that its surroundings changed.

When the robot walks somewhere, it should have some idea that it is somewhere
new. The naive way to do that, attaching a camera frame to the conversation
after every move, fails badly in practice. Three things conspire:

* **Images are salient.** Put a picture in front of a model and it will talk
  about the picture.
* **Role framing.** An image attached as a user message reads as "the human
  showed me this", which implies it wants a response.
* **Recency.** Whatever sits nearest the end of the context dominates the
  reply.

So you say "hey Rocky" and get a paragraph about the kitchen. Prompt rules
alone fight all three head-on and lose often enough to be irritating.

Instead, the frame never enters the conversation. It goes to a cheap one-shot
model call which returns a single factual sentence, and only that sentence is
injected, written as machine-generated state rather than as something you
said, and injected *before* your next utterance so it is never the most recent
thing in context. The prompt rule then has an easy job, because it is no longer
arguing with the format.

The same design answers context cost. One note exists at a time and is replaced
in place, so ambient awareness is a fixed ~20 tokens forever rather than several
hundred re-sent every turn.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from mesh_common.logging import get_logger

logger = get_logger("ambient")

#: Prefix marking a context message as an ambient note, so the previous one can
#: be found and replaced rather than accumulating.
AMBIENT_PREFIX = "[ambient"

#: What the summarizing model is asked for. Deliberately flat and factual: a
#: description with any colour in it invites the conversational model to riff
#: on it, which is the behaviour this whole module exists to prevent.
SUMMARY_PROMPT = (
    "Describe this robot's-eye view in ONE short factual clause, under 15 words. "
    "Name the kind of place and the one or two most notable things in it. "
    "No adjectives beyond what identifies something, no speculation, no "
    "commentary, no greeting. If the view is too dark or blurred to read, "
    "reply exactly: unclear."
)

#: Above this Jaccard overlap with the previous note, the scene is treated as
#: unchanged and the update is dropped. Shuffling around a desk should not
#: produce a new note every time.
SIMILARITY_THRESHOLD = 0.6

#: A note older than this is dropped rather than left to mislead. The robot
#: should not reason about a room it wandered out of ten minutes ago.
NOTE_LIFETIME_SECS = 300.0

#: Ignore movement events that arrive faster than this.
MIN_INTERVAL_SECS = 20.0


def _words(text: str) -> set[str]:
    """Reduce a description to a bag of comparable words.

    Args:
        text: A scene description.

    Returns:
        Lowercased alphanumeric words, punctuation discarded.
    """
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def is_similar(new: str, previous: str, threshold: float = SIMILARITY_THRESHOLD) -> bool:
    """Whether two scene descriptions say substantially the same thing.

    Args:
        new: The description just produced.
        previous: The description already in context.
        threshold: Jaccard overlap above which the scene counts as unchanged.

    Returns:
        True if the new description is not worth replacing the old one with.
    """
    if not previous:
        return False
    a, b = _words(new), _words(previous)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


def format_note(description: str, age_secs: float = 0.0) -> str:
    """Render a scene description as a context note.

    The wording matters. It is deliberately clipped and machine-like so it
    reads as instrumentation rather than as conversation, and it states its own
    age so the model can discount it.

    Args:
        description: One-clause scene description.
        age_secs: How long ago the observation was made.

    Returns:
        The text to place in context.
    """
    when = "just now" if age_secs < 45 else f"{int(age_secs // 60) or 1}m ago"
    return (
        f"{AMBIENT_PREFIX}, {when}] Your camera shows: {description.strip().rstrip('.')}. "
        "Background awareness only. Do not mention this unless it is directly "
        "relevant to what is being discussed."
    )


def is_ambient_note(message: Any) -> bool:
    """Whether a context message is one of these notes.

    Args:
        message: A context message.

    Returns:
        True if the message is an ambient note.
    """
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    return isinstance(content, str) and content.startswith(AMBIENT_PREFIX)


def replace_ambient_note(messages: list[Any], note: str | None) -> list[Any]:
    """Swap the ambient note in a context message list.

    Exactly one note exists at a time. Old ones are removed rather than left
    behind, which is what keeps the cost of ambient awareness flat instead of
    growing with every room the robot walks into.

    Args:
        messages: Current context messages.
        note: The new note, or None to simply clear any existing one.

    Returns:
        A new message list with at most one ambient note, at the end.
    """
    kept = [m for m in messages if not is_ambient_note(m)]
    if note:
        kept.append({"role": "user", "content": note})
    return kept


@dataclass
class AmbientNote:
    """The robot's current sense of where it is.

    Attributes:
        description: One-clause scene description.
        observed_at: Monotonic time the frame was taken.
    """

    description: str
    observed_at: float

    @property
    def age(self) -> float:
        """Seconds since the observation."""
        return time.monotonic() - self.observed_at

    @property
    def expired(self) -> bool:
        """Whether the note is too old to be trusted."""
        return self.age > NOTE_LIFETIME_SECS


class SceneSummarizer:
    """Turns a camera frame into one sentence, using a cheap one-shot call.

    Deliberately separate from the conversation's own model. This call is not
    on the critical path (it happens while the robot is still finishing its
    walk) and its output is text, so it never costs the conversation an
    image.
    """

    def __init__(self, api_key: str, model: str) -> None:
        """Create the summarizer.

        Args:
            api_key: Google API key.
            model: Model id to use for the one-shot description.
        """
        self._model = model
        self._client: Any | None = None
        self._api_key = api_key

    def _ensure_client(self) -> Any:
        """Create the genai client on first use.

        Returns:
            A configured client.
        """
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def describe(self, image: bytes, size: tuple[int, int], image_format: str) -> str | None:
        """Describe a camera frame in one clause.

        Args:
            image: Raw pixel data.
            size: ``(width, height)``.
            image_format: Pixel format, e.g. ``"RGB"``.

        Returns:
            A short description, or None if the view was unreadable or the call
            failed. Failure is not worth interrupting a conversation over.
        """
        try:
            jpeg = await self._encode(image, size, image_format)
            from google.genai import types

            response = await self._ensure_client().aio.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                    SUMMARY_PROMPT,
                ],
            )
            text = (response.text or "").strip()
        except Exception as exc:
            logger.debug(f"Scene summary failed: {exc}")
            return None

        if not text or text.lower().startswith("unclear"):
            return None
        return text.splitlines()[0][:120]

    @staticmethod
    async def _encode(image: bytes, size: tuple[int, int], image_format: str) -> bytes:
        """Compress a raw frame to JPEG off the event loop.

        Args:
            image: Raw pixel data.
            size: ``(width, height)``.
            image_format: Pixel format as delivered by the transport.

        Returns:
            JPEG bytes.
        """
        import asyncio
        import io

        from PIL import Image

        def encode() -> bytes:
            buffer = io.BytesIO()
            Image.frombytes(image_format, size, image).save(buffer, format="JPEG", quality=70)
            return buffer.getvalue()

        return await asyncio.to_thread(encode)


class AmbientVision:
    """Decides when to look around, and holds the resulting note.

    Owns the policy (rate limiting, change detection, expiry) separately
    from the pipeline plumbing that applies it, so the rules can be tested
    without a running pipeline.
    """

    def __init__(self, summarizer: SceneSummarizer | None) -> None:
        """Create the tracker.

        Args:
            summarizer: The scene summarizer, or None to disable ambient
                awareness entirely.
        """
        self._summarizer = summarizer
        self._note: AmbientNote | None = None
        self._last_attempt = 0.0

    @property
    def enabled(self) -> bool:
        """Whether ambient awareness is switched on."""
        return self._summarizer is not None

    def should_look(self) -> bool:
        """Whether a movement event is worth spending a look on.

        Returns:
            True if enough time has passed since the last attempt.
        """
        if not self.enabled:
            return False
        return time.monotonic() - self._last_attempt >= MIN_INTERVAL_SECS

    async def observe(self, image: bytes, size: tuple[int, int], image_format: str) -> str | None:
        """Look at a frame and update the note if the scene actually changed.

        Args:
            image: Raw pixel data.
            size: ``(width, height)``.
            image_format: Pixel format.

        Returns:
            The new note text to place in context, or None if nothing should
            change, because the view was unreadable, or because the robot is
            looking at the same room it was already in.
        """
        if self._summarizer is None:
            return None

        self._last_attempt = time.monotonic()
        description = await self._summarizer.describe(image, size, image_format)
        if description is None:
            return None

        previous = self._note.description if self._note and not self._note.expired else ""
        if is_similar(description, previous):
            logger.debug(f"Scene unchanged, keeping existing note: {description}")
            self._note = AmbientNote(previous or description, time.monotonic())
            return None

        self._note = AmbientNote(description, time.monotonic())
        logger.info(f"Noticed: {description}")
        return format_note(description)

    def current_note(self) -> str | None:
        """The note that should currently be in context.

        Returns:
            The note text, or None if there is none or it has expired.
        """
        if self._note is None:
            return None
        if self._note.expired:
            self._note = None
            return None
        return format_note(self._note.description, self._note.age)

    def clear(self) -> None:
        """Forget where the robot is."""
        self._note = None
