import torch
import time
from faster_whisper import WhisperModel
import soundfile as sf
import subprocess
import os
import random
import sys
import contextlib
import uuid
import re
import requests
import json
import wave 

from mesh_common.logging import get_logger
from mesh_server import config

logger = get_logger("mesh_voice")

# --- HELPER FUNCTIONS ---

@contextlib.contextmanager
def suppress_output():
    """Redirects stdout and stderr to devnull to suppress library noise."""
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        try:
            sys.stdout = devnull
            sys.stderr = devnull
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

def generate_elevenlabs_audio(text, output_file):
    """
    Generates audio using ElevenLabs API.
    Requests raw PCM (16-bit, 24kHz) and wraps it in a WAV container.
    """
    if not config.ELEVENLABS_API_KEY or not config.ELEVENLABS_VOICE_ID:
        logger.error("ElevenLabs API Key or Voice ID missing.")
        return False

    # Request PCM 24kHz to avoid mp3 decoding issues
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}?output_format=pcm_24000"
    
    headers = {
        "Accept": "audio/pcm",
        "Content-Type": "application/json",
        "xi-api-key": config.ELEVENLABS_API_KEY
    }
    
    data = {
        "text": text,
        "model_id": "eleven_v3",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    try:
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            # Wrap Raw PCM in WAV Container
            with wave.open(output_file, 'wb') as wav_file:
                wav_file.setnchannels(1)     # Mono
                wav_file.setsampwidth(2)     # 16-bit (2 bytes)
                wav_file.setframerate(24000) # 24kHz
                wav_file.writeframes(response.content)
            return True
        else:
            logger.warning(f"ElevenLabs API Error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"ElevenLabs Connection Error: {e}")
        return False

def get_prebaked_sound(category):
    # Resolve sounds directory relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dir = os.path.join(script_dir, "sounds")
    
    if not os.path.exists(search_dir): return None
    files = [f for f in os.listdir(search_dir) if f.startswith(category)]
    if files:
        selected = random.choice(files)
        with open(os.path.join(search_dir, selected), "rb") as f:
            return f.read()
    return None

# --- INITIALIZE ENGINES ---
logger.info("Loading Neural Engines... (GPU)")
device = "cuda" if torch.cuda.is_available() else "cpu"

stt_model = WhisperModel("small", device=device, compute_type="float16")

tts_engine = None
if config.USE_F5_TTS:
    logger.info("Initializing F5-TTS...")
    from f5_tts.api import F5TTS
    tts_engine = F5TTS()
    logger.info("F5-TTS Engine Loaded.")
else:
    logger.info("Initializing XTTS v2...")
    from TTS.api import TTS
    tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    logger.info("XTTS v2 Engine Loaded.")

logger.info(f"MESH API Online on {device.upper()}")

def speak_generator(text_to_speak):
    # Clean text
    clean_text = text_to_speak.replace("[CUE: GREEN]", "").replace("[CUE: FLASHING]", "").replace("[CUE: ON]", "")
    clean_text = clean_text.replace("*", "").replace('"', '').strip()
    
    # If NOT using ElevenLabs, clean tags (ElevenLabs handles them, local TTS doesn't)
    if not config.USE_ELEVENLABS:
        clean_text = re.sub(r'\[.*?\]', '', clean_text)
    
    logger.info(f"M.E.S.H.: {clean_text}")
    
    # Split into sentences for better latency
    sentences = re.split(r'(?<=[.!?]) +', clean_text)
    # Filter out empty or very short strings
    sentences = [s.strip() for s in sentences if len(s.strip()) > 1]
    
    logger.debug(f"Split into {len(sentences)} sentences.")
    
    first_chunk_timer = time.time()
    for i, sentence in enumerate(sentences):
        logger.debug(f"({i+1}/{len(sentences)}) Synthesizing: {sentence}")
        
        req_id = str(uuid.uuid4())[:8]
        temp_wav = f"temp_{req_id}.wav"
        
        try:
            # Fallback / Selection Logic
            use_elevenlabs_success = False

            if config.USE_ELEVENLABS:
                logger.info(f"Generating with ElevenLabs: '{sentence[:20]}...'")
                if generate_elevenlabs_audio(sentence, temp_wav):
                    use_elevenlabs_success = True
                else:
                    logger.warning("ElevenLabs Failed. Falling back to local engine...")
            
            # If ElevenLabs was not used or failed, use local engine
            if not use_elevenlabs_success:
                # Clean tags for local fallback
                local_sentence = re.sub(r'\[.*?\]', '', sentence).strip()
                if not local_sentence: continue # Skip if only tag remained

                if config.USE_F5_TTS:
                    # F5-TTS Logic
                    with suppress_output():
                        wav, sample_rate, spect = tts_engine.infer(
                            ref_file=config.REFERENCE_AUDIO,
                            ref_text="",
                            gen_text=local_sentence,
                            speed=0.7,
                            nfe_step=32,
                            remove_silence=False
                        )
                    sf.write(temp_wav, wav, sample_rate)
                else:
                    # XTTS v2 Logic
                    with suppress_output():
                        tts_engine.tts_to_file(
                            text=local_sentence, 
                            speaker_wav=config.REFERENCE_AUDIO, 
                            language="en", 
                            file_path=temp_wav,
                            speed=1.0
                        )
            
            # Direct Yield (No FX, No SoX)
            if os.path.exists(temp_wav):
                if first_chunk_timer:
                    ttfb = time.time() - first_chunk_timer
                    logger.info(f"[LATENCY] TTS (Time to First Byte): {ttfb:.2f}s")
                    first_chunk_timer = None # Only log once
                
                with open(temp_wav, "rb") as f:
                    yield f.read()
                
                # Cleanup
                os.remove(temp_wav)

        except Exception as e:
            logger.error(f"TTS Error: {e}")
            if os.path.exists(temp_wav): os.remove(temp_wav)

def transcribe(audio_buffer):
    """Wrapper for STT transcription"""
    try:
        start_time = time.time()
        segments, _ = stt_model.transcribe(audio_buffer, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()
        
        # Filter Hallucinations
        text_lower = text.lower().strip(" .,?!")
        if not text_lower: return ""
        
        # Exact match or very close containment for common ghosts
        for phantom in config.PHANTOM_PHRASES:
            if phantom in text_lower:
                logger.debug(f"Ignored phantom phrase: '{text}'")
                return ""

        duration = time.time() - start_time
        logger.info(f"[LATENCY] STT (Whisper): {duration:.2f}s")
        return text
    except Exception as e:
        logger.error(f"STT Error: {e}")
        return ""
