import sounddevice as sd
import numpy as np
import requests
import io
from scipy.io.wavfile import write
import queue
import time
import subprocess
import os

# --- CONFIGURATION ---
# The IP of your WSL (or Raspberry Pi) Server
SERVER_URL = "http://localhost:8000/listen"  

# Audio Config
FS = 44100 
THRESHOLD = 0.5
SILENCE_LIMIT = 1.0

print(f"--- M.E.S.H. NETWORK CLIENT ---")
print(f"Connecting to: {SERVER_URL}")

# --- PLAYBACK ENGINE ---
def play_stream(response):
    """
    Reads the incoming audio stream from the server and plays it.
    """
    print("\n[INCOMING STREAM] Playing...", end="", flush=True)
    
    chunk_count = 0
    for chunk in response.iter_content(chunk_size=None): 
        if not chunk: break
        
        # Create a temp file in the current working directory
        temp_filename = f"temp_recv_{chunk_count}.wav"
        
        # Get the absolute path to ensure PowerShell finds it
        # This fixes the "FileNotFoundException" regardless of where CMD defaults to
        abs_filepath = os.path.abspath(temp_filename)
        
        with open(temp_filename, "wb") as f:
            f.write(chunk)
            
        # Play using PowerShell with robust flags
        # -NoProfile: Stops the Terminal-Icons errors
        # -ExecutionPolicy Bypass: Ensures script runs
        # -Command: Executes our specific play logic
        cmd = [
            "powershell", 
            "-NoProfile", 
            "-ExecutionPolicy", "Bypass", 
            "-Command", 
            f"(New-Object Media.SoundPlayer '{abs_filepath}').PlaySync()"
        ]
        
        # We use subprocess.call to block until sound finishes
        subprocess.call(cmd)
        
        # Cleanup
        try: os.remove(temp_filename)
        except: pass
        
        print(".", end="", flush=True)
        chunk_count += 1
    
    print(" [DONE]")

# --- RECORDING ENGINE ---
q = queue.Queue()

def callback(indata, frames, time, status):
    if status: print(status)
    q.put(indata.copy())

# --- MAIN LOOP ---
# Auto-Calibrate
print("[STATUS] Calibrating noise floor...")
rec = sd.rec(int(2*FS), samplerate=FS, channels=1)
sd.wait()
noise_floor = np.max(np.abs(rec)) * 1.5
THRESHOLD = max(THRESHOLD, noise_floor)
print(f"[READY] Threshold: {THRESHOLD:.4f}")

try:
    with sd.InputStream(samplerate=FS, channels=1, callback=callback):
        while True:
            # We don't need a lock file anymore!
            # The server simply won't return data until it's done processing.
            # However, to prevent "hearing yourself", we will just pause recording
            # while the play_stream function is running.
            
            audio_buffer = []
            silence_counter = 0
            started = False
            
            while True:
                chunk = q.get()
                volume = np.linalg.norm(chunk) * 10
                
                if not started:
                    if volume > THRESHOLD:
                        print("\n[LISTENING] ...", end="", flush=True)
                        started = True
                        audio_buffer.append(chunk)
                else:
                    audio_buffer.append(chunk)
                    if volume < THRESHOLD:
                        silence_counter += 1
                    else:
                        silence_counter = 0
                    
                    if silence_counter > (SILENCE_LIMIT * (FS / len(chunk))):
                        break
            
            # Send to Server
            if audio_buffer:
                recording = np.concatenate(audio_buffer, axis=0)
                if len(recording) > FS * 0.5:
                    print(" [SENDING]")
                    
                    # Convert numpy array to WAV bytes in memory
                    wav_io = io.BytesIO()
                    write(wav_io, FS, recording)
                    wav_io.seek(0)
                    
                    try:
                        # POST Request
                        files = {'audio_file': ('cmd.wav', wav_io, 'audio/wav')}
                        
                        # Use stream=True to process audio as it arrives!
                        with requests.post(SERVER_URL, files=files, stream=True) as r:
                            if r.status_code == 200:
                                play_stream(r)
                            else:
                                print(f"[ERROR] Server returned {r.status_code}")
                                
                    except Exception as e:
                        print(f"[NETWORK ERROR] {e}")
                        
            # Clear queue after playing to ensure we don't hear the echo
            with q.mutex:
                q.queue.clear()

except KeyboardInterrupt:
    print("\n[EXIT] Connection closed.")