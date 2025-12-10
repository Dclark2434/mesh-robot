import torch
from TTS.api import TTS
from faster_whisper import WhisperModel
from fastapi import FastAPI, UploadFile, File, Response
from fastapi.responses import StreamingResponse
import uvicorn
import io
import requests
import json
import subprocess
import os
import re
import uuid
import shutil

# --- CONFIGURATION ---
app = FastAPI()
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mesh"
REFERENCE_AUDIO = "tars_ref.wav"
WAKE_WORDS = ["hey mesh", "hey, mesh", "mesh"]

# --- INITIALIZE ENGINES ---
print("\033[93m[SYSTEM] Loading Neural Engines... (GPU)\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"

# 1. Hearing (Whisper)
stt_model = WhisperModel("small", device=device, compute_type="float16")

# 2. Speaking (XTTS)
tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

print(f"\033[92m[SYSTEM] M.E.S.H. API Online on {device.upper()}\033[0m")

# --- HELPER FUNCTIONS ---

def process_audio_fx(input_file):
    """
    Applies the 'Interstellar Radio' effects using SoX.
    Returns the binary data of the processed WAV.
    """
    output_file = input_file.replace(".wav", "_fx.wav")
    
    # The Classic TARS Filter Chain
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
        # Cleanup temp files immediately
        if os.path.exists(input_file): os.remove(input_file)
        if os.path.exists(output_file): os.remove(output_file)

def think(prompt, context=[]):
    """Query Ollama"""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "context": context,
        "stream": False
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        return response.json() # Returns dict with 'response' and 'context'
    except Exception as e:
        print(f"[BRAIN ERROR] {e}")
        return {"response": "Connection lost.", "context": []}

def get_prebaked_sound(category):
    """Fetches bytes of a pre-baked sound (ack/boot)"""
    # Simple logic: grab the first one found or random
    # In a real API, we might cache these in RAM
    search_dir = "sounds"
    files = [f for f in os.listdir(search_dir) if f.startswith(category)]
    if files:
        import random
        selected = random.choice(files)
        with open(os.path.join(search_dir, selected), "rb") as f:
            return f.read()
    return None

# --- STREAM GENERATOR ---

def interaction_generator(user_text):
    """
    This is the core logic loop converted into a Generator.
    It yields chunks of audio bytes as they are created.
    """
    # 1. WAKE WORD SPLITTING LOGIC
    clean_input = user_text.lower().strip()
    trigger_word = next((w for w in WAKE_WORDS if w in clean_input), None)
    
    final_prompt = user_text
    
    if trigger_word:
        print(f"\033[92m[TRIGGER] {trigger_word}\033[0m")
        # Split: "Hey Mesh status report" -> ["hey ", "mesh", " status report"]
        parts = clean_input.partition(trigger_word)
        remaining_command = parts[2].strip(" .,?!")
        
        # Immediate ACK (Yield pre-baked sound bytes)
        ack_bytes = get_prebaked_sound("ack")
        if ack_bytes: yield ack_bytes
        
        if len(remaining_command) < 2:
            # User only said "Hey Mesh". We are done.
            return 
        else:
            # User sent a command immediately. Pass it to LLM.
            final_prompt = remaining_command

    print(f"\033[94mUser:\033[0m {final_prompt}")

    # 2. THINK (Ollama)
    # Note: In a stateless API, context management is tricky. 
    # For V1, we are stateless (Amnesia Mode). V2 can accept context in the POST request.
    thought_data = think(final_prompt)
    raw_response = thought_data['response']
    
    # 3. PARSE JSON / COMMANDS
    spoken_text = raw_response
    hardware_command = "none"
    hardware_param = "null"

    if "{" in raw_response:
        try:
            # Regex extraction to find the JSON object
            json_match = re.search(r"(\{.*\})", raw_response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
                spoken_text = data.get("response", "Error.")
                hardware_command = data.get("action", "none")
                hardware_param = data.get("param", "null")
        except:
            print(f"[PARSE ERROR] {raw_response}")

    # Log the command (Server Side)
    if hardware_command != "none":
        print(f"\033[93m[COMMAND] {hardware_command} -> {hardware_param}\033[0m")
        pass

    # 4. SPEAK (Pipeline)
    clean_text = spoken_text.replace("[CUE: GREEN]", "").replace("*", "").strip()
    print(f"\033[92mM.E.S.H.:\033[0m {clean_text}")
    
    sentences = re.split(r'(?<=[.!?]) +', clean_text)
    
    for i, sentence in enumerate(sentences):
        if len(sentence) < 2: continue
        
        # Unique temp file for this request chunk
        req_id = str(uuid.uuid4())[:8]
        temp_wav = f"temp_{req_id}.wav"
        
        try:
            tts_engine.tts_to_file(
                text=sentence, 
                speaker_wav=REFERENCE_AUDIO, 
                language="en", 
                file_path=temp_wav,
                speed=1.0
            )
            
            # Process & Yield
            processed_bytes = process_audio_fx(temp_wav)
            if processed_bytes:
                yield processed_bytes
                
        except Exception as e:
            print(f"[TTS Error] {e}")

# --- API ENDPOINTS ---

@app.post("/listen")
async def listen_endpoint(audio_file: UploadFile = File(...), response: Response = None):
    """
    Endpoint: Accepts WAV file -> Returns Audio Stream
    """
    # 1. Load Audio to RAM
    audio_bytes = await audio_file.read()
    audio_buffer = io.BytesIO(audio_bytes)
    
    # 2. Transcribe
    try:
        segments, _ = stt_model.transcribe(audio_buffer, beam_size=5)
        user_text = " ".join([segment.text for segment in segments]).strip()
    except Exception as e:
        print(f"[STT Error] {e}")
        return {"error": "Transcription failed"}

    if not user_text:
        return {"status": "no_speech"}


    return StreamingResponse(
        interaction_generator(user_text),
        media_type="audio/wav"
    )

if __name__ == "__main__":
    # Host 0.0.0.0 allows the Raspberry Pi to connect via LAN IP
    uvicorn.run(app, host="0.0.0.0", port=8000)