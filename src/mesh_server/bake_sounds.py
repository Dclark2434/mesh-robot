import torch
import sys
import subprocess
import os
import soundfile as sf
import contextlib
from mesh_server import config

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

# --- CONFIG ---
# Get the absolute path of the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "sounds")

# The Variation Matrix
# Key = The category
# Value = List of phrases to record for that category
PHRASES = {
    "ack": [
        "Yeah?", "I'm here.", "Go ahead.", "Listening.", "What's on your mind?",
        "Hearing you.", "Go for it.", "I'm all ears.", "Shoot.", "Speak up.",
        "Yep?", "Go.", "Waiting.", "Present.", "You have my attention.",
        "Hmh?", "What?", "Ready when you are.", "Start talking.", "I read you.", "I'm listening."
    ],
    "processing": [
        "One second.", "Let's see...",
        "Hang on.", "Working on it.", "Stand by.",
        "Processing that.", "Give me a beat.", "Uhhh...", "One moment.",
        "Looking into it.", "Hmmm...", "What? Oh.", "Just a sec.",
        "Digestive pause. Hang on."
    ],
    "boot": [
        "Mesh System Online.",
        "Systems initialization complete.",
        "Boot sequence successful.",
        "I'm alive! Hahaha"
    ],
    "shutdown": [
        "Powering down.",
        "Entering sleep mode.",
        "Goodnight."
    ]
}

# --- SETUP ---
if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

print("[SYSTEM] Loading Engine for Baking...")
device = "cuda" if torch.cuda.is_available() else "cpu"

tts_engine = None
USE_F5 = config.USE_F5_TTS # Use the central config

if USE_F5:
    print("\033[93m[SYSTEM] Initializing F5-TTS...\033[0m")
    from f5_tts.api import F5TTS
    tts_engine = F5TTS()
    print("\033[92m[SYSTEM] F5-TTS Engine Loaded.\033[0m")
else:
    print("\033[93m[SYSTEM] Initializing XTTS v2...\033[0m")
    from TTS.api import TTS
    tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    print("\033[92m[SYSTEM] XTTS v2 Engine Loaded.\033[0m")

# --- BAKE LOOP ---
for category, variations in PHRASES.items():
    for index, text in enumerate(variations):
        filename = f"{category}_{index}"
        print(f"Baking: {filename} -> '{text}'")
        
        # 1. Generate Raw
        ref_audio = os.path.join(os.path.dirname(__file__), "reference.wav")
        temp_file = os.path.join(SCRIPT_DIR, "temp_bake.wav")
        
        try:
            if USE_F5:
                 with suppress_output():
                    wav, sample_rate, spect = tts_engine.infer(
                        ref_file=ref_audio,
                        ref_text="",
                        gen_text=text,
                        speed=0.3,
                        nfe_step=32,
                        remove_silence=False
                    )
                    sf.write(temp_file, wav, sample_rate)
            else:
                 with suppress_output():
                    tts_engine.tts_to_file(
                        text=text, 
                        speaker_wav=config.REFERENCE_AUDIO, 
                        language="en", 
                        file_path=temp_file,
                        speed=1.0
                    )
            
            # 2. Apply Radio Static
            final_file = os.path.join(OUTPUT_DIR, f"{filename}.wav")
            sox_cmd = (
                f'sox {temp_file} -b 16 {final_file} '
                'overdrive 3 sinc 60-7000 reverb 5 gain -1'
            )
            subprocess.run(sox_cmd, shell=True, check=True, stderr=subprocess.DEVNULL)
            
        except Exception as e:
            print(f"[ERROR] Failed to bake {filename}: {e}")

# Cleanup
temp_bake_path = os.path.join(SCRIPT_DIR, "temp_bake.wav")
if os.path.exists(temp_bake_path): os.remove(temp_bake_path)
print("\n[SUCCESS] Soundboard generated.")