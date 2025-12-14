# voice/tts_engine.py
import torch
import os
from TTS.api import TTS

# Load model ONCE when this file is imported
print("Initializing MESH Voice System (XTTS v2)...")
device = "cuda" if torch.cuda.is_available() else "cpu"
tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
REF_AUDIO = os.path.join(os.path.dirname(__file__), "tars_ref.wav")

print(f"MESH Voice System Online on {device.upper()}")

def speak(text, output_filename="output.wav"):
    """Generates audio from text and saves it."""
    
    # Check if reference audio exists
    if not os.path.exists(REF_AUDIO):
        print(f"ERROR: Reference audio not found at {REF_AUDIO}")
        return

    print(f"MESH Speaking: {text}")
    
    tts_engine.tts_to_file(
        text=text, 
        speaker_wav=REF_AUDIO, 
        language="en", 
        file_path=output_filename,
        speed=1.0
    )
    
    print(f"Audio saved to {output_filename}")