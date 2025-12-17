import sounddevice as sd
import numpy as np
import requests
import io
from scipy.io.wavfile import write
import queue
import time
import subprocess
import os
import sys
from colorama import init, Fore, Style

# Initialize colorama for beautiful console output
init(autoreset=True)

# --- CONFIGURATION ---
# The IP of your Server (RPi or WSL). Set via env var or default to localhost.
SERVER_URL = os.environ.get("MESH_SERVER_URL", "http://localhost:8000/interact")

# Audio Config
FS = 44100 
THRESHOLD = 0.1
SILENCE_LIMIT = 1.0

def print_banner():
    """Prints a beautiful ASCII banner for M.E.S.H."""
    banner = f"""
{Fore.CYAN}{Style.BRIGHT}    __  ___   ______   _____  __  __
   /  |/  /  / ____/  / ___/ / / / /
  / /|_/ /  / __/     \__ \ / /_/ / 
 / /  / /  / /___    ___/ // __  /  
/_/  /_/  /_____/   /____//_/ /_/   
{Fore.WHITE}{Style.DIM}   MOBILE ENGINEERING SUPPORT HEXAPOD
{Style.RESET_ALL}
{Fore.YELLOW}[SYSTEM]{Fore.RESET} Connecting to: {Fore.BLUE}{SERVER_URL}
    """
    print(banner)

def play_stream(response):
    """
    Reads the entire audio stream from the server and plays it using 'aplay'.
    Buffering the whole stream prevents ALSA 'Device Busy' or 'Unknown error 524'.
    """
    print(f"\n{Fore.MAGENTA}[INCOMING]{Fore.RESET} Receiving ", end="", flush=True)
    
    full_audio = b""
    for chunk in response.iter_content(chunk_size=4096): 
        if not chunk: break
        full_audio += chunk
        print(f"{Fore.CYAN}•", end="", flush=True)
    
    if not full_audio:
        print(f" {Fore.RED}[EMPTY]")
        return

    # Create a unique temp file using timestamp to avoid collisions
    temp_filename = f"temp_recv_{int(time.time())}.wav"
    
    try:
        with open(temp_filename, "wb") as f:
            f.write(full_audio)
            
        # Play using aplay (standard Linux audio player)
        # -q: quiet mode
        # -t wav: force wav format
        # -D: specific device (e.g. 'hw:1,0' for USB speakers)
        print(f" {Fore.YELLOW}Playing...", end="", flush=True)
        
        alsa_device = os.environ.get("MESH_ALSA_DEVICE")
        cmd = ["aplay", "-q", "-t", "wav"]
        if alsa_device:
            # Use 'plughw' instead of 'hw' to automatically handle 
            # channel/sample rate conversions (fixes "Channels count non available")
            if alsa_device.startswith("hw:"):
                alsa_device = alsa_device.replace("hw:", "plughw:", 1)
            cmd.extend(["-D", alsa_device])
        cmd.append(temp_filename)
        
        subprocess.call(cmd)
        print(f" {Fore.GREEN}[DONE]")
    except FileNotFoundError:
        print(f"\n{Fore.RED}[ERROR]{Fore.RESET} 'aplay' not found. Please install alsa-utils.")
    except Exception as e:
        print(f"\n{Fore.RED}[ERROR]{Fore.RESET} Playback failed: {e}")
    finally:
        # Cleanup
        if os.path.exists(temp_filename):
            try: os.remove(temp_filename)
            except: pass

# --- RECORDING ENGINE ---
q = queue.Queue()

def callback(indata, frames, time, status):
    if status: 
        print(f"\n{Fore.RED}[AUDIO STATUS]{Fore.RESET} {status}")
    q.put(indata.copy())

def main():
    global THRESHOLD
    print_banner()

    # Auto-Calibrate
    print(f"{Fore.YELLOW}[CALIBRATING]{Fore.RESET} Measuring noise floor... ", end="", flush=True)
    try:
        rec = sd.rec(int(2*FS), samplerate=FS, channels=1)
        sd.wait()
        noise_floor = np.max(np.abs(rec)) * 2.0
        THRESHOLD = max(THRESHOLD, noise_floor)
        print(f"{Fore.GREEN}Ready!{Fore.RESET} (Threshold: {THRESHOLD:.4f})")
    except Exception as e:
        print(f"\n{Fore.RED}[FATAL]{Fore.RESET} Calibration failed: {e}")
        sys.exit(1)

    print(f"{Fore.WHITE}{Style.DIM}Waiting for speech... (Ctrl+C to exit)")

    try:
        with sd.InputStream(samplerate=FS, channels=1, callback=callback):
            while True:
                audio_buffer = []
                silence_counter = 0
                started = False
                
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    volume = np.max(np.abs(chunk))
                    
                    if not started:
                        if volume > THRESHOLD:
                            print(f"\n{Fore.GREEN}[HEARD IT]{Fore.RESET} ", end="", flush=True)
                            started = True
                            audio_buffer.append(chunk)
                    else:
                        audio_buffer.append(chunk)
                        # Print a small bar for every few chunks to avoid console flooding
                        if len(audio_buffer) % 5 == 0:
                            print(f"{Fore.CYAN}▂", end="", flush=True)
                        
                        if volume < THRESHOLD:
                            silence_counter += 1
                        else:
                            silence_counter = 0
                        
                        if silence_counter > (SILENCE_LIMIT * (FS / len(chunk))):
                            break
                
                # Send to Server
                if audio_buffer:
                    print(f" {Fore.YELLOW}[SENDING]{Fore.RESET}", end="", flush=True)
                    recording = np.concatenate(audio_buffer, axis=0)
                    
                    if len(recording) > FS * 0.5:
                        # Convert numpy array to WAV bytes in memory
                        wav_io = io.BytesIO()
                        write(wav_io, FS, recording)
                        wav_io.seek(0)
                        
                        try:
                            # POST Request
                            files = {'audio_file': ('cmd.wav', wav_io, 'audio/wav')}
                            
                            with requests.post(SERVER_URL, files=files, stream=True) as r:
                                if r.status_code == 200:
                                    play_stream(r)
                                else:
                                    print(f"\n{Fore.RED}[ERROR]{Fore.RESET} Server returned {r.status_code}")
                                    
                        except Exception as e:
                            print(f"\n{Fore.RED}[NETWORK ERROR]{Fore.RESET} {e}")
                    else:
                        print(f" {Fore.WHITE}{Style.DIM}Ignored (too short)")
                            
                # Clear queue after playing to ensure we don't hear the echo
                with q.mutex:
                    q.queue.clear()

    except KeyboardInterrupt:
        print(f"\n\n{Fore.YELLOW}[EXIT]{Fore.RESET} Powering down...")
    except Exception as e:
        print(f"\n{Fore.RED}[ERROR]{Fore.RESET} {e}")

if __name__ == "__main__":
    main()
