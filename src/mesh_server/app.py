"""The brain: a persistent real-time conversation pipeline.

Runs on the workstation, joins the same LiveKit room as the robot, and holds
one continuous session: the robot's microphone in, synthesized speech and
gesture instructions out.

Pipeline order, and why:

1. ``transport.input()`` -- audio from the robot's mic.
2. ``ListeningStatusProcessor`` -- LEDs follow the user's turn.
3. ``stt`` -- Deepgram streaming transcription.
4. ``context.user()`` -- owns voice activity detection, end-of-turn decisions
   and mic muting, and accumulates the user's turn.
5. ``llm`` -- Gemini over the standard text API.
6. ``ActionTagProcessor`` -- directives out of the text, gestures onto the
   timeline.
7. ``tts`` -- ElevenLabs streaming synthesis.
8. ``transport.output()`` -- audio to the robot, and the clock that gates
   timestamped frames until their moment.
9. ``GestureDispatcher`` / ``SpeakingStatusProcessor`` -- downstream of that
   clock, so gestures and LEDs land with the audio rather than ahead of it.
10. ``context.assistant()`` -- the reply goes back into conversation history.
"""

from __future__ import annotations

import asyncio
import sys

from livekit import api
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport
from pipecat.turns.user_mute.mute_until_first_bot_complete_user_mute_strategy import (
    MuteUntilFirstBotCompleteUserMuteStrategy,
)

from mesh_common.logging import get_logger
from mesh_common.protocol import Status, StatusMessage, encode
from mesh_server.expression.processors import (
    ActionTagProcessor,
    GestureDispatcher,
    GestureTimeline,
    ListeningStatusProcessor,
    SpeakingStatusProcessor,
)
from mesh_server.memory import MemoryStore
from mesh_server.settings import DATA_DIR, Settings

logger = get_logger("brain")


def _build_token(settings: Settings) -> str:
    """Mint a LiveKit access token for this process.

    Args:
        settings: Loaded configuration.

    Returns:
        A signed JWT granting join rights to the configured room.
    """
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(settings.identity)
        .with_name("MESH Brain")
        .with_grants(api.VideoGrants(room_join=True, room=settings.room_name))
        .to_jwt()
    )


def _build_vad(settings: Settings) -> SileroVADAnalyzer:
    """Construct the voice activity detector.

    The thresholds here are inherited tuning, not defaults: ``min_volume``
    keeps servo noise from registering as speech, and ``stop_secs`` is held
    below Deepgram's p99 transcript latency so final transcripts arrive before
    the turn is closed.

    Args:
        settings: Loaded configuration.

    Returns:
        A configured Silero analyzer.
    """
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=settings.turn.confidence,
            min_volume=settings.turn.min_volume,
            start_secs=settings.turn.start_secs,
            stop_secs=settings.turn.stop_secs,
        ),
        sample_rate=settings.audio.input_rate,
    )


def _build_services(settings: Settings, system_prompt: str):
    """Construct the three cloud services on the critical path.

    Args:
        settings: Loaded configuration.
        system_prompt: Fully assembled system instruction.

    Returns:
        A ``(stt, llm, tts)`` tuple.
    """
    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(
            model=settings.deepgram_model,
            interim_results=True,
            punctuate=True,
            smart_format=True,
            # Deepgram's own endpointing is redundant now that end-of-turn is
            # decided by the Smart Turn model, and leaving it on only delays
            # the final transcript.
            endpointing=False,
        ),
    )

    llm = GoogleLLMService(
        api_key=settings.gemini_api_key,
        settings=GoogleLLMService.Settings(
            model=settings.gemini_model,
            system_instruction=system_prompt,
        ),
    )

    tts = ElevenLabsTTSService(
        api_key=settings.elevenlabs_api_key,
        sample_rate=settings.audio.output_rate,
        settings=ElevenLabsTTSService.Settings(voice=settings.elevenlabs_voice_id),
    )

    return stt, llm, tts


