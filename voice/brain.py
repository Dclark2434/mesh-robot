import subprocess
import requests
import json
import time
import random
import os
import sys
import shutil

try:
    import torch
    from TTS.api import TTS
    from faster_whisper import WhisperModel
except ImportError:
    print("Error: You need to install TTS. Run: pip install TTS torch")
    sys.exit(1)

# config
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mesh"  
REFERENCE_AUDIO = "tars_ref.wav" # this is the reference for zero-shot voice
INPUT_FOLDER = "../input_buffer"
WAKE_WORDS = ["mesh", "tars", "computer", "hey"]
ATTENTION_SPAN = 30  # Seconds before he stops listening
last_interaction = 0
is_focused = False

print("\033[93m[SYSTEM] Loading Neural Voice Engine (XTTS v2)... Stand by.\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"

tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
print(f"\033[92m[SYSTEM] Voice Engine Online. Running on {device.upper()}.\033[0m")

stt_model = WhisperModel("small", device=device, compute_type="float16")

print(f"\033[92m[SYSTEM] M.E.S.H. Online on {device.upper()}. Waiting for input...\033[0m")

def execute_command(action, param):
    # SAFETY 1: handle NoneType if LLM sends null
    if param is None:
        param = "unknown"
        
    clean_param = param.lower().strip()
    
    # SAFETY 2: define Valid Hardware Limits (Stop him from walking to "Mars")
    VALID_MOVES = ["forward", "backward", "left", "right", "stop"]
    VALID_SCANS = ["full", "sector", "forward"]

    if action == "walk":
        if clean_param in VALID_MOVES:
            print(f"\033[93m[HARDWARE] Servos engaging... Moving {clean_param.upper()}\033[0m")
        else:
            print(f"\033[91m[HARDWARE WARNING] Invalid Move Parameter: '{param}'. Ignoring.\033[0m")
            
    elif action == "scan":
        if clean_param in VALID_SCANS:
            print(f"\033[93m[HARDWARE] LiDAR spinning up... Scanning {clean_param.upper()}\033[0m")
        else:
            print(f"\033[91m[HARDWARE WARNING] Invalid Scan Parameter: '{param}'. Ignoring.\033[0m")
            
    elif action == "shutdown":
        print("\033[91m[SYSTEM] Kill signal received.\033[0m")
        exit(0)

def speak(text):
    if not text: return
    
    # clean text
    display_text = text
    spoken_text = text.replace("[CUE: GREEN]", "").replace("[CUE: FLASHING]", "").replace("[CUE: ON]", "")
    
    print(f"\033[92mM.E.S.H.:\033[0m {display_text}") 

    try:

        tts_engine.tts_to_file(
            text=spoken_text, 
            speaker_wav=REFERENCE_AUDIO, 
            language="en", 
            file_path="mesh_hq.wav",
            speed=1.0
        )

        # radio effect processing. edit this if you want a crunchier sound.
        sox_command = (
            'sox mesh_hq.wav -b 16 mesh_final.wav '
            'overdrive 10 sinc 60-7000 reverb 15 gain -1'
        )
        subprocess.run(sox_command, shell=True, check=True, stderr=subprocess.DEVNULL)

        play_command = (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -c "
            "\"(New-Object Media.SoundPlayer "
            "'$(wslpath -w mesh_final.wav)').PlaySync()\""
        )
        subprocess.run(play_command, shell=True, check=True)
        
    except Exception as e:
        print(f"Voice Error: {e}")

def think(prompt, context):
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "context": context,
        "stream": False 
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        data = response.json()
        return data['response'], data['context']
    except requests.exceptions.RequestException as e:
        return f"Connection Error: {e}", context

def listen_to_file(filepath):
    try:
        print("\033[90m[Processing Audio...]\033[0m")
        segments, _ = stt_model.transcribe(filepath, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()
        return text
    except Exception as e:
        print(f"Hearing Error: {e}")
        return ""

def main():
    global is_focused, last_interaction
    context = []
    
    if not os.path.exists(INPUT_FOLDER): os.makedirs(INPUT_FOLDER)

    speak("Audio sensors active. Standing by.")

    while True:
        try:
            # 1. STATE CHECK (Timeout Logic)
            if is_focused and (time.time() - last_interaction > ATTENTION_SPAN):
                is_focused = False
                print("\033[90m[TIMEOUT] Returning to Idle Mode.\033[0m")
                # Optional: Play a "power down" beep here

            # 2. CHECK FOR FILES
            audio_files = [f for f in os.listdir(INPUT_FOLDER) if f.endswith('.wav')]
            
            if not audio_files:
                time.sleep(0.1)
                continue

            # Found audio!
            file_path = os.path.join(INPUT_FOLDER, audio_files[0])
            time.sleep(0.2) 
            
            user_input = listen_to_file(file_path)
            os.remove(file_path)
            
            if not user_input: continue

            print(f"\n\033[94mDustin (Voice):\033[0m {user_input}")
            clean_input = user_input.lower().strip()

            # 3. WAKE WORD LOGIC
            if not is_focused:
                if any(word in clean_input for word in WAKE_WORDS):
                    is_focused = True
                    last_interaction = time.time()
                    print("\033[92m[WAKE DETECTED] Focus Acquired.\033[0m")
                    
                    # --- THE FIX: Immediate Acknowledgment ---
                    # We play a sound/voice immediately so you know he heard you
                    speak("Listening.") 
                    # -----------------------------------------
                    
                else:
                    print(f"\033[90m[IGNORED] '{clean_input}'\033[0m")
                    continue
            else:
                last_interaction = time.time()

            # 4. PROCESS
            raw_response, context = think(user_input, context)
            
            try:
                # JSON Parsing
                if "{" in raw_response:
                    json_str = raw_response[raw_response.find('{'):raw_response.rfind('}')+1]
                    data = json.loads(json_str)
                    spoken = data.get("response", "Error.")
                    action = data.get("action", "none")
                    param = data.get("param", "null")
                else:
                    spoken = raw_response
                    action = "none"
                    param = "null"

                speak(spoken)
                if action != "none": execute_command(action, param)

            except json.JSONDecodeError:
                print(f"[PARSE ERROR] {raw_response}")
                speak("I missed that.")

        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()