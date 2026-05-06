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
    logger.info("F5-TTS Engine Loaded.")
    logger.info("F5-TTS Engine Loaded.")
elif config.TTS_ENGINE == "chatterbox":
    logger.info(f"Initializing Chatterbox TTS...")
    
    desired_model = config.CHATTERBOX_MODEL.lower()
    
    if "turbo" in desired_model:
        logger.info("Detected Turbo model configuration.")
        
        # Ensure HF_TOKEN is set for snapshot_download
        if config.HF_TOKEN:
            os.environ["HF_TOKEN"] = config.HF_TOKEN
            
        try:
            # Try current known path
            try:
                from chatterbox.tts_turbo import ChatterboxTurboTTS
            except ImportError:
                # Fallback: try direct import from chatterbox
                from chatterbox import ChatterboxTurboTTS
                
            # ChatterboxTurboTTS has the correct REPO_ID hardcoded ("ResembleAI/chatterbox-turbo")
            tts_engine = ChatterboxTurboTTS.from_pretrained(device)
            logger.info("ChatterboxTurboTTS Engine Loaded.")
        except ImportError as e:
            logger.error(f"Could not import ChatterboxTurboTTS. Error: {e}")
            logger.info("Try running: pip install chatterbox-tts --upgrade")
            tts_engine = None
        except Exception as e:
            logger.error(f"Failed to load Chatterbox Turbo: {e}")
            tts_engine = None
    else:
        # Base Model (Legacy)
        from chatterbox import ChatterboxTTS
        import chatterbox.tts
        
        hf_repo = "ResembleAI/chatterbox"
        hf_repo = "ResembleAI/chatterbox"
        # Removing multilingual logic as repo ID is invalid/unknown
             
        logger.info(f"Patching Chatterbox REPO_ID to: {hf_repo}")
        chatterbox.tts.REPO_ID = hf_repo
        
        try:
            tts_engine = ChatterboxTTS.from_pretrained(device)
            logger.info("Chatterbox Engine (Base) Loaded.")
        except Exception as e:
             logger.error(f"Failed to load Chatterbox: {e}")
             tts_engine = None
else:
    logger.warning(f"Unknown TTS Engine: {config.TTS_ENGINE}. Defaulting to Chatterbox logic (if compatible).")

logger.info(f"MESH API Online on {device.upper()}")

def warmup():
    """Runs a dummy inference to load model/CUDA kernels."""
    if tts_engine:
        logger.info("Warming up TTS Engine...")
        try:
            with suppress_output():
                if config.USE_F5_TTS:
                    # Warmup F5
                    tts_engine.infer(
                        ref_file=config.REFERENCE_AUDIO, ref_text="", gen_text="Warmup.",
                        remove_silence=False
                    )
                elif config.TTS_ENGINE == "chatterbox":
                    # Warmup Chatterbox
                    kwargs = {"temperature": 0.8}
                    if "turbo" not in config.CHATTERBOX_MODEL.lower():
                         kwargs["cfg_weight"] = 0.6
                         kwargs["exaggeration"] = 0.45
                         
                    if os.path.exists(config.REFERENCE_AUDIO):
                         kwargs["audio_prompt_path"] = config.REFERENCE_AUDIO
                         
                    tts_engine.generate("Ready.", **kwargs)
            logger.info("TTS Engine Warmed Up.")
        except Exception as e:
            logger.warning(f"TTS Warmup failed: {e}")

# Trigger warmup if not imported as module (or called explicitly)
# proper place is likely server.py call

