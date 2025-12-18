import sounddevice as sd
import json
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
FS = 16000 
THRESHOLD = 0.05
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

import struct

def play_wav(wav_data, alsa_device):
    """Utility to play a single WAV buffer."""
    if not wav_data.startswith(b"RIFF"): return
    temp_filename = f"temp_recv_{int(time.time() * 1000)}.wav"
    try:
        with open(temp_filename, "wb") as f:
            f.write(wav_data)
        cmd = ["aplay", "-q", "-t", "wav"]
        if alsa_device:
            dev = alsa_device.replace("hw:", "plughw:", 1) if alsa_device.startswith("hw:") else alsa_device
            cmd.extend(["-D", dev])
        cmd.append(temp_filename)
        subprocess.call(cmd)
    finally:
        if os.path.exists(temp_filename):
            try: os.remove(temp_filename)
            except: pass

def play_stream(response):
    """
    Streams audio segments and plays them as soon as each WAV is complete.
    """
    print(f"\n{Fore.MAGENTA}[INCOMING]{Fore.RESET} Streaming ", end="", flush=True)
    
    current_part = b""
    expected_size = 0
    is_json = False
    alsa_device = os.environ.get("MESH_ALSA_DEVICE")

    for chunk in response.iter_content(chunk_size=1024): 
        if not chunk: continue
        
        # Check for JSON status
        if not current_part and chunk.lstrip().startswith(b"{"):
            is_json = True
        
        current_part += chunk
        
        if is_json:
            continue
            
        print(f"{Fore.CYAN}•", end="", flush=True)

        # 1. Parse header for size (Seek RIFF in case of leading stream junk)
        if expected_size == 0 and len(current_part) >= 8:
            riff_idx = current_part.find(b"RIFF")
            if riff_idx != -1:
                if riff_idx > 0:
                    current_part = current_part[riff_idx:] # Strip junk
                
                if len(current_part) >= 8:
                    expected_size = struct.unpack("<I", current_part[4:8])[0] + 8
        
        # 2. If we have the full file, play it!
        if expected_size > 0 and len(current_part) >= expected_size:
            wav_to_play = current_part[:expected_size]
            remaining = current_part[expected_size:]
            
            # Start playback (sequential block is fine because server is slow anyway)
            play_wav(wav_to_play, alsa_device)
            
            # Reset for next part in stream
            current_part = remaining
            expected_size = 0
            if current_part and current_part.startswith(b"RIFF"):
                if len(current_part) >= 8:
                    expected_size = struct.unpack("<I", current_part[4:8])[0] + 8
    
    # Final cleanup
    if is_json:
        try:
            status_data = json.loads(current_part)
            status = status_data.get("status", "unknown")
            if status == "no_speech": print(f" {Fore.WHITE}{Style.DIM}[SILENCE]")
            else: print(f" {Fore.YELLOW}[STATUS: {status}]")
        except: print(f" {Fore.RED}[DATA ERROR]")
    else:
        if current_part:
            play_wav(current_part, alsa_device)
        print(f" {Fore.GREEN}[DONE]")

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
                    recording = np.concatenate(audio_buffer, axis=0)
                    recorded_secs = len(recording) / FS
                    print(f" {Fore.YELLOW}[RECORDING DONE]{Fore.RESET} Duration: {recorded_secs:.2f}s", end="", flush=True)
                    start_proc = time.time()
                    
                    if len(recording) > FS * 0.5:
                        # Convert numpy array to WAV bytes in memory
                        wav_io = io.BytesIO()
                        write(wav_io, FS, recording)
                        wav_io.seek(0)
                        
                        proc_duration = time.time() - start_proc
                        print(f" {Fore.CYAN}(Encoded in {proc_duration:.2f}s){Fore.RESET} {Fore.YELLOW}[SENDING]{Fore.RESET}", end="", flush=True)
                        
                        try:
                            # POST Request
                            files = {'audio_file': ('cmd.wav', wav_io, 'audio/wav')}
                            
                            start_send = time.time()
                            with requests.post(SERVER_URL, files=files, stream=True) as r:
                                upload_duration = time.time() - start_send
                                print(f" {Fore.CYAN}(Server Wait: {upload_duration:.2f}s){Fore.RESET}", end="", flush=True)
                                
                                if r.status_code == 200:
                                    first_byte_time = None
                                    
                                    # We wrap response.iter_content to catch the first byte time
                                    def timed_iter(resp):
                                        nonlocal first_byte_time
                                        for c in resp.iter_content(chunk_size=4096):
                                            if first_byte_time is None:
                                                first_byte_time = time.time()
                                            yield c
                                    
                                    # Create a wrapper for play_stream to use our timed iterator
                                    class TimedResponse:
                                        def __init__(self, r): self.r = r
                                        def iter_content(self, **kwargs): return timed_iter(self.r)
                                    
                                    play_stream(TimedResponse(r))
                                    
                                    # Report Latency
                                    total_turnaround = time.time() - start_send
                                    ttfb = (first_byte_time - start_send) if first_byte_time else 0
                                    print(f" {Fore.CYAN}[LATENCY] Round-trip: {total_turnaround:.2f}s (TTFB: {ttfb:.2f}s)")
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
