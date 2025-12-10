import subprocess
import requests
import json
import time
import random
import os
import sys
import shutil
import glob
import threading
import queue
import re

try:
    import torch
    from TTS.api import TTS
    from faster_whisper import WhisperModel
except ImportError:
    print("Error: You need to install TTS. Run: pip install TTS torch faster-whisper")
    sys.exit(1)

# --- CONFIGURATION ---
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mesh"  
REFERENCE_AUDIO = "tars_ref.wav"
INPUT_FOLDER = "../input_buffer" # Ensure this matches your Ear Client path
WAKE_WORDS = ["hey mesh", "hey, mesh", "mesh"]
ATTENTION_SPAN = 60
last_interaction = 0
is_focused = False

# --- LOCK FILE CONFIG ---
# This file signals the Ear Client to stop recording
LOCK_FILE_PATH = os.path.join(INPUT_FOLDER, "speaking.lock")

# --- INITIALIZE ENGINES ---
print("\033[93m[SYSTEM] Loading Neural Voice Engine (XTTS v2)... Stand by.\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"

tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
print(f"\033[92m[SYSTEM] Voice Engine Online. Running on {device.upper()}.\033[0m")

stt_model = WhisperModel("small", device=device, compute_type="float16")
print(f"\033[92m[SYSTEM] M.E.S.H. Online on {device.upper()}. Waiting for input...\033[0m")

# --- HELPER FUNCTIONS ---

def set_speaking_lock(state):
    """Creates or destroys the lock file to mute the ears"""
    try:
        if state:
            with open(LOCK_FILE_PATH, "w") as f:
                f.write("LOCKED")
        else:
            if os.path.exists(LOCK_FILE_PATH):
                os.remove(LOCK_FILE_PATH)
    except Exception as e:
        print(f"[LOCK ERROR] {e}")

def execute_command(action, param):
    if param is None: param = "unknown"
    clean_param = param.lower().strip()
    
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

def play_sound(category):
    """Plays a RANDOM pre-baked file"""
    search_pattern = os.path.join("sounds", f"{category}_*.wav")
    files = glob.glob(search_pattern)
    
    if not files:
        print(f"[ERROR] No sound files found for category: {category}")
        return

    chosen_file = random.choice(files)
    linux_path = os.path.abspath(chosen_file)
    
    # LOCK ON
    set_speaking_lock(True)
    
    command = (
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -c "
        "\"(New-Object Media.SoundPlayer "
        f"'$(wslpath -w {linux_path})').PlaySync()\""
    )
    subprocess.run(command, shell=True)
    
    # LOCK OFF
    set_speaking_lock(False)

def speak(text):
    if not text: return
    
    clean_text = text.replace("[CUE: GREEN]", "").replace("[CUE: FLASHING]", "").replace("[CUE: ON]", "")
    clean_text = clean_text.replace("*", "").replace('"', '').strip()
    print(f"\033[92mM.E.S.H.:\033[0m {clean_text}") 

    sentences = re.split(r'(?<=[.!?]) +', clean_text)
    sentences = [s for s in sentences if len(s.strip()) > 2]

    if not sentences: return

    audio_queue = queue.Queue()

    # --- PRODUCER ---
    def generator_worker():
        for i, sentence in enumerate(sentences):
            raw_file = f"mesh_raw_{i}.wav"
            final_file = f"mesh_final_{i}.wav"
            
            try:
                tts_engine.tts_to_file(
                    text=sentence, 
                    speaker_wav=REFERENCE_AUDIO, 
                    language="en", 
                    file_path=raw_file,
                    speed=1.0 
                )
                subprocess.run(
                    f'sox {raw_file} -b 16 {final_file} overdrive 3 sinc 60-7000 reverb 5 gain -1',
                    shell=True, check=True, stderr=subprocess.DEVNULL
                )
                audio_queue.put(final_file)
                if os.path.exists(raw_file): os.remove(raw_file)
            except Exception as e:
                print(f"[Generator Error] chunk {i}: {e}")
        audio_queue.put(None)

    # --- START GENERATOR ---
    gen_thread = threading.Thread(target=generator_worker)
    gen_thread.start()

    # --- CONSUMER (PLAYER) ---
    while True:
        try:
            file_path = audio_queue.get()
            if file_path is None: break
            
            # LOCK ON
            set_speaking_lock(True)
            
            subprocess.run(
                f"powershell.exe -NoProfile -ExecutionPolicy Bypass -c \"(New-Object Media.SoundPlayer '$(wslpath -w {file_path})').PlaySync()\"",
                shell=True, check=True
            )
            
            # LOCK OFF
            set_speaking_lock(False)
            
            if os.path.exists(file_path): os.remove(file_path)
            
        except Exception as e:
            print(f"[Player Error]: {e}")
            set_speaking_lock(False) # Safety unlock
            break

    gen_thread.join()

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

# --- MAIN LOOP ---
def main():
    global is_focused, last_interaction
    context = []
    
    if not os.path.exists(INPUT_FOLDER): os.makedirs(INPUT_FOLDER)
    
    # Ensure lock is cleared on startup
    set_speaking_lock(False)

    play_sound("boot") 
    print("M.E.S.H. Ready.")

    while True:
        try:
            # 1. STATE CHECK
            if is_focused and (time.time() - last_interaction > ATTENTION_SPAN):
                is_focused = False
                print("\033[90m[TIMEOUT] Returning to Idle Mode.\033[0m")
                # Optional: play_sound("shutdown")

            # 2. CHECK FOR FILES
            audio_files = [f for f in os.listdir(INPUT_FOLDER) if f.endswith('.wav')]
            
            if not audio_files:
                time.sleep(0.1)
                continue

            file_path = os.path.join(INPUT_FOLDER, audio_files[0])
            time.sleep(0.2)
            
            user_input = listen_to_file(file_path)
            os.remove(file_path)
            
            if not user_input: continue

            print(f"\n\033[94mDustin (Voice):\033[0m {user_input}")
            clean_input = user_input.lower().strip()

            # 3. WAKE WORD LOGIC (Fixed Split Logic)
            if not is_focused:
                trigger_word = next((w for w in WAKE_WORDS if w in clean_input), None)
                
                if trigger_word:
                    is_focused = True
                    last_interaction = time.time()
                    print(f"\033[92m[WAKE DETECTED] Trigger: '{trigger_word}'\033[0m")
                    
                    play_sound("ack")
                    
                    # Split logic: "Hey Mesh [Command]" vs "Hey Mesh"
                    parts = clean_input.partition(trigger_word)
                    remaining_command = parts[2].strip(" .,?!")
                    
                    if len(remaining_command) < 2:
                        print("\033[90m[WAITING] Awaiting command...\033[0m")
                        continue 
                    else:
                        print(f"\033[90m[FAST TRACK] Command: '{remaining_command}'\033[0m")
                        user_input = remaining_command
                else:
                    print(f"\033[90m[IGNORED] '{clean_input}'\033[0m")
                    continue
            else:
                last_interaction = time.time()

            # 4. PROCESS
            # Check for hardware killswitch first
            if "shut down" in user_input.lower() or "power off" in user_input.lower():
                 play_sound("shutdown")
                 print("\033[91m[SYSTEM] Kill signal received.\033[0m")
                 exit(0)

            raw_response, context = think(user_input, context)
            
            try:
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