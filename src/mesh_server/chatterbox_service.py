
import torch
import numpy as np
from pipecat.services.tts_service import TTSService
from pipecat.frames.frames import TTSAudioRawFrame
from mesh_common.logging import get_logger
from mesh_server import config
import os

logger = get_logger("chatterbox_pipecat")

class ChatterboxTTSService(TTSService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._sample_rate = 24000
        self._engine = None
        self._load_engine()
        # Bypass Pipecat's internal started check which often fails due to class identity issues
        self._FrameProcessor__started = True

    def _load_engine(self):
        try:
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            logger.info(f"Initializing Chatterbox Turbo on {self._device}...")
            if config.HF_TOKEN:
                os.environ["HF_TOKEN"] = config.HF_TOKEN
            self._engine = ChatterboxTurboTTS.from_pretrained(self._device)
            logger.info("Chatterbox Turbo loaded successfully for Pipecat.")
        except Exception as e:
            logger.error(f"Failed to load Chatterbox Turbo: {e}")

    async def run_tts(self, text: str):
        if not self._engine:
            logger.error("Chatterbox engine not loaded.")
            return

        logger.debug(f"Synthesizing: {text}")
        
        # Prepare kwargs based on config/personality
        kwargs = {
            "temperature": 0.8,
            "norm_loudness": False,
            "exaggeration": 0.35,
        }
        
        if os.path.exists(config.REFERENCE_AUDIO):
            kwargs["audio_prompt_path"] = config.REFERENCE_AUDIO

        try:
            import asyncio
            audio_tensor = await asyncio.to_thread(self._engine.generate, text, **kwargs)
            
            if hasattr(audio_tensor, "cpu"):
                wav = audio_tensor.squeeze().cpu().numpy()
            else:
                wav = audio_tensor
            
            audio_int16 = (wav * 32767).astype(np.int16).tobytes()
            yield TTSAudioRawFrame(audio=audio_int16, sample_rate=self._sample_rate, num_channels=1)
            
        except Exception as e:
            logger.error(f"Chatterbox synthesis error: {e}")

    async def handle_text_frame(self, frame, context_id=None):
        async for audio_frame in self.run_tts(frame.text):
            await self.push_frame(audio_frame)

    async def process_frame(self, frame, direction):
        if type(frame).__name__ == "StartFrame":
            self._FrameProcessor__started = True
        
        logger.info(f"ChatterboxTTS: Received frame {type(frame).__name__}")
        await super().process_frame(frame, direction)
