"""Tests for keeping camera images from accumulating in conversation context.

An image left in context is re-sent to the model on every subsequent turn, so
one look would otherwise tax the whole rest of the conversation. These cases
pin down what gets collapsed, what survives, and what is preserved of a look
after its pixels are gone.
"""

from mesh_server.vision import collapse_old_images


def image_message(question: str, url: str = "data:image/jpeg;base64,AAAA") -> dict:
    """Build a context message carrying an image.

    Args:
        question: What the robot was looking for.
        url: Stand-in for the encoded image payload.

    Returns:
        A context message in the shape LLMContext produces.
    """
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


def said(text: str) -> dict:
    """Build an assistant reply message.

    Args:
        text: What the robot said.

    Returns:
        A context message.
    """
    return {"role": "assistant", "content": text}


def images_in(messages) -> int:
    """Count messages still carrying real image data.

    Args:
        messages: Context messages.

    Returns:
        Number of messages containing image content.
    """
    return sum(
        1
        for m in messages
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    )


def test_a_single_look_is_left_alone():
    messages = [image_message("what the user is holding"), said("That's a mug.")]
    assert collapse_old_images(messages, keep_images=1) is messages


def test_older_images_are_collapsed_but_the_newest_survives():
    messages = [
        image_message("what the user is holding"),
        said("That's a mug."),
        image_message("what colour the mug is"),
        said("Blue."),
    ]
    pruned = collapse_old_images(messages, keep_images=1)
    assert images_in(pruned) == 1
    # The survivor is the most recent one.
    assert pruned[2] == messages[2]


def test_a_collapsed_look_keeps_what_was_learned():
    messages = [
        image_message("what the user is holding"),
        said("That's a blue mug with a chip in it."),
        image_message("what is on the table"),
        said("A keyboard."),
    ]
    text = collapse_old_images(messages, keep_images=1)[0]["content"]
    assert isinstance(text, str)
    assert "what the user is holding" in text
    assert "blue mug" in text


def test_collapsed_message_carries_no_image_payload():
    # The whole point: the base64 must actually be gone.
    messages = [
        image_message("first", url="data:image/jpeg;base64,SECRETPAYLOAD"),
        said("ok"),
        image_message("second"),
        said("ok"),
    ]
    assert "SECRETPAYLOAD" not in str(collapse_old_images(messages, keep_images=1))


def test_keep_zero_collapses_everything():
    messages = [image_message("a"), said("x"), image_message("b"), said("y")]
    assert images_in(collapse_old_images(messages, keep_images=0)) == 0


def test_conversation_length_is_preserved():
    # Collapsing must not delete turns; it rewrites them in place, or the
    # conversation stops making sense.
    messages = [image_message("a"), said("x"), image_message("b"), said("y")]
    assert len(collapse_old_images(messages, keep_images=1)) == len(messages)


def test_a_look_with_no_reply_still_collapses():
    messages = [image_message("a"), image_message("b"), said("y")]
    pruned = collapse_old_images(messages, keep_images=1)
    assert images_in(pruned) == 1
    assert "no longer available" in pruned[0]["content"]


def test_long_replies_are_truncated_in_the_stand_in():
    messages = [image_message("a"), said("x" * 900), image_message("b"), said("y")]
    assert len(collapse_old_images(messages, keep_images=1)[0]["content"]) < 400


def test_non_dict_messages_are_left_untouched():
    # The context can hold LLM-specific message objects, not just dicts.
    class Opaque:
        pass

    opaque = Opaque()
    messages = [opaque, image_message("a"), said("x"), image_message("b")]
    pruned = collapse_old_images(messages, keep_images=1)
    assert pruned[0] is opaque


def test_text_only_conversation_is_unchanged():
    messages = [{"role": "user", "content": "hello"}, said("hi")]
    assert collapse_old_images(messages, keep_images=1) is messages


def test_a_reply_is_never_attributed_to_the_wrong_look():
    # Scanning forward for "what it said" must stop at the next turn, or an
    # unanswered look steals the answer belonging to a later one.
    messages = [
        image_message("the first thing"),
        image_message("the second thing"),
        said("It is a keyboard."),
        image_message("the third thing"),
    ]
    pruned = collapse_old_images(messages, keep_images=1)
    assert "keyboard" not in pruned[0]["content"]
    assert "keyboard" in pruned[1]["content"]
