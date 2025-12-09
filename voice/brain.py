import subprocess
import requests
import json
import time
import random
import os
import sys

try:
    import torch
    from TTS.api import TTS
except ImportError:
    print("Error: You need to install TTS. Run: pip install TTS torch")
    sys.exit(1)

# config
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mesh"  
REFERENCE_AUDIO = "tars_ref.wav" # this is the reference for zero-shot voice

print("\033[93m[SYSTEM] Loading Neural Voice Engine (XTTS v2)... Stand by.\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"
tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
print(f"\033[92m[SYSTEM] Voice Engine Online. Running on {device.upper()}.\033[0m")


def execute_command(action, param):
    if action == "walk":
        print(f"\033[93m[HARDWARE] Servos engaging... Moving {param.upper()}\033[0m")
    elif action == "scan":
        print(f"\033[93m[HARDWARE] LiDAR spinning up... Scanning {param.upper()}\033[0m")
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

def main():
    context = []
    print("--- M.E.S.H. READY ---")

    while True:
        try:
            user_input = input("\n\033[94mDustin:\033[0m ") 
            if user_input.lower() in ["exit", "quit"]: break
            
            raw_response, context = think(user_input, context)
            
            try:
                if "{" in raw_response:
                    json_str = raw_response[raw_response.find('{'):raw_response.rfind('}')+1]
                    data = json.loads(json_str)
                    spoken_text = data.get("response", "Error.")
                    action = data.get("action", "none")
                    param = data.get("param", "null")
                else:
                    spoken_text = raw_response
                    action = "none"
                    param = "null"

                speak(spoken_text)
                
                if action != "none":
                    execute_command(action, param)

            except json.JSONDecodeError:
                print(f"[PARSE ERROR] {raw_response}")
                speak("I am having trouble processing that.")

        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()