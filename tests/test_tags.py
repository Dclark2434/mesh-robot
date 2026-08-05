"""Tests for the streaming tag parser.

The parser sits between the LLM and the voice, so its failure mode is the
robot reading "[ACTION: wave]" out loud. These cases are the ones that
actually happened.
"""

from mesh_server.expression.tags import MAX_HOLDBACK_CHARS, TagKind, TagStreamParser


def drain(parser: TagStreamParser, *chunks: str):
    """Feed chunks then flush, collecting all speech and tags.

    Args:
        parser: The parser under test.
        *chunks: Text chunks as the LLM would stream them.

    Returns:
        A ``(speech, tags)`` pair covering the whole stream.
    """
    speech, tags = "", []
    for chunk in chunks:
        text, found = parser.feed(chunk)
        speech += text
        tags += found
    text, found = parser.flush()
    return speech + text, tags + found


def test_plain_text_passes_through():
    speech, tags = drain(TagStreamParser(), "Hello there, how are you?")
    assert speech == "Hello there, how are you?"
    assert tags == []


def test_action_is_stripped_from_speech():
    speech, tags = drain(TagStreamParser(), "Hey there [ACTION: wave] good to see you")
    assert "ACTION" not in speech
    assert speech == "Hey there good to see you"
    assert [t.name for t in tags] == ["wave"]


def test_tag_split_across_chunks():
    # The failure this parser exists for: tags arrive in pieces.
    speech, tags = drain(TagStreamParser(), "Hi ", "[ACT", "ION: wa", "ve] there")
    assert speech == "Hi there"
    assert [t.name for t in tags] == ["wave"]


def test_tolerates_missing_space_and_odd_case():
    # Gemini writes it this way often enough that the strict regex was
    # letting tags through to the voice.
    for raw in ("[ACTION:wave]", "[action: Wave]", "[ Action : wave ]"):
        speech, tags = drain(TagStreamParser(), f"hi {raw} there")
        assert [t.name.lower() for t in tags] == ["wave"], raw
        assert "wave]" not in speech, raw


def test_parameter_forms():
    _, tags = drain(TagStreamParser(), "[ACTION: turn_left:9]")
    assert (tags[0].name, tags[0].param) == ("turn_left", "9")

    _, tags = drain(TagStreamParser(), "[ACTION: turn_left=9]")
    assert (tags[0].name, tags[0].param) == ("turn_left", "9")

    _, tags = drain(TagStreamParser(), "[ACTION: wave]")
    assert tags[0].param is None


def test_elevenlabs_audio_tags_reach_the_voice():
    # These are vocal-delivery directives that TTS itself consumes; stripping
    # them would silently remove the expressiveness the voice was chosen for.
    speech, tags = drain(TagStreamParser(), "That's [laughing] ridiculous")
    assert speech == "That's [laughing] ridiculous"
    assert tags == []


def test_memory_tag_is_extracted_and_silent():
    speech, tags = drain(
        TagStreamParser(), "Nice to meet you. [MEMORY: user is called Dustin] So!"
    )
    assert speech == "Nice to meet you. So!"
    assert tags[0].kind is TagKind.MEMORY
    assert tags[0].name == "user is called Dustin"


def test_word_index_anchors_the_gesture():
    _, tags = drain(TagStreamParser(), "one two three [ACTION: wave] four")
    assert tags[0].word_index == 3


def test_word_index_counts_across_chunk_splits():
    # Words split mid-token must not be counted twice.
    _, tags = drain(TagStreamParser(), "hel", "lo the", "re [ACTION: nod]")
    assert tags[0].word_index == 2


def test_partial_tag_is_held_back_not_spoken():
    # Text before the bracket is safe to speak; the bracket itself waits.
    parser = TagStreamParser()
    speech, tags = parser.feed("Hey [ACTION: wa")
    assert speech.strip() == "Hey"
    assert "[" not in speech
    assert tags == []
    speech, tags = parser.feed("ve] there")
    assert speech.strip() == "there"
    assert [t.name for t in tags] == ["wave"]


def test_unclosed_bracket_eventually_releases():
    # A stray "[" in prose must not stall speech forever.
    parser = TagStreamParser()
    speech, _ = parser.feed("[" + "x" * (MAX_HOLDBACK_CHARS + 1))
    assert speech.startswith("[x")


def test_flush_releases_dangling_bracket():
    parser = TagStreamParser()
    assert parser.feed("done [oops")[0].strip() == "done"
    assert parser.flush()[0] == "[oops"