def speak_generator(text_to_speak):
    # Clean text
    clean_text = text_to_speak.replace("[CUE: GREEN]", "").replace("[CUE: FLASHING]", "").replace("[CUE: ON]", "")
    clean_text = clean_text.replace("*", "").replace('"', '').strip()
    
    # Mapping for Chatterbox Compatibility
    # Chatterbox expects: [laugh], [chuckle], [sigh], [cough], [clear throat], [groan], [sniff], [gasp], [sush]
    # LLM produces: [laughing], [chuckles], [sighs], etc.
    # NOTE: Base 100m model reads these tags out loud, so we restrict this to Turbo only.
    if config.TTS_ENGINE == "chatterbox" and "turbo" in config.CHATTERBOX_MODEL.lower() and not config.USE_ELEVENLABS:
        tag_map = {
            r"\[laughing\]": "[laugh]",
            r"\[long laugh\]": "[laugh]",
            r"\[chuckles\]": "[chuckle]",
            r"\[giggles\]": "[chuckle]",
            r"\[sighs\]": "[sigh]",
            r"\[exhales\]": "[sigh]",
            r"\[clears throat\]": "[clear throat]",
            r"\[groans\]": "[groan]",
            r"\[gasps\]": "[gasp]",
            r"\[coughs\]": "[cough]"
        }
        
        for p, r in tag_map.items():
            clean_text = re.sub(p, r, clean_text, flags=re.IGNORECASE)
            
        # Strip unsupported tags so they aren't read out loud
        # Supported: laugh, chuckle, sigh, cough, clear throat, groan, sniff, gasp, sush
        # Strip: whispers, shouting, etc.
        # Regex explanation: Match [tag] where tag is NOT in the allowed list
        all_tags = re.findall(r'\[.*?\]', clean_text)
        allowed_tags = ["[laugh]", "[chuckle]", "[sigh]", "[cough]", "[clear throat]", "[groan]", "[sniff]", "[gasp]", "[sush]"]
        
        for tag in all_tags:
            if tag.lower() not in allowed_tags:
                clean_text = clean_text.replace(tag, "")
    
    # If NOT using ElevenLabs OR Chatterbox, clean tags (local fallback generally doesn't handle them)
    # Chatterbox and ElevenLabs both support [laughing] etc. (mapped above for Chatterbox)
    elif not config.USE_ELEVENLABS:
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
                # Clean tags for local fallback (UNLESS it's Chatterbox, which supports them)
                if config.TTS_ENGINE == "chatterbox":
                     local_sentence = sentence
                else:
                     local_sentence = re.sub(r'\[.*?\]', '', sentence).strip()
                
                if not local_sentence and config.TTS_ENGINE != "chatterbox": continue # Skip if only tag remained (legacy)

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
                    sf.write(temp_wav, wav, sample_rate, subtype='PCM_16')
                else:
                    # Chatterbox Logic
                    with suppress_output():
                        # Chatterbox usage
                        # Default Params
                        kwargs = {
                            "temperature": 0.8,
                        }
                        
                        # TURBO Tuning
                        if "turbo" in config.CHATTERBOX_MODEL.lower():
                            # Turbo typically warns about these, but internal code inspection shows
                            # it *does* use exaggeration for emotion_adv in T3Cond.
                            # We disable internal loudness normalization (-27 LUFS) to preserve reference dynamics.
                            kwargs["norm_loudness"] = False
                            kwargs["exaggeration"] = 0.35 # Fix "depressed" tone
                        else:
                            # Base Model Logic
                            kwargs["cfg_weight"] = 0.6
                            kwargs["exaggeration"] = 0.45
                        
                        if os.path.exists(config.REFERENCE_AUDIO):
                            # Correct argument is audio_prompt_path
                            logger.debug(f"Using Reference Audio: {config.REFERENCE_AUDIO}")
                            kwargs["audio_prompt_path"] = config.REFERENCE_AUDIO
                        else:
                            logger.warning(f"Reference Audio NOT FOUND at: {config.REFERENCE_AUDIO}")
                        
                        # Generate returns a tensor (1, samples)
                        import logging as pylogging # Avoid conflict with local logger var
                        cb_logger = pylogging.getLogger("chatterbox.tts_turbo")
                        old_level = cb_logger.level
                        cb_logger.setLevel(pylogging.ERROR)
                        try:
                            audio_tensor = tts_engine.generate(local_sentence, **kwargs)
                        finally:
                            cb_logger.setLevel(old_level)
                        
                        # Convert to numpy
                        if hasattr(audio_tensor, "cpu"):
                            wav = audio_tensor.squeeze().cpu().numpy()
                        else:
                            wav = audio_tensor # Fallback
                        
                        # POST-PROCESS: Volume Boost
                        # Normalization is now disabled for Turbo, so native volume should be fine.
                        # No extra gain needed.
                        
                        sample_rate = 24000 # Chatterbox default
                    
                    sf.write(temp_wav, wav, sample_rate, subtype='PCM_16')
            
            # Direct Yield (No FX, No SoX)
            try:
                if os.path.exists(temp_wav):
                    if first_chunk_timer:
                        ttfb = time.time() - first_chunk_timer
                        logger.info(f"[LATENCY] TTS (Time to First Byte): {ttfb:.2f}s")
                        first_chunk_timer = None # Only log once
                    
                    with open(temp_wav, "rb") as f:
                        yield f.read()
            finally:
                # Cleanup MUST happen even if generator is closed by client
                if os.path.exists(temp_wav):
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
