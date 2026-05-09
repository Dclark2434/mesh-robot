
import asyncio
import os
import re
import sys
import numpy as np
from typing import List

from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    LLMTextFrame,
    LLMContextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    EndFrame,
    CancelFrame,
    StartFrame
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.livekit.transport import LiveKitTransport
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_context import LLMContext

from mesh_common.logging import get_logger
from mesh_server import config
from mesh_server.chatterbox_service import ChatterboxTTSService

logger = get_logger("pipecat_server")

class MESHFrameProcessor(FrameProcessor):
    """Base class for MESH processors to handle Pipecat initialization quirks."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Bypass Pipecat internal started check (private mangled variable)
        self._FrameProcessor__started = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if type(frame).__name__ == "StartFrame":
            self._FrameProcessor__started = True
        await super().process_frame(frame, direction)

class ActionTagProcessor(MESHFrameProcessor):
    def __init__(self, transport: LiveKitTransport):
        super().__init__()
        self._transport = transport
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if type(frame).__name__ == "StartFrame":
            self._FrameProcessor__started = True

        if isinstance(frame, LLMTextFrame):
            text = frame.text
            self._buffer += text
            
            actions = re.findall(r'\[ACTION: (.*?)\]', self._buffer)
            for action_str in actions:
                logger.info(f"Detected Action: {action_str}")
                parts = action_str.split(':', 1)
                action = parts[0].strip().lower()
                param = parts[1].strip() if len(parts) > 1 else None
                
                await self._transport.send_data({
                    "type": "action", 
                    "action": action,
                    "param": param
                })
                self._buffer = self._buffer.replace(f"[ACTION: {action_str}]", "")
            
            clean_text = self._buffer
            if '[' in clean_text and ']' not in clean_text[clean_text.rfind('['):]:
                sendable_text = clean_text[:clean_text.rfind('[')]
                self._buffer = clean_text[clean_text.rfind('['):]
            else:
                sendable_text = clean_text
                self._buffer = ""
            
            if sendable_text:
                await self.push_frame(LLMTextFrame(sendable_text))
        else:
            await super().process_frame(frame, direction)

class MultimodalAudioAggregator(MESHFrameProcessor):
    def __init__(self, context: LLMContext):
        super().__init__()
        self._context = context
        self._audio_buffer = []
        self._frame_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if type(frame).__name__ == "StartFrame":
            self._FrameProcessor__started = True

        if isinstance(frame, AudioRawFrame):
            self._audio_buffer.append(frame)
            self._frame_count += 1
            if self._frame_count % 100 == 0:
                level = np.abs(np.frombuffer(frame.audio, dtype=np.int16)).mean()
                logger.debug(f"Server receiving audio - Level: {level:.2f}")
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

    from pipecat.transports.livekit.transport import LiveKitParams
    transport = LiveKitTransport(
        url=config.LIVEKIT_URL,
        token=token,
        room_name="mesh-robot-room",
        params=LiveKitParams(
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            vad=SileroVADAnalyzer()
        )
    )

    context = LLMContext(messages=[
        {"role": "system", "content": config.SYSTEM_PROMPT + "\n\nCRITICAL: You are receiving raw audio input. Analyze the user's voice and respond as Rocky. Keep responses short, punchy, and excited. Use 'Amaze!' frequently. Use [ACTION: ...] tags liberally within your speech."}
    ])
    
    aggregator = MultimodalAudioAggregator(context)
    llm = GoogleLLMService(
        api_key=config.GEMINI_API_KEY,
        settings=GoogleLLMService.Settings(
            model="gemini-3-flash-preview"
        )
    )
    tts = ChatterboxTTSService()
    action_processor = ActionTagProcessor(transport)

    pipeline = Pipeline([
        transport.input(),
        aggregator,
        llm,
        action_processor,
        tts,
        transport.output()
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=False
        ),
        enable_rtvi=False
    )

    @transport.event_handler("on_participant_connected")
    async def on_participant_connected(transport, participant):
        logger.info(f"Participant joined: {participant.identity}. Sending greeting.")
        await task.queue_frames([LLMTextFrame("Amaze! I'm online and ready to rock!")])

    runner = PipelineRunner()
    await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())
