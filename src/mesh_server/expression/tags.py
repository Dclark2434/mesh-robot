"""Streaming parser for the inline tag language the LLM speaks.

The model writes ordinary prose with directives embedded in it::

    Hey there! [ACTION: wave] Good to see you. [MEMORY: user is called Dustin]

Those directives must never reach the TTS voice, and they arrive split across
streaming chunks -- ``[ACT``, ``ION: wa``, ``ve] Good`` is a perfectly normal
sequence. This module reassembles them.

Three things it does that the previous implementation did not:

* **Tolerates how models actually write.** ``[ACTION:wave]``, ``[action: Wave]``
  and ``[ACTION: turn_left=9]`` all parse. The old exact-match regex silently
  passed those through to TTS, so the robot read the tag aloud.
* **Leaves ElevenLabs audio tags alone.** ``[laughing]`` and ``[sighs]`` are
  vocal-delivery directives that TTS itself consumes, so bracketed text that
  isn't a known directive is emitted verbatim.
* **Records where in the sentence each tag sat.** Each tag carries the number
  of words of clean speech that precede it, which is what lets gestures be
  fired against the spoken word rather than against LLM token arrival.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: Longest run of text we will hold back waiting for a ``]``. A stray open
#: bracket in prose must not stall speech forever.
MAX_HOLDBACK_CHARS = 96

#: ``[ ACTION : name : param ]`` with everything optional that can be.
_DIRECTIVE = re.compile(
    r"""\[\s*
        (?P<kind>ACTION|MEMORY)\s*
        [:=]?\s*
        (?P<body>[^\]]*)
        \]""",
    re.IGNORECASE | re.VERBOSE,
)


class TagKind(str, Enum):
    """Directive families understood by the parser."""

    ACTION = "action"
    MEMORY = "memory"


@dataclass(frozen=True)
class Tag:
    """One directive extracted from the model's output.

    Attributes:
        kind: Whether this is an action or a memory directive.
        name: For actions, the action name. For memories, the full text.
        param: Optional parameter for actions (``turn_left:9`` -> ``"9"``).
        word_index: Number of words of clean speech emitted before this tag
            appeared. A tag at the very start of a turn has index 0.
    """

    kind: TagKind
    name: str
    param: str | None
    word_index: int


class TagStreamParser:
    """Incremental parser turning an LLM text stream into speech plus tags.

    Feed it chunks as they arrive; it returns the text that is safe to speak
    now, plus any directives that completed. Text is held back only while it
    might still turn out to be part of a directive.
    """

    def __init__(self) -> None:
        """Initialize an empty parser."""
        self._buffer = ""
        self._words = 0
        self._at_word_boundary = True

    @property
    def words_emitted(self) -> int:
        """Number of whitespace-delimited words emitted as clean speech so far."""
        return self._words

    def reset(self) -> None:
        """Discard all state.

        Called between bot turns and on interruption, so a half-finished tag
        from an abandoned response cannot leak into the next one.
        """
        self._buffer = ""
        self._words = 0
        self._at_word_boundary = True

    def feed(self, chunk: str) -> tuple[str, list[Tag]]:
        """Consume one streamed chunk of model output.

        Args:
            chunk: Raw text as emitted by the LLM.

        Returns:
            A ``(speech, tags)`` pair. ``speech`` is text safe to forward to
            TTS now, with directives removed; it may be empty when the chunk
            ended mid-directive. ``tags`` are directives that completed within
            this chunk.
        """
        self._buffer += chunk
        return self._drain(final=False)

    def flush(self) -> tuple[str, list[Tag]]:
        """Emit everything still buffered at the end of a turn.

        Any unterminated ``[`` is treated as ordinary prose, since no further
        text is coming to close it.

        Returns:
            A ``(speech, tags)`` pair, as for :meth:`feed`.
        """
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> tuple[str, list[Tag]]:
        """Pull every resolvable piece out of the buffer.

        Args:
            final: Whether the stream has ended, in which case a dangling
                open bracket is released as prose rather than held.

        Returns:
            A ``(speech, tags)`` pair.
        """
        out: list[str] = []
        tags: list[Tag] = []

        while self._buffer:
            open_at = self._buffer.find("[")

            if open_at == -1:
                out.append(self._take(len(self._buffer)))
                break

            if open_at > 0:
                # Everything before the bracket is unambiguously speech.
                out.append(self._take(open_at))
                continue

            close_at = self._buffer.find("]")
            if close_at == -1:
                # Incomplete. Hold, unless it has grown past anything a
                # directive could plausibly be -- or the stream has ended.
                if final or len(self._buffer) > MAX_HOLDBACK_CHARS:
                    out.append(self._take(len(self._buffer)))
                break

            candidate = self._buffer[: close_at + 1]
            match = _DIRECTIVE.fullmatch(candidate)
            if match:
                tags.append(self._build_tag(match))
                self._buffer = self._buffer[close_at + 1 :]
            else:
                # Not ours -- an ElevenLabs audio tag such as [laughing].
                # Pass it through untouched for TTS to interpret.
                out.append(self._take(close_at + 1))

        return "".join(out), tags

    def _build_tag(self, match: re.Match[str]) -> Tag:
        """Construct a Tag from a matched directive.

        Args:
            match: A successful match of :data:`_DIRECTIVE`.

        Returns:
            The parsed tag, anchored at the current word position.
        """
        kind = TagKind(match.group("kind").lower())
        body = match.group("body").strip()

        if kind is TagKind.MEMORY:
            return Tag(kind, body, None, self._words)

        # Actions may carry a parameter as "name:param" or "name=param".
        name, param = _split_param(body)
        return Tag(kind, name, param, self._words)

    def _take(self, count: int) -> str:
        """Remove the first ``count`` characters from the buffer as speech.

        Collapses the double spaces left behind by a removed directive, and
        keeps the running word count in step.

        Args:
            count: Number of characters to consume.

        Returns:
            The text to speak, possibly empty after whitespace collapsing.
        """
        piece, self._buffer = self._buffer[:count], self._buffer[count:]

        if self._at_word_boundary:
            piece = piece.lstrip()

        cleaned: list[str] = []
        for char in piece:
            is_space = char.isspace()
            if is_space and self._at_word_boundary:
                continue  # collapse runs, including the gap a tag left behind
            if not is_space and self._at_word_boundary:
                self._words += 1
            self._at_word_boundary = is_space
            cleaned.append(char)

        return "".join(cleaned)


def _split_param(body: str) -> tuple[str, str | None]:
    """Split an action body into its name and optional parameter.

    Args:
        body: Text between the directive keyword and the closing bracket,
            e.g. ``"turn_left:9"`` or ``"wave"``.

    Returns:
        A ``(name, param)`` pair; ``param`` is None when absent or blank.
    """
    for sep in (":", "="):
        if sep in body:
            name, _, param = body.partition(sep)
            param = param.strip()
            return name.strip(), param or None
    return body.strip(), None
