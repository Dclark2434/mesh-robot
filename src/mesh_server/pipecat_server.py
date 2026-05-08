
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
        logger.info(f"ActionTagProcessor: Processing frame {type(frame).__name__}")
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
        params=LiveKitParams(
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            vad=SileroVADAnalyzer()
        )
    )

    # 2. Multimodal Context Aggregator
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
    
    class MultimodalAudioAggregator(FrameProcessor):
        def __init__(self, context: LLMContext):
            super().__init__()
            self._context = context
            self._audio_buffer = []
            self._frame_count = 0

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            print(f"[AGGREGATOR] Received {type(frame).__name__}")
            if isinstance(frame, AudioRawFrame):
                self._audio_buffer.append(frame)
                self._frame_count += 1
                if self._frame_count % 100 == 0:
                    level = np.abs(np.frombuffer(frame.audio, dtype=np.int16)).mean()
                    print(f"[AGGREGATOR] Audio Level: {level:.2f}")
                
                # For multimodal, we don't necessarily push the raw audio forward 
                # unless the LLM expects it. Gemini multimodal expects it via the context.
                await super().process_frame(frame, direction)
            elif isinstance(frame, UserStartedSpeakingFrame):
                print("[AGGREGATOR] User started speaking, clearing buffer.")
                self._audio_buffer = []
                await super().process_frame(frame, direction)
            elif isinstance(frame, UserStoppedSpeakingFrame):
                print("[AGGREGATOR] User stopped speaking, pushing to Gemini.")
                if self._audio_buffer:
                    await self._context.add_audio_frames_message(audio_frames=self._audio_buffer)
                    self._audio_buffer = []
                    await self.push_frame(LLMContextFrame(self._context))
                await super().process_frame(frame, direction)
            else:
                await super().process_frame(frame, direction)

    # Initialize Context
    context = LLMContext(messages=[
        {"role": "system", "content": config.SYSTEM_PROMPT + "\n\nCRITICAL: You are receiving raw audio input. Analyze the user's voice and respond as Rocky. Keep responses short, punchy, and excited. Use 'Amaze!' frequently. Use [ACTION: ...] tags liberally within your speech."}
    ])
    
    aggregator = MultimodalAudioAggregator(context)

    # 3. LLM (Gemini)
    llm = GoogleLLMService(
        api_key=config.GEMINI_API_KEY,
        settings=GoogleLLMService.Settings(
            model="gemini-3-flash-preview"
        )
    )

    class Tracer(FrameProcessor):
        def __init__(self, name: str):
            super().__init__()
            self._name = name

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            logger.info(f"[TRACER:{self._name}] Received {type(frame).__name__}")
            await super().process_frame(frame, direction)
            logger.info(f"[TRACER:{self._name}] Pushed {type(frame).__name__}")

    # (Keep Aggregator and ActionTagProcessor as they were, but double check they use super())

    # 4. TTS (Chatterbox Turbo - Embedded for diagnostic)
    from pipecat.services.tts_service import TTSService
    from pipecat.frames.frames import TTSAudioRawFrame
    import torch
    
    class EmbeddedChatterboxTTS(TTSService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._sample_rate = 24000
            self._engine = None
            self._load_engine()

        def _load_engine(self):
            try:
                from chatterbox.tts_turbo import ChatterboxTurboTTS
                logger.info(f"Initializing Chatterbox Turbo on {self._device}...")
                self._engine = ChatterboxTurboTTS.from_pretrained(self._device)
                logger.info("Chatterbox Turbo loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load Chatterbox: {e}")

        async def run_tts(self, text: str):
            if not self._engine: return
            try:
                import asyncio
                audio_tensor = await asyncio.to_thread(self._engine.generate, text, temperature=0.8)
                if hasattr(audio_tensor, "cpu"):
                    wav = audio_tensor.squeeze().cpu().numpy()
                else:
                    wav = audio_tensor
                audio_int16 = (wav * 32767).astype(np.int16).tobytes()
                yield TTSAudioRawFrame(audio=audio_int16, sample_rate=self._sample_rate, num_channels=1)
            except Exception as e:
                logger.error(f"TTS error: {e}")

        async def handle_text_frame(self, frame, context_id=None):
            async for audio_frame in self.run_tts(frame.text):
                await self.push_frame(audio_frame)

        async def process_frame(self, frame, direction):
            logger.info(f"EmbeddedChatterbox: Processing frame {type(frame).__name__}")
            await super().process_frame(frame, direction)

    tts = EmbeddedChatterboxTTS()
    action_processor = ActionTagProcessor(transport)

    # 6. Pipeline (TEST MODE)
    pipeline = Pipeline([
        transport.input(),
        Tracer("A"),
        Tracer("B"),
        transport.output()
    ])

    task = PipelineTask(pipeline, params=PipelineParams(
        allow_interruptions=True,
        enable_metrics=True
    ))

    # Handle participant connection for proactive greeting
    greeting_triggered = False

    @transport.event_handler("on_participant_connected")
    async def on_participant_connected(transport, participant):
        nonlocal greeting_triggered
        identity = participant if isinstance(participant, str) else getattr(participant, "identity", "unknown")
        logger.info(f"Participant connected: {identity}")
        
        if not greeting_triggered:
            logger.info(f"First participant ({identity}) joined. Triggering proactive greeting...")
            await task.queue_frame(LLMContextFrame(context))
            greeting_triggered = True

    runner = PipelineRunner()
    
    logger.info("Pipecat Pipeline Starting... Waiting for connections on LiveKit.")
    await runner.run(task)

if __name__ == "__main__":
    asyncio.run(main())
