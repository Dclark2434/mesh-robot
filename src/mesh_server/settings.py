"""Typed configuration for the brain.

Replaces the previous module of bare ``os.getenv`` calls evaluated at import
time. Two concrete problems that fixes: ``PIPECAT_VAD_THRESHOLD`` and
``PIPECAT_VAD_CONFIDENCE`` were defined but never read (the real values were
hardcoded in the pipeline), and ``MESH_PERSONALITY`` was baked into a
module-level constant at import, so switching persona meant restarting the
process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PERSONALITIES_DIR = BASE_DIR / "personalities"
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env", override=True)


def _env(name: str, default: str = "") -> str:
    """Read an environment variable as a stripped string.

    Args:
        name: Variable name.
        default: Value to use when unset.

    Returns:
        The value with surrounding whitespace removed.
    """
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    """Read an environment variable as a float, falling back on bad input.

    Args:
        name: Variable name.
        default: Value to use when unset or unparseable.

    Returns:
        The parsed value.
    """
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class TurnSettings:
    """Turn-taking tuning.

    These numbers are inherited from the previous implementation's measured
    work and deliberately preserved. The latency budget behind them: Deepgram's
    p99 transcript latency is ~350ms, and Pipecat subtracts the VAD trailing
    silence from its STT wait, so ``stop_secs`` must stay below that or final
    transcripts land after the turn has already been closed.

    Attributes:
        confidence: Silero speech-probability threshold.
        min_volume: Minimum RMS to consider a frame speech at all. Guards
            against servo whine tripping the VAD.
        stop_secs: Trailing silence before VAD calls the turn over.
        start_secs: Leading speech required before VAD calls it a turn start.
        smart_turn: Use the bundled Smart Turn v3 semantic end-of-turn model on
            top of VAD. It decides from *content* whether the user is finished,
            so a mid-sentence pause no longer triggers a reply.
        min_words: Recognised words required before a user turn starts. Zero
            restores the default of starting on voice activity alone, which in
            a room containing a servo-driven robot means starting on any noise.
        stop_timeout: Seconds before a turn is force-ended when no stop
            strategy fires. Only reached by turns that began without speech in
            them, so the shorter the better.
        summarize_above_tokens: Context size that triggers summarization.
            Deliberately size-based: message-count triggers fire constantly
            when turns fragment into one-word messages.
        wake_phrases: Require one of these before the robot will engage.
            Empty means always listening. Worth setting in a room with a
            television or a small child in it, since a word count cannot
            filter babble -- babble has words in it.
        wake_timeout: Seconds of quiet before the wake phrase is needed again.
            The timer resets on every exchange, so a conversation once started
            continues naturally; it only closes after a real lull.
    """

    confidence: float = 0.6
    min_volume: float = 0.1
    stop_secs: float = 0.2
    start_secs: float = 0.2
    smart_turn: bool = True
    min_words: int = 2
    stop_timeout: float = 2.5
    summarize_above_tokens: int = 8000
    wake_phrases: list[str] = field(default_factory=list)
    wake_timeout: float = 45.0


@dataclass
class AudioSettings:
    """Sample rates for the two directions of the conversation.

    Attributes:
        input_rate: Mic sample rate. 16kHz is what both Silero and the Smart
            Turn model expect, so resampling is avoided by matching it.
        output_rate: TTS sample rate.
        output_chunk_ms: Size of each outbound audio chunk. Smaller chunks mean
            less buffering before the first sound leaves the machine; Pipecat's
            default of 40ms is tuned for congested cloud deployments rather
            than a LAN.
    """

    input_rate: int = 16000
    output_rate: int = 24000
    output_chunk_ms: int = 20


@dataclass
class Settings:
    """Everything the brain needs to start.

    Attributes:
        personality: Which persona prompt to load.
        gemini_api_key: Google API key for the reasoning model.
        gemini_model: Model id.
        deepgram_api_key: Deepgram API key for streaming STT.
        deepgram_model: Deepgram model id.
        elevenlabs_api_key: ElevenLabs API key.
        elevenlabs_voice_id: The chosen custom voice.
        livekit_url: WebSocket URL of the self-hosted LiveKit server.
        livekit_api_key: LiveKit API key.
        livekit_api_secret: LiveKit API secret.
        room_name: Room both processes join.
        identity: This process's participant identity.
        enable_metrics: Collect per-turn latency breakdowns. On by default --
            without it there is no way to tell which stage of the pipeline is
            costing you time.
        dashboard: Serve the read-only web console.
        dashboard_host: Interface to bind it to. Defaults to all interfaces so
            it is reachable from a phone on the same network.
        dashboard_port: Port for the console.
        vision_enabled: Offer the ``look`` tool to the model. Turning this off
            leaves the robot conversational but blind.
        keep_images: How many recent camera images keep their pixels in
            context. Older ones collapse to a text stand-in, because an image
            left in context is re-sent on every subsequent turn.
        ambient_vision: Look around after the robot walks somewhere, and keep
            one sentence about where it is in context.
        ambient_model: Model used for that one-shot scene description. Kept
            separate from the conversation's model so it can be swapped for a
            cheaper one without touching the voice.
        audio: Sample-rate configuration.
        turn: Turn-taking configuration.
    """

    personality: str = field(default_factory=lambda: _env("MESH_PERSONALITY", "mesh"))

    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3-flash-preview"))

    deepgram_api_key: str = field(default_factory=lambda: _env("DEEPGRAM_API_KEY"))
    deepgram_model: str = field(default_factory=lambda: _env("DEEPGRAM_MODEL", "nova-3"))

    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID"))

    livekit_url: str = field(default_factory=lambda: _env("LIVEKIT_URL", "ws://localhost:7880"))
    livekit_api_key: str = field(default_factory=lambda: _env("LIVEKIT_API_KEY"))
    livekit_api_secret: str = field(default_factory=lambda: _env("LIVEKIT_API_SECRET"))
    room_name: str = field(default_factory=lambda: _env("MESH_ROOM", "mesh-robot-room"))
    identity: str = field(default_factory=lambda: _env("MESH_BRAIN_IDENTITY", "mesh-brain"))

    enable_metrics: bool = field(default_factory=lambda: _env("MESH_METRICS", "1") != "0")

    dashboard: bool = field(default_factory=lambda: _env("MESH_DASHBOARD", "1") != "0")
    dashboard_host: str = field(default_factory=lambda: _env("MESH_DASHBOARD_HOST", "0.0.0.0"))
    dashboard_port: int = field(
        default_factory=lambda: int(_env("MESH_DASHBOARD_PORT", "8080") or 8080)
    )

    vision_enabled: bool = field(default_factory=lambda: _env("MESH_VISION", "1") != "0")
    keep_images: int = field(default_factory=lambda: int(_env("MESH_KEEP_IMAGES", "1") or 1))
    ambient_vision: bool = field(default_factory=lambda: _env("MESH_AMBIENT_VISION", "1") != "0")
    ambient_model: str = field(
        default_factory=lambda: _env("MESH_AMBIENT_MODEL")
        or _env("GEMINI_MODEL", "gemini-3-flash-preview")
    )

    audio: AudioSettings = field(default_factory=AudioSettings)
    turn: TurnSettings = field(default_factory=TurnSettings)

    def __post_init__(self) -> None:
        """Apply environment overrides for nested tuning values."""
        self.turn.confidence = _env_float("MESH_VAD_CONFIDENCE", self.turn.confidence)
        self.turn.min_volume = _env_float("MESH_VAD_MIN_VOLUME", self.turn.min_volume)
        self.turn.stop_secs = _env_float("MESH_VAD_STOP_SECS", self.turn.stop_secs)
        self.turn.smart_turn = _env("MESH_SMART_TURN", "1") != "0"
        self.turn.min_words = int(_env("MESH_MIN_WORDS") or self.turn.min_words)
        self.turn.stop_timeout = _env_float("MESH_TURN_STOP_TIMEOUT", self.turn.stop_timeout)
        self.turn.summarize_above_tokens = int(
            _env("MESH_SUMMARIZE_ABOVE_TOKENS") or self.turn.summarize_above_tokens
        )
        self.turn.wake_timeout = _env_float("MESH_WAKE_TIMEOUT", self.turn.wake_timeout)
        phrases = _env("MESH_WAKE_PHRASES")
        self.turn.wake_phrases = [p.strip() for p in phrases.split(",") if p.strip()]

    def missing_credentials(self) -> list[str]:
        """Report which required credentials are absent.

        Returns:
            Names of the environment variables that must be set before the
            pipeline can run, in the order a user should fix them.
        """
        required = {
            "LIVEKIT_API_KEY": self.livekit_api_key,
            "LIVEKIT_API_SECRET": self.livekit_api_secret,
            "DEEPGRAM_API_KEY": self.deepgram_api_key,
            "GEMINI_API_KEY": self.gemini_api_key,
            "ELEVENLABS_API_KEY": self.elevenlabs_api_key,
            "ELEVENLABS_VOICE_ID": self.elevenlabs_voice_id,
        }
        return [name for name, value in required.items() if not value]

    def available_personalities(self) -> list[str]:
        """List the personas that have a prompt file on disk.

        Returns:
            Sorted persona names, excluding the shared rules file.
        """
        return sorted(
            path.stem
            for path in PERSONALITIES_DIR.glob("*.txt")
            if path.stem != "base_rules"
        )

    def load_system_prompt(self, memory_block: str = "") -> str:
        """Assemble the full system prompt for the active persona.

        The persona file supplies character; ``base_rules.txt`` supplies the
        behavioural contract; the action reference is generated from the shared
        protocol registry so the model is never offered a gesture the robot
        cannot perform; and remembered facts are appended last.

        Args:
            memory_block: Rendered long-term memory, or an empty string.

        Returns:
            The complete system instruction.
        """
        from mesh_common.protocol import prompt_action_reference

        persona_path = PERSONALITIES_DIR / f"{self.personality}.txt"
        if not persona_path.exists():
            persona_path = PERSONALITIES_DIR / "mesh.txt"

        parts = [
            persona_path.read_text(encoding="utf-8").strip(),
            (PERSONALITIES_DIR / "base_rules.txt").read_text(encoding="utf-8").strip(),
            "========================\nAVAILABLE ACTIONS\n========================\n"
            + prompt_action_reference(),
        ]
        if memory_block:
            parts.append(memory_block)
        return "\n\n".join(parts)
