
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

from mesh_common.logging import get_logger
from mesh_server import config
from mesh_server.chatterbox_service import ChatterboxTTSService

logger = get_logger("pipecat_server")

class ActionTagProcessor(FrameProcessor):
    """
    Extracts [ACTION: XYZ] tags from LLM text stream.
    Sends them as data messages via transport and removes them from TTS stream.
    """
    def __init__(self, transport: LiveKitTransport):
        super().__init__()
        self._transport = transport
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        logger.debug(f"ActionTagProcessor: Processing frame {type(frame).__name__}")
        if isinstance(frame, LLMTextFrame):
            text = frame.text
            self._buffer += text
            
            # Find all action tags [ACTION: NAME: PARAM] or [ACTION: NAME]
            actions = re.findall(r'\[ACTION: (.*?)\]', self._buffer)
            for action_str in actions:
                logger.info(f"Detected Action: {action_str}")
                
                # Parse "ACTION:PARAM" or just "ACTION"
                parts = action_str.split(':', 1)
                action = parts[0].strip().lower()
                param = parts[1].strip() if len(parts) > 1 else None
                
                # Send to client via LiveKit Data Channel in MESH-standard format
                await self._transport.send_data({
                    "type": "action", 
                    "action": action,
                    "param": param
                })
                
                # Remove from buffer to prevent it being sent to TTS
                self._buffer = self._buffer.replace(f"[ACTION: {action_str}]", "")
            
            # Push the cleaned text forward
            clean_text = self._buffer
            if '[' in clean_text and ']' not in clean_text[clean_text.rfind('['):]:
                # Partial tag detected at the end, keep it in buffer
                sendable_text = clean_text[:clean_text.rfind('[')]
                self._buffer = clean_text[clean_text.rfind('['):]
            else:
                sendable_text = clean_text
                self._buffer = ""
            
            if sendable_text:
                logger.info(f"LLM Output (cleaned): {sendable_text}")
                await self.push_frame(LLMTextFrame(sendable_text))
        else:
            await self.push_frame(frame, direction)

async def main():
    # Ensure LiveKit credentials are present
    if not config.LIVEKIT_API_KEY or not config.LIVEKIT_API_SECRET:
        logger.error("LIVEKIT_API_KEY or LIVEKIT_API_SECRET not set in .env")
        return

    # 1. Transport
    # Generate LiveKit Token
    from livekit import api
    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity("MESH-Brain")
        .with_name("MESH-Brain")
        .with_grants(api.VideoGrants(room_join=True, room="mesh-robot-room"))
        .to_jwt()
    )

    # 1. Transport
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

    # 2. Multimodal Context Aggregator
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
    
    class MultimodalAudioAggregator(LLMUserAggregator):
        def __init__(self, context: LLMContext):
            super().__init__(context)
            self._audio_buffer = []
            self._frame_count = 0

        async def push_aggregation(self):
            if self._audio_buffer:
                logger.info(f"Aggregating {len(self._audio_buffer)} frames for Gemini.")
                await self._context.add_audio_frames_message(audio_frames=self._audio_buffer)
                self._audio_buffer = []
                await self.push_context_frame()
                return "Audio Message"
            return ""

        async def process_frame(self, frame, direction):
            logger.debug(f"MultimodalAggregator: Processing frame {type(frame).__name__}")
            if isinstance(frame, AudioRawFrame):
                self._audio_buffer.append(frame)
                self._frame_count += 1
                if self._frame_count % 100 == 0:
                    level = np.abs(np.frombuffer(frame.audio, dtype=np.int16)).mean()
                    logger.info(f"Server receiving audio - Frame {self._frame_count}, Level: {level:.2f}")
                await super().process_frame(frame, direction)
            elif isinstance(frame, UserStartedSpeakingFrame):
                logger.info("VAD Trigger: User started speaking.")
                self._audio_buffer = []
                await super().process_frame(frame, direction)
            else:
                await super().process_frame(frame, direction)

    # Initialize Context
    context = LLMContext(messages=[
        {"role": "system", "content": config.SYSTEM_PROMPT + "\n\nCRITICAL: You are receiving raw audio input. Analyze the user's voice and respond as Rocky. Keep responses short, punchy, and excited. Use 'Amaze!' frequently. Use [ACTION: ...] tags liberally within your speech."}
    ])
    
    aggregator = MultimodalAudioAggregator(context)

    # 3. LLM (Gemini)
    MODEL_NAME = "gemini-1.5-flash" if config.GEMINI_MODEL_NAME == "gemini-3-flash-preview" else config.GEMINI_MODEL_NAME
    llm = GoogleLLMService(
        api_key=config.GEMINI_API_KEY,
        settings=GoogleLLMService.Settings(
            model=MODEL_NAME
        )
    )

    # 4. TTS (Chatterbox Turbo)
    tts = ChatterboxTTSService()

    # 5. Action Processor
    action_processor = ActionTagProcessor(transport)

    # 6. Pipeline
    # Pipeline flow: 
    # Transport In (Audio) -> Aggregator (WAV Packager) -> LLM (Standard Multimodal) -> Action Processor -> TTS -> Transport Out
    pipeline = Pipeline([
        transport.input(),
        aggregator,
        llm,
        action_processor,
        tts,
        transport.output()
    ])

    task = PipelineTask(pipeline, params=PipelineParams(
        allow_interruptions=True,
        enable_metrics=True
    ))

    # Proactive Welcome: Make Rocky introduce himself immediately
    await task.queue_frame(LLMContextFrame(context))

    # Handle interruptions & UI state
    @transport.event_handler("on_participant_started_speaking")
    async def on_vad_start(transport, participant):
        logger.info(f"VAD: {participant.identity} started speaking. Cancelling current task.")
        # If Rocky is currently in the middle of a thought, note the interruption
        # so he can react to it in his next turn.
        logger.info("User started speaking, interrupting...")
        context.add_message({"role": "system", "content": "The user interrupted you. Feel free to be slightly annoyed or surprised in your next response if it fits your personality."})
        await task.cancel() # Interrupt current response

    @transport.event_handler("on_participant_stopped_speaking")
    async def on_vad_stop(transport, participant):
        logger.info("User stopped speaking.")

    runner = PipelineRunner()
    
    logger.info("Pipecat Pipeline Starting... Waiting for connections on LiveKit.")
    await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())
