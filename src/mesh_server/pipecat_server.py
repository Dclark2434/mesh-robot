
import asyncio
import os
import re
import sys
from typing import List

from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    LLMTextFrame,
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
            
            # Also clean vocal tags if necessary, though Chatterbox handles them
            # For now, we only strip the ACTION tags so the TTS doesn't read them
            
            # Push the cleaned text forward
            # Note: We need to be careful with partial tags at the end of the buffer
            # If the buffer ends with '[', we wait.
            clean_text = self._buffer
            if '[' in clean_text and ']' not in clean_text[clean_text.rfind('['):]:
                # Partial tag detected at the end, keep it in buffer
                sendable_text = clean_text[:clean_text.rfind('[')]
                self._buffer = clean_text[clean_text.rfind('['):]
            else:
                sendable_text = clean_text
                self._buffer = ""
            
            if sendable_text:
                await self.push_frame(LLMTextFrame(sendable_text))
        else:
            await super().process_frame(frame, direction)

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
        params=LiveKitParams(vad=SileroVADAnalyzer())
    )

    # 2. Multimodal Context Aggregator
    # This collects raw audio and appends it to the LLM context as a 'Part'
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
    
    class MultimodalAudioAggregator(LLMUserAggregator):
        def __init__(self, context: LLMContext):
            super().__init__(context)
            self._audio_buffer = []

        async def process_frame(self, frame, direction):
            from pipecat.frames.frames import AudioRawFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame, LLMContextFrame
            
            if isinstance(frame, AudioRawFrame):
                self._audio_buffer.append(frame)
            elif isinstance(frame, UserStartedSpeakingFrame):
                self._audio_buffer = []
                await super().process_frame(frame, direction)
            elif isinstance(frame, UserStoppedSpeakingFrame):
                if self._audio_buffer:
                    # Create the native audio message for Gemini
                    logger.info(f"Aggregated {len(self._audio_buffer)} audio frames for Gemini analysis.")
                    await self._context.add_audio_frames_message(audio_frames=self._audio_buffer)
                    self._audio_buffer = []
                    # Push context to LLM
                    await self.push_frame(LLMContextFrame(self._context))
                await super().process_frame(frame, direction)
            else:
                await super().process_frame(frame, direction)

    # Initialize Context
    context = LLMContext(messages=[
        {"role": "system", "content": config.SYSTEM_PROMPT + "\n\nCRITICAL: You are receiving raw audio input. Analyze the user's voice and respond as Rocky. Keep responses short, punchy, and excited. Use 'Amaze!' frequently. Use [ACTION: ...] tags liberally within your speech."}
    ])
    
    aggregator = MultimodalAudioAggregator(context)

    # 3. LLM (Gemini 3.0 Flash - Standard API)
    llm = GoogleLLMService(
        api_key=config.GEMINI_API_KEY,
        settings=GoogleLLMService.Settings(
            model=config.GEMINI_MODEL_NAME # gemini-3-flash-preview
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

    # Handle interruptions & UI state
    @transport.event_handler("on_participant_started_speaking")
    async def on_vad_start(transport, participant):
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
