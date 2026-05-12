import asyncio
import os
import re
import sys
import numpy as np
from typing import List

from pipecat.frames.frames import (
    AudioRawFrame,
    UserAudioRawFrame,
    Frame,
    LLMTextFrame,
    LLMContextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    CancelFrame,
    StartFrame
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_mute import AlwaysUserMuteStrategy
from pipecat.services.deepgram.stt import DeepgramSTTService

from mesh_common.logging import get_logger
from mesh_server import config

logger = get_logger("pipecat_server")

class MESHFrameProcessor(FrameProcessor):
    """Base class for MESH processors."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)



from pipecat.processors.audio.vad_processor import VADProcessor

class ActionTagProcessor(MESHFrameProcessor):
    def __init__(self, transport: LiveKitTransport):
        super().__init__()
        self.transport = transport
        logger.info("ActionTagProcessor initialized")
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, LLMTextFrame):
            text = frame.text
            
            # Simple fallback extraction since we don't have the regex module installed
            if "[ACTION:" in text:
                logger.info(f"Detected Action Frame: {text}")
            self._buffer += text
            actions = re.findall(r'\[ACTION: (.*?)\]', self._buffer)
            for action_str in actions:
                logger.info(f"Detected Action: {action_str}")
                parts = action_str.split(':', 1)
                action = parts[0].strip().lower()
                param = parts[1].strip() if len(parts) > 1 else None
                
                import json
                await self.transport.send_message(json.dumps({
                    "type": "action", 
                    "action": action,
                    "param": param
                }))
                self._buffer = self._buffer.replace(f"[ACTION: {action_str}]", "")
                
            memories = re.findall(r'\[MEMORY: (.*?)\]', self._buffer)
            for mem_str in memories:
                logger.info(f"Detected Memory: {mem_str}")
                # Future: Save memory to disk/context
                self._buffer = self._buffer.replace(f"[MEMORY: {mem_str}]", "")
            
            clean_text = self._buffer
            if '[' in clean_text and ']' not in clean_text[clean_text.rfind('['):]:
                sendable_text = clean_text[:clean_text.rfind('[')]
                self._buffer = clean_text[clean_text.rfind('['):]
            else:
                sendable_text = clean_text
                self._buffer = ""
            
            if sendable_text:
                await self.push_frame(LLMTextFrame(sendable_text), direction)
        else:
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

class LEDStateProcessor(MESHFrameProcessor):
    """Sends LED state updates to the client based on pipeline events."""
    def __init__(self, transport: LiveKitTransport):
        super().__init__()
        self.transport = transport

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        import json
        
        if isinstance(frame, UserStartedSpeakingFrame):
            await self.transport.send_message(json.dumps({
                "type": "led", "state": "listening"
            }))
        elif isinstance(frame, UserStoppedSpeakingFrame):
            await self.transport.send_message(json.dumps({
                "type": "led", "state": "thinking"
            }))
        elif isinstance(frame, BotStartedSpeakingFrame):
            await self.transport.send_message(json.dumps({
                "type": "led", "state": "speaking"
            }))
        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self.transport.send_message(json.dumps({
                "type": "led", "state": "idle"
            }))
        
        await self.push_frame(frame, direction)

class MultimodalAudioAggregator(MESHFrameProcessor):
    def __init__(self, context: LLMContext):
        super().__init__()
        self._context = context
        self._audio_buffer = []
        self._frame_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, (AudioRawFrame, UserAudioRawFrame)):
            self._audio_buffer.append(frame)
            self._frame_count += 1
            if self._frame_count % 20 == 0:
                level = np.abs(np.frombuffer(frame.audio, dtype=np.int16)).mean()
                logger.info(f"AUDIO IN: Level={level:.2f}")
            await self.push_frame(frame, direction)
        elif isinstance(frame, UserStartedSpeakingFrame):
            logger.info("VAD: User started speaking.")
            self._audio_buffer = []
            await self.push_frame(frame, direction)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            logger.info(f"VAD: User stopped speaking. Aggregating {len(self._audio_buffer)} frames.")
            if self._audio_buffer:
                await self._context.add_audio_frames_message(audio_frames=self._audio_buffer)
                self._audio_buffer = []
                await self.push_frame(LLMContextFrame(self._context))
            await self.push_frame(frame, direction)
        else:
            if not isinstance(frame, (AudioRawFrame, UserAudioRawFrame)):
                logger.info(f"Pipeline Frame: {type(frame).__name__}")
            await super().process_frame(frame, direction)

async def main():
    if not config.LIVEKIT_API_KEY or not config.LIVEKIT_API_SECRET:
        logger.error("LiveKit credentials missing.")
        return

    from livekit import api
    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity("MESH-Brain")
        .with_grants(api.VideoGrants(room_join=True, room="mesh-robot-room"))
        .to_jwt()
    )

    transport = LiveKitTransport(
        url=config.LIVEKIT_URL,
        token=token,
        room_name="mesh-robot-room",
        params=LiveKitParams(
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            audio_in_enabled=True,
            audio_in_sample_rate=16000
        )
    )

    stt = DeepgramSTTService(
        api_key=config.DEEPGRAM_API_KEY,
        settings=DeepgramSTTService.Settings(
            interim_results=True,
            endpointing=300
        )
    )
    
    llm = GoogleLLMService(
        api_key=config.GEMINI_API_KEY,
        settings=GoogleLLMService.Settings(
            model=config.GEMINI_MODEL_NAME
        )
    )

    if config.USE_ELEVENLABS:
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        tts = ElevenLabsTTSService(
            api_key=config.ELEVENLABS_API_KEY,
            sample_rate=24000,
            settings=ElevenLabsTTSService.Settings(
                voice=config.ELEVENLABS_VOICE_ID             
            )
        )
    else:
        from mesh_server.chatterbox_service import ChatterboxTTSService
        tts = ChatterboxTTSService()
    
    action_processor = ActionTagProcessor(transport)
    led_processor = LEDStateProcessor(transport)
    # Set stop_secs to 0.8s so it doesn't cut off words if the user pauses slightly.
    vad_analyzer = SileroVADAnalyzer(
        params=VADParams(confidence=0.5, min_volume=0.09, stop_secs=1), 
        sample_rate=16000
    )

    context = LLMContext(messages=[
        {"role": "system", "content": config.SYSTEM_PROMPT + "\n\nCRITICAL:Keep responses short, punchy, and excited. Use [ACTION: ...] tags liberally within your speech."}
    ])
    
    context_aggregator = LLMContextAggregatorPair(
        context=context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad_analyzer,
            user_mute_strategies=[AlwaysUserMuteStrategy()]
        )
    )

    pipeline = Pipeline([
        transport.input(),
        led_processor,
        stt,
        context_aggregator.user(),
        llm,
        action_processor,
        tts,
        transport.output(),
        context_aggregator.assistant()
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=False,
            audio_out_sample_rate=24000
        ),
        enable_rtvi=False,
        idle_timeout_secs=None
    )

    @transport.event_handler("on_participant_connected")
    async def on_participant_connected(transport, participant):
        logger.info(f"Participant joined: {participant}. Sending greeting.")
        await task.queue_frames([LLMTextFrame("Amaze! I'm online and ready to rock!")])

    runner = PipelineRunner()
    await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())
