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
import re

# Client imports

from mesh_common.logging import get_logger
from mesh_common.config import SAMPLE_RATE, CHANNELS, DEFAULT_SERVER_PORT
from mesh_client.led_controller import LEDManager, LEDState
from mesh_client.servo_controller import ServoController, HeadController
from mesh_client.locomotion import LocomotionController

logger = get_logger("mesh_client")

# --- CONFIGURATION ---
SERVER_URL = os.environ.get("MESH_SERVER_URL", f"http://127.0.0.1:{DEFAULT_SERVER_PORT}/interact")
THRESHOLD = float(os.environ.get("MESH_THRESHOLD", 0.4))
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

def stream_audio_response(response: requests.Response, cmd_callback=None):
    """Streams audio segments from server and plays them, executing commands if found."""
    logger.info("Response stream started...")
    
    current_part = b""
    expected_size = 0
    
    first_chunk = True
    
    for chunk in response.iter_content(chunk_size=4096): 
        if not chunk: continue
        current_part += chunk

        if first_chunk:
             logger.info(f"Stream Start Bytes: {current_part[:100]}")
             # Check for any newlines in the first 100 bytes
             if b"\n" in current_part[:100]:
                 logger.info("Newline found in header.")
             first_chunk = False

        # 1. Try to parse JSON Command at start of buffer
        # (Server sends '{"action":...}\n' between WAVs)
        if current_part.lstrip().startswith(b"{") and b"\n" in current_part:
            try:
                newline_idx = current_part.find(b"\n")
                json_line = current_part[:newline_idx].strip()
                data = json.loads(json_line)
                if "action" in data:
                    logger.info(f"Received Command: {data}")
                    if cmd_callback: cmd_callback(data)
                
                # Consume JSON line
                current_part = current_part[newline_idx+1:]
                expected_size = 0 # Reset expectation
            except Exception as e:
                logger.debug(f"JSON Parse Attempt Failed: {e}")

        # 2. Parse WAV header for size
        if expected_size == 0:
            riff_idx = current_part.find(b"RIFF")
            if riff_idx != -1:
                # Discard garbage before RIFF
                current_part = current_part[riff_idx:]
                if len(current_part) >= 8:
                    expected_size = struct.unpack("<I", current_part[4:8])[0] + 8
        
        # 3. Play when full WAV part is received
        if expected_size > 0 and len(current_part) >= expected_size:
            wav_to_play = current_part[:expected_size]
            current_part = current_part[expected_size:]
            play_wav(wav_to_play)
            expected_size = 0
    
    # Handle remaining simple JSON response (if old protocol)
    if current_part.strip().startswith(b"{") and current_part.strip().endswith(b"}"):
         try:
            data = json.loads(current_part)
            logger.info(f"Server Status: {data.get('status')}")
         except: pass

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
    
    # Initialize Hardware
    # Initialize Hardware
    leds = LEDManager()
    sc = ServoController()
    head = HeadController(sc)
    locomotion = LocomotionController(sc)
    
    leds.set_state(LEDState.THINKING)
    head.look_up(10)

    def on_server_command(cmd):
        """Handle hardware commands from server."""
        action = cmd.get("action")
        param = cmd.get("param")
        
        # Normalize action
        if action in ["walk", "walk_forward", "move_forward"]:
             # Try to extract number of steps from param, default to 4
             steps = 4
             if param:
                 match = re.search(r'\d+', str(param))
                 if match:
                     # If they say "10cm", usually that's just 1-2 steps, but let's just treat digits as steps for now
                     # to be responsive.
                     val = int(match.group())
                     # Cap it for safety
                     steps = min(val, 10)
             
             logger.info(f"Executing Walk: {steps} steps")
             leds.set_state(LEDState.THINKING) 
             locomotion.move_forward(steps)
             leds.set_state(LEDState.SPEAKING)


    # Calibration
    logger.info("Calibrating noise floor...")
    try:
        rec = sd.rec(int(2 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS)
        sd.wait()
        noise_floor = np.max(np.abs(rec)) * 2.0
        # Only overwrite THRESHOLD if it wasn't set by environment variable (optional logic)
        # THRESHOLD = max(THRESHOLD, noise_floor) 
        logger.info(f"Calibration captured noise floor: {noise_floor:.4f}. Using current Threshold: {THRESHOLD:.4f}")
        leds.set_state(LEDState.IDLE)
        head.look_neutral()
    except Exception as e:
        leds.set_state(LEDState.ERROR)
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
                            leds.set_state(LEDState.LISTENING)
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
                            leds.set_state(LEDState.THINKING)
                            time.sleep(0.2) # Ensure thinking state is visible
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
                                    leds.set_state(LEDState.SPEAKING)
                                    head.look_up(20)
                                    leds.set_state(LEDState.SPEAKING)
                                    head.look_up(20)
                                    stream_audio_response(r, on_server_command)
                                    logger.info(f"[LATENCY] Round-trip: {time.time() - start_time:.2f}s")
                                    leds.set_state(LEDState.IDLE)
                                    head.look_neutral()
                                else:
                                    leds.set_state(LEDState.ERROR)
                                    logger.error(f"Server error: {r.status_code}")
                                    time.sleep(1) # Show error for a bit
                                    leds.set_state(LEDState.IDLE)
                                    head.look_neutral()
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
        leds.set_state(LEDState.ERROR)
        logger.error(f"Main loop error: {e}")
    finally:
        leds.stop()

if __name__ == "__main__":
    main()
