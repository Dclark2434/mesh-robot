import torch
from TTS.api import TTS
from faster_whisper import WhisperModel
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
import uvicorn
import io
import requests
import json
import subprocess
import os
import re
import threading
import queue

# --- CONFIG ---
app = FastAPI()
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mesh"
REFERENCE_AUDIO = "tars_ref.wav"
SPEAKER_ID = 613 

# --- LOAD ENGINES (Global) ---
print("[SYSTEM] Loading Neural Engines...")
device = "cuda" if torch.cuda.is_available() else "cpu"

# 1. Hearing
stt_model = WhisperModel("small", device=device, compute_type="float16")

# 2. Speaking
tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

print(f"[SYSTEM] M.E.S.H. Server Online on {device.upper()}")

# --- LOGIC ---

def think(prompt):
    """Simple blocking call to Ollama"""
    payload = {
        "model": MODEL_NAME, 
        "prompt": prompt, 
        "stream": False
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        return response.json()['response']
    except:
        return "Error connecting to Brain."

def generate_audio_stream(text):
    """
    Generator function that yields WAV bytes.
    This allows the client to play 'chunk 1' while we generate 'chunk 2'.
    """
    # Clean text
    clean_text = text.replace("[CUE: GREEN]", "").replace("*", "").strip()
    print(f"\033[92mM.E.S.H.:\033[0m {clean_text}")

    # Split sentences
    sentences = re.split(r'(?<=[.!?]) +', clean_text)
    
    for i, sentence in enumerate(sentences):
        if len(sentence) < 2: continue
        
        # We process in RAM (BytesIO) instead of Disk
        # NOTE: Coqui XTTS creates temp files internally, so we still use a temp path
        # but we handle the cleanup instantly.
        temp_wav = f"temp_stream_{i}.wav"
        processed_wav = f"proc_stream_{i}.wav"
        
        try:
            # 1. Generate
            tts_engine.tts_to_file(
                text=sentence, 
                speaker_wav=REFERENCE_AUDIO, 
                language="en", 
                file_path=temp_wav,
                speed=1.0
            )
            
            # 2. FX (SoX)
            subprocess.run(
                f'sox {temp_wav} -b 16 {processed_wav} overdrive 5 sinc 60-7000 reverb 10 50 20 gain -2',
                shell=True, check=True, stderr=subprocess.DEVNULL
            )
            
            # 3. Read bytes and yield to network
            with open(processed_wav, "rb") as f:
                yield f.read()
                
        finally:
            # Cleanup
            if os.path.exists(temp_wav): os.remove(temp_wav)
            if os.path.exists(processed_wav): os.remove(processed_wav)

# --- API ENDPOINTS ---

@app.post("/interact")
async def interact(audio: UploadFile = File(...)):
    """
    1. Receive Audio
    2. Transcribe (Whisper)
    3. Think (Ollama)
    4. Stream Audio Back (XTTS)
    """
    # 1. Read Audio into RAM
    audio_bytes = await audio.read()
    
    # Whisper expects a file path or file-like object. 
    # We wrap bytes in BytesIO.
    audio_file = io.BytesIO(audio_bytes)
    
    # 2. Transcribe
    print("\n[HEARING] Processing...")
    segments, _ = stt_model.transcribe(audio_file, beam_size=5)
    user_text = " ".join([segment.text for segment in segments]).strip()
    
    if not user_text:
        return {"status": "no_speech"}
        
    print(f"\033[94mUser:\033[0m {user_text}")
    
    # 3. Think
    # (Optional: Wake Word logic would go here if you send raw audio stream)
    raw_response = think(user_text)
    
    # Extract JSON/Text logic (Simplified for brevity)
    if "{" in raw_response:
        try:
            json_str = raw_response[raw_response.find('{'):raw_response.rfind('}')+1]
            data = json.loads(json_str)
            spoken_text = data.get("response", "Error.")
        except:
            spoken_text = raw_response
    else:
        spoken_text = raw_response

    # 4. Stream Audio Return
    return StreamingResponse(
        generate_audio_stream(spoken_text), 
        media_type="audio/wav"
    )

if __name__ == "__main__":
    # Run the server on 0.0.0.0 so external devices (Pi) can hit it
    uvicorn.run(app, host="0.0.0.0", port=8000)