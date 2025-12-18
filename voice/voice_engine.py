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
import config

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

def process_audio_fx(input_file):
    output_file = input_file.replace(".wav", "_fx.wav")
    cmd = (
        f'sox {input_file} -b 16 {output_file} '
        'overdrive 5 sinc 60-7000 reverb 10 50 20 gain -2'
    )
    try:
        subprocess.run(cmd, shell=True, check=True, stderr=subprocess.DEVNULL)
        with open(output_file, "rb") as f:
            audio_data = f.read()
        return audio_data
    except Exception as e:
        print(f"[FX ERROR] {e}")
        return None
    finally:
        if os.path.exists(input_file): os.remove(input_file)
        if os.path.exists(output_file): os.remove(output_file)

def get_prebaked_sound(category):
    search_dir = "sounds"
    if not os.path.exists(search_dir): return None
    files = [f for f in os.listdir(search_dir) if f.startswith(category)]
    if files:
        selected = random.choice(files)
        with open(os.path.join(search_dir, selected), "rb") as f:
            return f.read()
    return None

# --- INITIALIZE ENGINES ---
print("\033[93m[SYSTEM] Loading Neural Engines... (GPU)\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"

stt_model = WhisperModel("small", device=device, compute_type="float16")

tts_engine = None
if config.USE_F5_TTS:
    print("\033[93m[SYSTEM] Initializing F5-TTS...\033[0m")
    from f5_tts.api import F5TTS
    tts_engine = F5TTS()
    print("\033[92m[SYSTEM] F5-TTS Engine Loaded.\033[0m")
else:
    print("\033[93m[SYSTEM] Initializing XTTS v2...\033[0m")
    from TTS.api import TTS
    tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    print("\033[92m[SYSTEM] XTTS v2 Engine Loaded.\033[0m")

print(f"\033[92m[SYSTEM] MESH API Online on {device.upper()}\033[0m")

def speak_generator(text_to_speak):
    # Clean text
    clean_text = text_to_speak.replace("[CUE: GREEN]", "").replace("[CUE: FLASHING]", "").replace("[CUE: ON]", "")
    clean_text = clean_text.replace("*", "").replace('"', '').strip()
    
    print(f"\033[92mM.E.S.H.:\033[0m {clean_text}")
    
    # Split into sentences for better latency
    sentences = re.split(r'(?<=[.!?]) +', clean_text)
    # Filter out empty or very short strings
    sentences = [s.strip() for s in sentences if len(s.strip()) > 1]
    
    print(f"\033[93m[TTS] Split into {len(sentences)} sentences.\033[0m")
    
    first_chunk_timer = time.time()
    for i, sentence in enumerate(sentences):
        print(f"\033[93m[TTS] ({i+1}/{len(sentences)}) Synthesizing: {sentence}\033[0m")
        
        req_id = str(uuid.uuid4())[:8]
        temp_wav = f"temp_{req_id}.wav"
        
        try:
            if config.USE_F5_TTS:
                # F5-TTS Logic
                with suppress_output():
                    wav, sample_rate, spect = tts_engine.infer(
                        ref_file=config.REFERENCE_AUDIO,
                        ref_text="",
                        gen_text=sentence,
                        speed=0.7,
                        nfe_step=32,
                        remove_silence=False
                    )
                sf.write(temp_wav, wav, sample_rate)
            else:
                # XTTS v2 Logic
                with suppress_output():
                    tts_engine.tts_to_file(
                        text=sentence, 
                        speaker_wav=config.REFERENCE_AUDIO, 
                        language="en", 
                        file_path=temp_wav,
                        speed=1.0
                    )
            processed_bytes = process_audio_fx(temp_wav)
            if processed_bytes:
                if first_chunk_timer:
                    ttfb = time.time() - first_chunk_timer
                    print(f"\033[96m[LATENCY] TTS (Time to First Byte): {ttfb:.2f}s\033[0m")
                    first_chunk_timer = None # Only log once
                yield processed_bytes
        except Exception as e:
            print(f"[TTS Error] {e}")

def transcribe(audio_buffer):
    """Wrapper for STT transcription"""
    try:
        start_time = time.time()
        segments, _ = stt_model.transcribe(audio_buffer, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()
        duration = time.time() - start_time
        print(f"\033[96m[LATENCY] STT (Whisper): {duration:.2f}s\033[0m")
        return text
    except Exception as e:
        print(f"[STT Error] {e}")
        return ""
