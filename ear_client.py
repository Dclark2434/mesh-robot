import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write
import os
import time
import queue

OUTPUT_FOLDER = r"\\wsl.localhost\Ubuntu\home\dclark\mesh-robot\input_buffer"

FS = 44100 
THRESHOLD = 0.5  # volume sensitivity (Adjust if he can't hear you or hears ghosts)
SILENCE_LIMIT = 1 # seconds of silence to consider the sentence "done"
LOCK_FILE = os.path.join(OUTPUT_FOLDER, "speaking.lock")

print(f"--- M.E.S.H. AUTO-EAR ---")
print(f"Targeting: {OUTPUT_FOLDER}")
print(f"[STATUS] Calibrating background noise... shhh...")

recording = sd.rec(int(2 * FS), samplerate=FS, channels=1)
sd.wait()
noise_floor = np.max(np.abs(recording)) * 1.5
THRESHOLD = max(THRESHOLD, noise_floor)
print(f"[STATUS] Threshold set to {THRESHOLD:.4f}. Listening...")

q = queue.Queue()

def callback(indata, frames, time, status):
    """This is called by the audio thread for every chunk of audio"""
    if status:
        print(status)
    q.put(indata.copy())

# Main Loop
try:
    with sd.InputStream(samplerate=FS, channels=1, callback=callback):
        while True:
            if os.path.exists(LOCK_FILE):
                # If the robot is talking, clear the buffer and wait
                with q.mutex:
                    q.queue.clear()
                time.sleep(0.1)
                continue
            
            # 1. Listen for Trigger Volume
            audio_buffer = []
            silence_counter = 0
            started = False
            
            while True:
                # Get audio chunk
                chunk = q.get()
                volume = np.linalg.norm(chunk) * 10
                
                if not started:
                    # Waiting for voice...
                    if volume > THRESHOLD:
                        print("\n[DETECTED] Recording...", end="", flush=True)
                        started = True
                        audio_buffer.append(chunk)
                else:
                    # Currently Recording...
                    audio_buffer.append(chunk)
                    if volume < THRESHOLD:
                        silence_counter += 1
                    else:
                        silence_counter = 0
                        print(".", end="", flush=True) # visual feedback
                    
                    # Stop if silence persists for X seconds
                    if silence_counter > (SILENCE_LIMIT * (FS / len(chunk))):
                        print(" [SENT]")
                        break
            
            # 2. Save File
            if audio_buffer:
                # Concatenate all chunks
                recording = np.concatenate(audio_buffer, axis=0)
                
                # Check if file is long enough to matter (ignore 0.2s blips)
                if len(recording) > FS * 0.5:
                    timestamp = int(time.time())
                    filename = os.path.join(OUTPUT_FOLDER, f"cmd_{timestamp}.wav")
                    write(filename, FS, recording)

except KeyboardInterrupt:
    print("\nExiting.")