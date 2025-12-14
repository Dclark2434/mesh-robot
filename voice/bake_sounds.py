import torch
from TTS.api import TTS
import subprocess
import os

# --- CONFIG ---
REFERENCE_AUDIO = "tars_ref.wav"
OUTPUT_DIR = "sounds"

# The Variation Matrix
# Key = The category
# Value = List of phrases to record for that category
PHRASES = {
    "ack": [
        "Hang on a sec,",
        "one sec",
        "let me think about that",
        "what?",
        "oh",
        "uh"
    ],
    "processing": [
        "Processing.",
        "Calculating.",
        "Running the numbers.",
        "One moment."
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
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

# --- BAKE LOOP ---
for category, variations in PHRASES.items():
    for index, text in enumerate(variations):
        filename = f"{category}_{index}"
        print(f"Baking: {filename} -> '{text}'")
        
        # 1. Generate Raw
        temp_file = "temp_bake.wav"
        tts.tts_to_file(
            text=text, 
            speaker_wav=REFERENCE_AUDIO, 
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

# Cleanup
if os.path.exists("temp_bake.wav"): os.remove("temp_bake.wav")
print("\n[SUCCESS] Soundboard generated.")