"""Tests for the shared action registry and wire protocol.

The registry's job is to stop the prompt and the robot from drifting apart,
which is what let the old prompt advertise gestures the client could not
perform.
"""

import json

from mesh_common.protocol import (
    ACTION_ALIASES,
    ACTIONS,
    ActionMessage,
    Lane,
    action_keyterms,
    ParamKind,
    Status,
    StatusMessage,
    decode,
    encode,
    prompt_action_reference,
    resolve_action,
)


def test_every_alias_points_at_a_real_action():
    for alias, target in ACTION_ALIASES.items():
        assert target in ACTIONS, f"alias '{alias}' targets missing action '{target}'"


def test_resolve_is_forgiving_about_formatting():
    for written in ("wave", "WAVE", " Wave ", "wave"):
        assert resolve_action(written).name == "wave"


def test_resolve_maps_aliases():
    assert resolve_action("walk").name == "walk_forward"
    assert resolve_action("YES").name == "nod"
    assert resolve_action("lay_flat").name == "reset"


def test_resolve_rejects_unknown_actions():
    assert resolve_action("fly") is None
    assert resolve_action("") is None


def test_locomotion_is_never_speech_safe():
    # Walking mid-sentence makes the robot pace while it talks.
    for name in ("walk_forward", "move_backward", "turn_left", "turn_right"):
        assert not ACTIONS[name].speech_safe


def test_head_gestures_are_quick_enough_to_punctuate_speech():
    for spec in ACTIONS.values():
        if spec.lane is Lane.HEAD:
            assert spec.speech_safe
            assert spec.duration <= 3.0


def test_only_step_actions_take_a_parameter():
    for spec in ACTIONS.values():
        if spec.param is ParamKind.STEPS:
            assert spec.lane is Lane.BODY


def test_prompt_reference_lists_every_action():
    reference = prompt_action_reference()
    for name in ACTIONS:
        assert f"[ACTION: {name}" in reference


def test_action_round_trips_over_the_wire():
    payload = decode(encode(ActionMessage("wave", None, 4.1)))
    assert payload["type"] == "action"
    assert payload["action"] == "wave"
    assert payload["expires_in"] == 4.1


def test_status_round_trips_over_the_wire():
    payload = decode(encode(StatusMessage(Status.THINKING)))
    assert payload["type"] == "status"
    assert Status(payload["status"]) is Status.THINKING


def test_decode_survives_garbage():
    # A malformed packet must not take the robot down mid-conversation.
    assert decode(b"not json") == {}
    assert decode(json.dumps([1, 2, 3])) == {}
    assert decode(b"") == {}


def test_keyterms_cover_the_words_commands_are_made_of():
    # These bias speech recognition. In a noisy room "one step to the left"
    # degrades into "one set to black", and every word that matters there
    # should be on this list.
    terms = set(action_keyterms())
    for word in ("left", "right", "forward", "backward", "turn", "walk", "step", "look"):
        assert word in terms, word


def test_keyterms_are_deduplicated_and_lowercase():
    terms = action_keyterms()
    assert terms == sorted(set(terms))
    assert all(t == t.lower() for t in terms)


def test_keyterms_skip_noise_fragments():
    # Two-letter splinters from action names carry no signal and just dilute
    # the boost list.
    assert all(len(t) > 2 for t in action_keyterms())


def test_telemetry_carries_the_audio_glitch_count():
    # Audio buffer glitches break the alignment echo cancellation depends on,
    # so this is the number that explains a robot answering its own voice.
    from mesh_common.protocol import TelemetryMessage

    payload = decode(encode(TelemetryMessage(battery_percent=80.0, audio_glitches=7)))
    assert payload["audio_glitches"] == 7


def test_telemetry_defaults_to_no_glitches():
    from mesh_common.protocol import TelemetryMessage

    assert decode(encode(TelemetryMessage()))["audio_glitches"] == 0
