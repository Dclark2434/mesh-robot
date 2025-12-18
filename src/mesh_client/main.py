import sounddevice as sd
import json
import numpy as np
import requests
import io
import queue
import time
import subprocess
import os
import sys
import struct
from scipy.io.wavfile import write
from typing import Optional, Generator

# Add src to path for internal imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mesh_common.logging import get_logger
from mesh_common.config import SAMPLE_RATE, CHANNELS, DEFAULT_SERVER_PORT

logger = get_logger("mesh_client")

# --- CONFIGURATION ---
SERVER_URL = os.environ.get("MESH_SERVER_URL", f"http://localhost:{DEFAULT_SERVER_PORT}/interact")
THRESHOLD = float(os.environ.get("MESH_THRESHOLD", 0.05))
SILENCE_LIMIT = float(os.environ.get("MESH_SILENCE_LIMIT", 1.0))
ALSA_DEVICE = os.environ.get("MESH_ALSA_DEVICE")

def print_banner():
    from colorama import Fore, Style
    banner = f"""
{Fore.CYAN}{Style.BRIGHT}    __  ___   ______   _____  __  __
   /  |/  /  / ____/  / ___/ / / / /
  / /|_/ /  / __/     \\__ \\ / /_/ / 
 / /  / /  / /___    ___/ // __  /  
/_/  /_/  /_____/   /____//_/ /_/   
{Fore.WHITE}{Style.DIM}   MOBILE ENGINEERING SUPPORT HEXAPOD
{Style.RESET_ALL}
{Fore.YELLOW}[SYSTEM]{Fore.RESET} Connecting to: {Fore.BLUE}{SERVER_URL}
    """
    print(banner)

# --- PLAYBACK ENGINES ---

def play_wav_windows(wav_data: bytes):
    """Utility to play a single WAV buffer on Windows via PowerShell."""
    if not wav_data.startswith(b"RIFF"): return
    temp_filename = f"temp_recv_{int(time.time() * 1000)}.wav"
    abs_filepath = os.path.abspath(temp_filename)
    try:
        with open(temp_filename, "wb") as f:
            f.write(wav_data)
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", 
            "-Command", f"(New-Object Media.SoundPlayer '{abs_filepath}').PlaySync()"
        ]
        subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        if os.path.exists(temp_filename):
            try: os.remove(temp_filename)
            except: pass

def play_wav_linux(wav_data: bytes, alsa_device: Optional[str] = None):
    """Utility to play a single WAV buffer on Linux via aplay."""
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

def play_wav(wav_data: bytes):
    """Platform-agnostic WAV player."""
    if sys.platform == "win32":
        play_wav_windows(wav_data)
    else:
        play_wav_linux(wav_data, ALSA_DEVICE)

# --- STREAMING ENGINE ---

def stream_audio_response(response: requests.Response):
    """Streams audio segments from server and plays them."""
    logger.info("Response stream started...")
    
    current_part = b""
    expected_size = 0
    is_json = False
    
    for chunk in response.iter_content(chunk_size=1024): 
        if not chunk: continue
        
        if not current_part and chunk.lstrip().startswith(b"{"):
            is_json = True
            
        current_part += chunk
        
        if not is_json:
            # 1. Parse header for size
            if expected_size == 0 and len(current_part) >= 8:
                riff_idx = current_part.find(b"RIFF")
                if riff_idx != -1:
                    if riff_idx > 0: current_part = current_part[riff_idx:]
                    if len(current_part) >= 8:
                        expected_size = struct.unpack("<I", current_part[4:8])[0] + 8
            
            # 2. Play when full part is received
            if expected_size > 0 and len(current_part) >= expected_size:
                wav_to_play = current_part[:expected_size]
                current_part = current_part[expected_size:]
                play_wav(wav_to_play)
                expected_size = 0
                if current_part.startswith(b"RIFF") and len(current_part) >= 8:
                    expected_size = struct.unpack("<I", current_part[4:8])[0] + 8

    if is_json:
        try:
            data = json.loads(current_part)
            status = data.get("status", "unknown")
            logger.info(f"Server Status: {status}")
        except:
            logger.error("Failed to parse JSON response from server")
    elif current_part:
        play_wav(current_part)
    
    logger.info("Stream complete.")

# --- RECORDING ENGINE ---

audio_queue = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    if status:
        logger.warning(f"Audio Status: {status}")
    audio_queue.put(indata.copy())

def main():
    global THRESHOLD
    print_banner()

    # Calibration
    logger.info("Calibrating noise floor...")
    try:
        rec = sd.rec(int(2 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS)
        sd.wait()
        noise_floor = np.max(np.abs(rec)) * 2.0
        THRESHOLD = max(THRESHOLD, noise_floor)
        logger.info(f"Calibration complete. Threshold: {THRESHOLD:.4f}")
    except Exception as e:
        logger.error(f"Calibration failed: {e}")
        sys.exit(1)

    logger.info("Listening... (Ctrl+C to exit)")

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=audio_callback):
            while True:
                audio_buffer = []
                silence_counter = 0
                started = False
                
                while True:
                    try:
                        chunk = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    volume = np.max(np.abs(chunk))
                    
                    if not started:
                        if volume > THRESHOLD:
                            logger.info("Speech detected...")
                            started = True
                            audio_buffer.append(chunk)
                    else:
                        audio_buffer.append(chunk)
                        if volume < THRESHOLD:
                            silence_counter += 1
                        else:
                            silence_counter = 0
                        
                        # Stop if silence limit reached
                        if silence_counter > (SILENCE_LIMIT * (SAMPLE_RATE / len(chunk))):
                            break
                
                if audio_buffer:
                    recording = np.concatenate(audio_buffer, axis=0)
                    if len(recording) > SAMPLE_RATE * 0.5: # Min 0.5s
                        logger.info(f"Sending audio ({len(recording)/SAMPLE_RATE:.2f}s)...")
                        
                        wav_io = io.BytesIO()
                        write(wav_io, SAMPLE_RATE, recording)
                        wav_io.seek(0)
                        
                        try:
                            files = {'audio_file': ('cmd.wav', wav_io, 'audio/wav')}
                            start_time = time.time()
                            with requests.post(SERVER_URL, files=files, stream=True) as r:
                                if r.status_code == 200:
                                    stream_audio_response(r)
                                    logger.info(f"[LATENCY] Round-trip: {time.time() - start_time:.2f}s")
                                else:
                                    logger.error(f"Server error: {r.status_code}")
                        except Exception as e:
                            logger.error(f"Network error: {e}")
                    else:
                         logger.debug("Captured audio too short, ignoring.")
                
                # Clear queue to avoid echoes
                with audio_queue.mutex:
                    audio_queue.queue.clear()

    except KeyboardInterrupt:
        logger.info("Exiting...")
    except Exception as e:
        logger.error(f"Main loop error: {e}")

if __name__ == "__main__":
    main()
