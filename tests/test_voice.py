"""Tests for choosing a synthesis transport to match the voice model.

ElevenLabs does not serve the v3 family over its websocket streaming
endpoint. Asking for it there returns no audio and no error, and because
synthesis sits near the end of the pipeline, everything queued behind it
stalls too: no greeting, no reply, and nothing in the log to say why. The
transport therefore has to follow the model rather than being chosen freely.
"""

import os

import pytest

from mesh_server.settings import PROMPT_FRAGMENTS, Settings


@pytest.fixture
def voice(monkeypatch):
    """Build Settings for a named voice model.

    Args:
        monkeypatch: pytest environment patcher.

    Yields:
        A callable taking a model name and returning Settings.
    """

    def _for(model: str) -> Settings:
        monkeypatch.setitem(os.environ, "ELEVENLABS_MODEL", model)
        return Settings()

    yield _for


def test_a_v3_voice_goes_over_http(voice):
    assert voice("eleven_v3").elevenlabs_needs_http


def test_a_turbo_voice_stays_on_the_websocket(voice):
    # The websocket is the better path where it works: one persistent
    # connection, lower time to first byte.
    assert not voice("eleven_turbo_v2_5").elevenlabs_needs_http
    assert not voice("eleven_flash_v2_5").elevenlabs_needs_http


def test_the_transport_and_the_audio_tags_move_together(voice):
    # Both follow from the model being v3, and a mismatch is a silent failure
    # in one direction or spoken "[laughing]" in the other.
    for model in ("eleven_v3", "eleven_turbo_v2_5", "eleven_flash_v2_5"):
        settings = voice(model)
        assert settings.elevenlabs_needs_http == settings.audio_tags_supported, model


def test_voice_character_is_left_alone_unless_asked(voice, monkeypatch):
    # Unset must mean "whatever the voice is configured with in ElevenLabs",
    # not a default that quietly overrides it.
    for name in ("ELEVENLABS_STABILITY", "ELEVENLABS_STYLE", "ELEVENLABS_SPEED"):
        monkeypatch.delitem(os.environ, name, raising=False)
    settings = voice("eleven_v3")
    assert settings.elevenlabs_stability is None
    assert settings.elevenlabs_style is None
    assert settings.elevenlabs_speed is None


def test_prompt_fragments_are_not_offered_as_characters(voice):
    # audio_tags.txt sits in the personalities directory because it is appended
    # to whichever persona is active; selecting it would load a note about
    # vocal delivery as the entire character.
    personas = voice("eleven_v3").available_personalities()
    assert not PROMPT_FRAGMENTS & set(personas)
    assert "rocky" in personas
