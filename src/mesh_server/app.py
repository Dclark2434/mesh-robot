"""The brain: a persistent real-time conversation pipeline.

Runs on the workstation, joins the same LiveKit room as the robot, and holds
one continuous session: the robot's microphone in, synthesized speech and
gesture instructions out.

Pipeline order, and why:

1. ``transport.input()``, audio from the robot's mic.
2. ``ListeningStatusProcessor``, LEDs follow the user's turn.
3. ``stt``; Deepgram streaming transcription.
4. ``context.user()``, owns voice activity detection, end-of-turn decisions
   and mic muting, and accumulates the user's turn.
5. ``llm``; Gemini over the standard text API.
6. ``ActionTagProcessor``, directives out of the text, gestures onto the
   timeline.
7. ``tts``; ElevenLabs streaming synthesis.
8. ``transport.output()``, audio to the robot, and the clock that gates
   timestamped frames until their moment.
9. ``GestureDispatcher`` / ``SpeakingStatusProcessor``, downstream of that
   clock, so gestures and LEDs land with the audio rather than ahead of it.
10. ``context.assistant()``; the reply goes back into conversation history.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from livekit import api
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import NOT_GIVEN, LLMContext
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
from pipecat.turns.user_start import (
    MinWordsUserTurnStartStrategy,
    TranscriptionUserTurnStartStrategy,
    VADUserTurnStartStrategy,
    WakePhraseUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import (
    UserTurnStrategies,
    default_user_turn_stop_strategies,
)
from pipecat.utils.context.llm_context_summarization import (
    LLMAutoContextSummarizationConfig,
)

from mesh_common.logging import get_logger
from mesh_common.protocol import (
    MessageType,
    Status,
    StatusMessage,
    action_keyterms,
    decode,
    encode,
)
from mesh_server.dashboard.events import bus
from mesh_server.dashboard.health import HealthTracker, State
from mesh_server.dashboard.server import DashboardServer
from mesh_server.dashboard.taps import HeardTap, SaidTap, attach_log_bridge
from mesh_server.expression.echo import SpokenRecord
from mesh_server.expression.processors import (
    ActionTagProcessor,
    EchoGuard,
    GestureDispatcher,
    GestureTimeline,
    ListeningStatusProcessor,
    SpeakingStatusProcessor,
)
from mesh_server.memory import MemoryStore
from mesh_server.settings import DATA_DIR, Settings
from mesh_server.vision.ambient import AmbientVision, SceneSummarizer
from mesh_server.vision.feed import (
    AmbientVisionProcessor,
    CameraFeed,
    CameraFeedProcessor,
    VisionContextPruner,
    make_look_handler,
    vision_tools,
)

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


def _build_turn_strategies(settings: Settings) -> UserTurnStrategies:
    """Decide what counts as the user starting and stopping a turn.

    The default start strategies include voice activity alone, which in a room
    with a servo-driven robot in it means a turn begins every time something
    clicks. Each false start broadcasts an interruption, cutting the robot
    off mid-sentence, and then waits for a transcript that never arrives,
    until the stop timeout expires.

    Requiring a couple of recognised words instead makes a turn start on
    speech rather than on sound. Interim transcripts are used, so it still
    fires while the user is talking rather than after they finish.

    Args:
        settings: Loaded configuration.

    Returns:
        Configured start and stop strategies.
    """
    if settings.turn.min_words > 0:
        start = [
            MinWordsUserTurnStartStrategy(
                min_words=settings.turn.min_words, use_interim=True
            ),
            TranscriptionUserTurnStartStrategy(),
        ]
    else:
        start = [VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()]

    # In a room with other people talking (a television, a toddler) word
    # count is not enough, because babble contains words. A wake phrase gates
    # on being *addressed*. Timeout mode keeps the conversation natural: say
    # the name once, then talk normally, and the timer resets on every
    # exchange. It only closes again after a genuine lull.
    if settings.turn.wake_phrases:
        wake = WakePhraseUserTurnStartStrategy(
            phrases=settings.turn.wake_phrases,
            timeout=settings.turn.wake_timeout,
        )
        logger.info(
            f"Wake phrases active: {', '.join(settings.turn.wake_phrases)} "
            f"(stays awake {settings.turn.wake_timeout:.0f}s)"
        )
        start = [wake, *start]

    return UserTurnStrategies(start=start, stop=default_user_turn_stop_strategies())


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
    stt_settings: dict[str, Any] = {
        "model": settings.deepgram_model,
        "interim_results": True,
        "punctuate": True,
        "smart_format": True,
        # Deepgram's own endpointing is redundant now that end-of-turn is
        # decided by the Smart Turn model, and leaving it on only delays
        # the final transcript.
        "endpointing": False,
    }

    # Bias recognition toward the words this robot is actually asked to act on,
    # plus whatever it is called. Keyterm prompting is a nova-3 feature, so it
    # is only sent when that is the model in use.
    if settings.deepgram_model.startswith("nova-3"):
        stt_settings["keyterm"] = [settings.personality, *action_keyterms()]

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        settings=DeepgramSTTService.Settings(**stt_settings),
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

    # The dashboard is wired up first so that startup itself is visible in it.
    health = HealthTracker()
    bus.bind(asyncio.get_running_loop())
    attach_log_bridge(bus)
    health.update("brain", State.OK, "running")

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
            # Subscribes to the robot's camera. Frames are absorbed by
            # CameraFeedProcessor immediately; nothing downstream sees them
            # until the model asks to look.
            video_in_enabled=settings.vision_enabled,
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
        payload = decode(message)
        if payload.get("type") == MessageType.STATUS.value:
            bus.publish("status", status=payload.get("status"))
        try:
            await transport.send_message(message)
        except Exception as exc:  # the conversation must survive a dropped packet
            logger.warning(f"Could not send control message: {exc}")

    stt, llm, tts = _build_services(settings, system_prompt)
    timeline = GestureTimeline()
    # What the robot is saying, so a transcript of its own voice can be
    # recognised before it becomes an interruption.
    spoken = SpokenRecord()

    # Vision. The camera streams continuously but only reaches the model when
    # it calls `look`; see mesh_server/vision.py for why it is a tool rather
    # than an action tag.
    feed = CameraFeed()
    llm.register_function("look", make_look_handler(feed))

    context = LLMContext(tools=vision_tools() if settings.vision_enabled else NOT_GIVEN)

    # Ambient awareness: after the robot walks somewhere, a cheap one-shot call
    # turns a frame into one sentence. Only that sentence enters the
    # conversation, and only between turns, see vision/ambient.py.
    ambient = AmbientVision(
        SceneSummarizer(settings.gemini_api_key, settings.ambient_model)
        if settings.vision_enabled and settings.ambient_vision
        else None
    )
    ambient_processor = AmbientVisionProcessor(ambient, feed, context)

    context_aggregator = LLMContextAggregatorPair(
        context=context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=_build_vad(settings),
            user_turn_strategies=_build_turn_strategies(settings),
            # The fallback for when no stop strategy fires, which happens on
            # every turn that started from noise, because no transcript is
            # coming. The default of 5s is five seconds of the robot appearing
            # to think about a cough.
            user_turn_stop_timeout=settings.turn.stop_timeout,
            # The old AlwaysUserMuteStrategy gated the mic for the whole time
            # the robot was speaking, which made barge-in structurally
            # impossible. Echo is now cancelled acoustically on the robot, so
            # the mic can stay live; this only holds it shut through the
            # opening greeting, before the echo canceller has converged.
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
        assistant_params=LLMAssistantAggregatorParams(
            enable_auto_context_summarization=True,
            # Trigger on size, not on message count. The default also fires
            # every 20 messages, and a fragmented turn produces messages like
            # "I" and "one step left.", so a spoken sentence can cost three
            # of them. That had summarization running every few exchanges,
            # sometimes twice concurrently, each a multi-second call to the
            # same Gemini the conversation is waiting on.
            auto_context_summarization_config=LLMAutoContextSummarizationConfig(
                max_context_tokens=settings.turn.summarize_above_tokens,
                max_unsummarized_messages=None,
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            # Absorbs the video stream before it can travel any further, and
            # answers the model's requests to look.
            CameraFeedProcessor(feed),
            ambient_processor,
            ListeningStatusProcessor(send),
            stt,
            # Before the aggregator, which consumes transcription frames
            # rather than forwarding them.
            # Both before the aggregator: it is what turns a transcript into a
            # user turn and therefore into an interruption, so anything later
            # is too late to stop the robot cutting itself off. The guard runs
            # first, so what the dashboard reports as heard is speech that was
            # actually acted on.
            *([EchoGuard(spoken)] if settings.echo_guard else []),
            HeardTap(bus),
            context_aggregator.user(),
            llm,
            ActionTagProcessor(timeline, memory, spoken),
            # After tag stripping, so the transcript shows what was spoken
            # rather than the raw model output with directives still in it.
            SaidTap(bus),
            # Collapses stale images once the reply is complete, so one look
            # does not tax every turn that follows it.
            VisionContextPruner(context, settings.keep_images),
            tts,
            transport.output(),
            GestureDispatcher(timeline, send),
            SpeakingStatusProcessor(send, spoken),
            context_aggregator.assistant(),
        ]
    )

    latency = UserBotLatencyObserver()

    # A conversational reply is never slower than this. Anything above it is an
    # artifact: the observer times from the last "user stopped speaking" to the
    # next "bot started speaking", so a greeting triggered by the robot
    # reconnecting gets attributed to whatever was said minutes earlier. Real
    # numbers are worth watching, and one 96-second bar ruins the scale.
    IMPLAUSIBLE_LATENCY_MS = 15_000.0

    last_total_ms = 0.0

    @latency.event_handler("on_latency_measured")
    async def _on_latency(_observer, seconds: float) -> None:
        nonlocal last_total_ms
        last_total_ms = seconds * 1000
        if last_total_ms > IMPLAUSIBLE_LATENCY_MS:
            logger.debug(f"[LATENCY] ignoring {last_total_ms:.0f}ms (not a real turn)")
            return
        logger.info(f"[LATENCY] user stopped -> bot speaking: {last_total_ms:.0f}ms")

    @latency.event_handler("on_latency_breakdown")
    async def _on_breakdown(_observer, breakdown) -> None:
        parts = " ".join(
            f"{m.processor}={m.duration_secs * 1000:.0f}ms" for m in breakdown.ttfb
        )
        if parts:
            logger.info(f"[LATENCY] {parts}")

        # A service that just answered is, definitionally, up, and its own
        # time-to-first-byte is the most useful "detail" the status panel can
        # show for it.
        breakdown_data = []
        for metric in breakdown.ttfb:
            millis = metric.duration_secs * 1000
            for key, marker in (("stt", "Deepgram"), ("llm", "Google"), ("tts", "ElevenLabs")):
                if marker.lower() in metric.processor.lower():
                    # A service that answered is up, but a stale timer is not a
                    # useful "detail" to display next to it.
                    plausible = millis <= IMPLAUSIBLE_LATENCY_MS
                    health.update(key, State.OK, f"{millis:.0f}ms" if plausible else "ok")
            if millis <= IMPLAUSIBLE_LATENCY_MS:
                breakdown_data.append({"service": metric.processor, "ms": round(millis)})

        if breakdown_data and last_total_ms <= IMPLAUSIBLE_LATENCY_MS:
            bus.publish("latency", total_ms=round(last_total_ms), breakdown=breakdown_data)

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

    greeted_sid: str | None = None

    async def welcome(participant_id: str) -> None:
        """Reset the robot's state and greet it.

        Runs on every join, not just the first. Restarting the robot while the
        brain keeps running is the normal debugging loop, and without this the
        robot comes back to no greeting and whatever LED state it was left in
        -- which reads as a hang.

        Args:
            participant_id: SID of the joining participant. Used to
                de-duplicate, since the transport raises two events for the
                very first participant.
        """
        nonlocal greeted_sid
        if participant_id == greeted_sid:
            return
        greeted_sid = participant_id

        logger.info(f"Robot joined: {participant_id}")
        health.update("robot", State.OK, "connected")
        # Clears any stale status the robot was left showing.
        await send(encode(StatusMessage(Status.IDLE)))
        # TTSSpeakFrame goes straight to synthesis. The previous code queued an
        # LLMTextFrame into the pipeline *source*, where it flowed into STT and
        # the aggregator instead of ever reaching the voice.
        await task.queue_frame(TTSSpeakFrame("Hey! I'm online. What are we doing?"))

    # Two handlers, because neither covers both cases on its own:
    # on_first_participant_joined also fires for a robot that was already in
    # the room before the brain started, and on_participant_connected is the
    # only one that fires again when the robot reconnects.
    @transport.event_handler("on_first_participant_joined")
    async def _on_first_joined(_transport, participant_id) -> None:
        """Greet a robot that was already in the room.

        Args:
            _transport: The transport raising the event.
            participant_id: SID of the participant.
        """
        await welcome(participant_id)

    @transport.event_handler("on_participant_connected")
    async def _on_joined(_transport, participant_id) -> None:
        """Greet a robot that has just connected or reconnected.

        Args:
            _transport: The transport raising the event.
            participant_id: SID of the participant.
        """
        await welcome(participant_id)

    @transport.event_handler("on_data_received")
    async def _on_robot_message(_transport, data, _participant_id) -> None:
        """Handle a message from the robot.

        Args:
            _transport: The transport raising the event.
            data: Raw payload.
            _participant_id: Sender's SID.
        """
        payload = decode(data)
        kind = payload.get("type")
        health.touch("robot")

        if kind == MessageType.MOVED.value:
            ambient_processor.on_moved()

        elif kind == MessageType.LOG.value:
            # Mirrored into the same stream as the brain's own logs, tagged so
            # the dashboard can show the two halves side by side.
            for line in payload.get("lines", []):
                bus.publish(
                    "log",
                    origin="robot",
                    level=line.get("level", "INFO"),
                    source=line.get("source", ""),
                    message=line.get("message", ""),
                    dropped=payload.get("dropped", 0),
                )

        elif kind == MessageType.HELLO.value:
            health.update("robot", State.OK, "connected")
            health.update(
                "hardware",
                State.OK if payload.get("hardware") else State.WARN,
                "live" if payload.get("hardware") else "simulated",
            )
            health.update(
                "audio",
                State.OK if payload.get("echo_cancellation") else State.WARN,
                "AEC on" if payload.get("echo_cancellation") else "mic gated, no barge-in",
            )
            health.update(
                "camera",
                State.OK if payload.get("camera") else State.DOWN,
                payload.get("camera_backend", "none"),
            )
            logger.info(
                f"Robot ready: {len(payload.get('actions', []))} actions, "
                f"camera={payload.get('camera_backend')}, "
                f"aec={payload.get('echo_cancellation')}"
            )

        elif kind == MessageType.TELEMETRY.value:
            # Audio buffer glitches break the alignment echo cancellation
            # depends on, so this is the light that should go amber when the
            # robot is about to start answering its own voice.
            glitches = payload.get("audio_glitches") or 0
            if glitches:
                logger.warning(
                    f"Robot reported {glitches} audio glitches; "
                    "echo cancellation may be degraded"
                )
                health.update("audio", State.WARN, f"{glitches} glitches")

            percent = payload.get("battery_percent")
            bus.publish(
                "telemetry",
                battery_percent=percent,
                battery_volts=payload.get("battery_volts"),
            )
            if percent is None:
                health.update("battery", State.UNKNOWN)
            elif percent < 15:
                health.update("battery", State.DOWN, f"{percent:.0f}%")
                logger.warning(f"Robot battery at {percent:.0f}%")
            elif percent < 30:
                health.update("battery", State.WARN, f"{percent:.0f}%")
            else:
                health.update("battery", State.OK, f"{percent:.0f}%")

    @transport.event_handler("on_participant_disconnected")
    async def _on_left(_transport, participant) -> None:
        """Log the robot leaving the room.

        Args:
            _transport: The transport raising the event.
            participant: The departing participant.
        """
        nonlocal greeted_sid
        greeted_sid = None
        logger.info(f"Participant left: {participant}")
        health.update("robot", State.DOWN, "disconnected")
        for key in ("hardware", "audio", "camera", "battery"):
            health.update(key, State.UNKNOWN, "robot offline")

    @transport.event_handler("on_connected")
    async def _on_connected(_transport) -> None:
        """Mark the room as joined.

        Args:
            _transport: The transport raising the event.
        """
        health.update("livekit", State.OK, settings.room_name)

    @transport.event_handler("on_disconnected")
    async def _on_disconnected(_transport) -> None:
        """Mark the room as lost.

        Args:
            _transport: The transport raising the event.
        """
        health.update("livekit", State.DOWN, "disconnected")

    dashboard: DashboardServer | None = None
    if settings.dashboard:
        dashboard = DashboardServer(
            bus,
            health,
            feed,
            {
                "persona": settings.personality,
                "llm": settings.gemini_model,
                "stt": settings.deepgram_model,
                "room": settings.room_name,
            },
        )
        await dashboard.start(settings.dashboard_host, settings.dashboard_port)

    health.update("pipeline", State.OK, "running")
    logger.info(f"Joining {settings.livekit_url} as '{settings.identity}'")
    try:
        await PipelineRunner().run(task)
    finally:
        health.update("pipeline", State.DOWN, "stopped")
        if dashboard is not None:
            await dashboard.stop()
    return 0


def main() -> None:
    """Console entry point."""
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
