"""Keeping camera images from accumulating in conversation context.

An image attached to the LLM context is re-sent on every subsequent turn, so a
single look would otherwise tax the whole rest of the conversation. The newest
image keeps its pixels, since follow-up questions about the last thing seen
need them, and older ones collapse into a sentence describing what the robot
looked for and what it said about it.

Free of pipeline imports so the rules can be tested without the real-time
stack installed.
"""

from __future__ import annotations

from typing import Any

from mesh_common.logging import get_logger

logger = get_logger("vision")

#: How many real images stay in context. One keeps follow-up questions about
#: the last thing seen working, without accumulating.
DEFAULT_KEEP_IMAGES = 1


def _is_image_message(message: Any) -> bool:
    """Whether a context message carries an image.

    Args:
        message: A context message, which may be an LLM-specific object rather
            than a plain dict.

    Returns:
        True if the message contains image content.
    """
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)


def _text_of(message: Any) -> str:
    """Extract the plain text of a context message.

    Args:
        message: A context message.

    Returns:
        Concatenated text content, or an empty string.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return ""


def _reply_after(messages: list[Any], index: int) -> str:
    """Find what the robot said about this particular look.

    Scanning stops at the next user message. Without that, a look whose reply
    never arrived would steal the answer belonging to a *later* look, and the
    stand-in would describe the wrong picture.

    Args:
        messages: Full context message list.
        index: Position of the image message.

    Returns:
        The assistant's response to this look, or an empty string if it has
        none.
    """
    for message in messages[index + 1 :]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            return ""  # a new turn began; anything after belongs to it
        if role == "assistant":
            text = _text_of(message)
            if text:
                return text
    return ""


def _stand_in(question: str, reply: str, limit: int = 220) -> str:
    """Compose the text that replaces a purged image.

    Args:
        question: What the robot was looking for.
        reply: What it said about what it saw.
        limit: Maximum length of the retained reply.

    Returns:
        A one-line description of the look that happened.
    """
    purpose = question.strip() or "at the scene"
    if reply:
        if len(reply) > limit:
            reply = reply[: limit - 1].rstrip() + "…"
        return f"[Earlier you looked to see {purpose}, and said: \"{reply}\"]"
    return f"[Earlier you looked to see {purpose}. The image is no longer available.]"


def collapse_old_images(
    messages: list[Any], keep_images: int = DEFAULT_KEEP_IMAGES
) -> list[Any]:
    """Replace all but the newest images with text stand-ins.

    An image left in context is re-sent to the model on every subsequent turn.
    Collapsing old ones keeps the conversation's cost flat instead of growing
    with every look, while preserving what was learned by looking.

    Args:
        messages: Current context messages.
        keep_images: How many of the most recent images keep their pixels.

    Returns:
        A new message list, or the original if nothing needed collapsing.
    """
    positions = [i for i, message in enumerate(messages) if _is_image_message(message)]
    if len(positions) <= keep_images:
        return messages

    doomed = positions[: len(positions) - keep_images] if keep_images else positions
    collapsed = list(messages)
    for index in doomed:
        collapsed[index] = {
            "role": "user",
            "content": _stand_in(_text_of(messages[index]), _reply_after(messages, index)),
        }
    logger.debug(f"Collapsed {len(doomed)} stale image(s) out of context")
    return collapsed