def test_reset_discards_partial_state():
    parser = TagStreamParser()
    parser.feed("Hey [ACTION: wa")
    parser.reset()
    speech, tags = drain(parser, "Fresh start")
    assert speech == "Fresh start"
    assert tags == []
    assert parser.words_emitted == 2


def test_gap_left_by_a_tag_is_collapsed():
    speech, _ = drain(TagStreamParser(), "Hello [ACTION: wave] world")
    assert "  " not in speech


def test_tag_at_the_very_start_anchors_at_zero():
    speech, tags = drain(TagStreamParser(), "[ACTION: wave] Hello!")
    assert tags[0].word_index == 0
    assert speech == "Hello!"


# -- tool calls the model writes out as prose -----------------------------


def test_a_leaked_tool_call_is_never_spoken():
    # Observed in the wild: the robot announced its own function call aloud.
    speech, _ = drain(
        TagStreamParser(),
        "I look! startcall:default_api:look{question:what is on the person's shirt} "
        "It is dark, but I see shapes",
    )
    assert "startcall" not in speech
    assert "default_api" not in speech
    assert "question:" not in speech
    assert "I look!" in speech
    assert "I see shapes" in speech


def test_a_leaked_call_split_across_chunks_is_still_caught():
    # The marker arrives in pieces, so a naive per-chunk check misses it.
    speech, _ = drain(
        TagStreamParser(), "Let me see. start", "call:default_api:look{q:x}", " All done."
    )
    assert "start" not in speech.lower().replace("all done.", "")
    assert "default_api" not in speech
    assert "Let me see." in speech
    assert "All done." in speech


def test_other_leak_spellings_are_caught():
    for leak in (
        "tool_code\nlook(question='x')\n",
        "<start_of_call>default_api.look(q)</start_of_call>",
        "functioncall: look{a:b}",
    ):
        speech, _ = drain(TagStreamParser(), f"Before {leak} after")
        # The call must not survive. Punctuation left behind by a partly
        # matched wrapper is harmless: TTS says nothing for "</".
        assert "look" not in speech.lower(), leak
        assert "question" not in speech.lower(), leak
        assert "Before" in speech and "after" in speech, leak


def test_ordinary_speech_is_not_suppressed():
    # None of these contain a marker, and all are plausible things to say.
    for line in (
        "I will start calling you Friend.",
        "That is the default setting, question?",
        "My tools are legs and a camera.",
        "Start! Go! Now!",
    ):
        speech, _ = drain(TagStreamParser(), line)
        assert speech == line, line


def test_a_leak_does_not_swallow_the_rest_of_the_turn():
    parser = TagStreamParser()
    speech, _ = drain(parser, "startcall:default_api:look{q:x}", "Now I can see!")
    assert "Now I can see!" in speech


def test_an_unterminated_leak_ends_at_a_blank_line():
    speech, _ = drain(
        TagStreamParser(), "hi startcall:default_api:look\n\nstill talking"
    )
    assert "startcall" not in speech
    assert "still talking" in speech


def test_suppression_gives_up_rather_than_eating_a_whole_reply():
    # A marker matched in earnest is followed by a short call. If it runs on,
    # something has gone wrong, and losing a sentence beats losing the turn.
    from mesh_server.expression.tags import MAX_SUPPRESS_CHARS

    speech, _ = drain(
        TagStreamParser(),
        "default_api" + "x" * (MAX_SUPPRESS_CHARS + 50),
        " back again",
    )
    assert "back again" in speech


def test_a_partial_action_tag_is_never_spoken():
    # Observed: an interrupted reply flushed "[ACTION: nod" as prose and the
    # robot read it aloud.
    parser = TagStreamParser()
    speech, _ = drain(parser, "I can see many things with my light-sensor-eye! [ACTION: nod")
    assert "ACTION" not in speech
    assert "nod" not in speech
    assert "light-sensor-eye" in speech


def test_a_partial_memory_tag_is_never_spoken():
    speech, _ = drain(TagStreamParser(), "Nice to meet you. [MEMO")
    assert "MEMO" not in speech
    assert "Nice to meet you." in speech


def test_a_stray_bracket_that_is_not_a_directive_still_gets_spoken():
    # Only openings that look like a directive are dropped; ordinary prose
    # with a bracket in it must survive.
    speech, _ = drain(TagStreamParser(), "the value [x is unknown")
    assert "[x is unknown" in speech


def test_thought_markers_are_suppressed():
    speech, _ = drain(
        TagStreamParser(), "start_thought\nToo dark! I see red shapes and face"
    )
    assert "start_thought" not in speech
    assert "Too dark!" in speech