async def run() -> int:
    """Build and run the conversation pipeline until interrupted.

    Returns:
        A process exit code.
    """
    settings = Settings()

    missing = settings.missing_credentials()
    if missing:
        logger.error("Missing credentials: " + ", ".join(missing))
        logger.error(f"Set them in {DATA_DIR.parent / '.env'} and try again.")
        return 1

    memory = MemoryStore(DATA_DIR / "facts.json")
    system_prompt = settings.load_system_prompt(memory.as_prompt_block())
    logger.info(
        f"Persona '{settings.personality}' "
        f"(available: {', '.join(settings.available_personalities())}), "
        f"{len(memory)} remembered facts"
    )

    transport = LiveKitTransport(
        url=settings.livekit_url,
        token=_build_token(settings),
        room_name=settings.room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_in_sample_rate=settings.audio.input_rate,
            audio_out_enabled=True,
            audio_out_sample_rate=settings.audio.output_rate,
            # Smaller outbound chunks: less buffering before the first sound
            # leaves the machine. Pipecat's 40ms default suits congested cloud
            # links, not a LAN.
            audio_out_10ms_chunks=max(1, settings.audio.output_chunk_ms // 10),
        ),
    )

    async def send(message: str) -> None:
        """Put one encoded message on the room's data channel.

        Args:
            message: JSON payload from ``mesh_common.protocol.encode``.
        """
        try:
            await transport.send_message(message)
        except Exception as exc:  # the conversation must survive a dropped packet
            logger.warning(f"Could not send control message: {exc}")

    stt, llm, tts = _build_services(settings, system_prompt)
    timeline = GestureTimeline()

    context_aggregator = LLMContextAggregatorPair(
        context=LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=_build_vad(settings),
            # The old AlwaysUserMuteStrategy gated the mic for the whole time
            # the robot was speaking, which made barge-in structurally
            # impossible. Echo is now cancelled acoustically on the robot, so
            # the mic can stay live; this only holds it shut through the
            # opening greeting, before the echo canceller has converged.
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
        assistant_params=LLMAssistantAggregatorParams(
            enable_auto_context_summarization=True,
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            ListeningStatusProcessor(send),
            stt,
            context_aggregator.user(),
            llm,
            ActionTagProcessor(timeline, memory),
            tts,
            transport.output(),
            GestureDispatcher(timeline, send),
            SpeakingStatusProcessor(send),
            context_aggregator.assistant(),
        ]
    )

    latency = UserBotLatencyObserver()

    @latency.event_handler("on_latency_measured")
    async def _on_latency(_observer, seconds: float) -> None:
        logger.info(f"[LATENCY] user stopped -> bot speaking: {seconds * 1000:.0f}ms")

    @latency.event_handler("on_latency_breakdown")
    async def _on_breakdown(_observer, breakdown) -> None:
        parts = " ".join(
            f"{m.processor}={m.duration_secs * 1000:.0f}ms" for m in breakdown.ttfb
        )
        if parts:
            logger.info(f"[LATENCY] {parts}")

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=settings.audio.input_rate,
            audio_out_sample_rate=settings.audio.output_rate,
            enable_metrics=settings.enable_metrics,
            enable_usage_metrics=settings.enable_metrics,
        ),
        observers=[latency] if settings.enable_metrics else [],
        enable_rtvi=False,
        idle_timeout_secs=None,
    )

    # on_first_participant_joined covers both orderings -- the transport also
    # raises it for participants already in the room when we connect, so the
    # greeting does not depend on the robot booting second.
    @transport.event_handler("on_first_participant_joined")
    async def _on_robot_joined(_transport, participant_id) -> None:
        """Greet the robot when it joins.

        Args:
            _transport: The transport raising the event.
            participant_id: SID of the joining participant.
        """
        logger.info(f"Robot joined: {participant_id}")
        await send(encode(StatusMessage(Status.IDLE)))
        # TTSSpeakFrame goes straight to synthesis. The previous code queued an
        # LLMTextFrame into the pipeline *source*, where it flowed into STT and
        # the aggregator instead of ever reaching the voice.
        await task.queue_frame(TTSSpeakFrame("Hey! I'm online. What are we doing?"))

    @transport.event_handler("on_participant_disconnected")
    async def _on_left(_transport, participant) -> None:
        """Log the robot leaving the room.

        Args:
            _transport: The transport raising the event.
            participant: The departing participant.
        """
        logger.info(f"Participant left: {participant}")

    logger.info(f"Joining {settings.livekit_url} as '{settings.identity}'")
    await PipelineRunner().run(task)
    return 0


def main() -> None:
    """Console entry point."""
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
