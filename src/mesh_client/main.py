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
THRESHOLD = float(os.environ.get("MESH_THRESHOLD", 0.2))
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
    """Utility to play a single WAV buffer on Linux via pw-play (Primary) or aplay (Fallback)."""
    if not wav_data.startswith(b"RIFF"): return
    temp_filename = f"temp_recv_{int(time.time() * 1000)}.wav"
    abs_filepath = os.path.abspath(temp_filename)
    
    logger.info(f"Playing {len(wav_data)} bytes...")
    
    try:
        with open(abs_filepath, "wb") as f:
            f.write(wav_data)
        
        # 1. Attempt Primary: PipeWire (pw-play)
        # Using subprocess.run to verify success/failure (capturing stderr)
        try:
            cmd = ["pw-play", abs_filepath]
            result = subprocess.run(cmd, capture_output=True, text=True) # text=True for string output
            
            if result.returncode != 0:
                logger.warning(f"pw-play failed (rc={result.returncode}): {result.stderr.strip()}")
                raise Exception("pw-play failure")
                
        except Exception as e:
            # 2. Attempt Fallback: ALSA (aplay)
            logger.info("Falling back to ALSA (aplay)...")
            cmd = ["aplay", "-q", "-t", "wav"]
            if alsa_device:
                dev = alsa_device.replace("hw:", "plughw:", 1) if alsa_device.startswith("hw:") else alsa_device
                cmd.extend(["-D", dev])
            cmd.append(abs_filepath)
            
            # Run fallback, still capturing output to diagnose if that fails too
            result_alsa = subprocess.run(cmd, capture_output=True, text=True)
            if result_alsa.returncode != 0:
                logger.error(f"aplay also failed (rc={result_alsa.returncode}): {result_alsa.stderr.strip()}")

    finally:
        if os.path.exists(abs_filepath):
            try: os.remove(abs_filepath)
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
    
    buffer = b""
    expected_size = 0
    # Match JSON commands: {"action":...} with optional newline
    command_pattern = re.compile(b'(\{"action":.*?\})(\\n)?')

    for chunk in response.iter_content(chunk_size=4096): 
        if not chunk: continue
        buffer += chunk
        
        # 1. Scan for Commands manually (Regex can be flaky on binary)
        while True:
            start_idx = buffer.find(b'{"action"')
            if start_idx == -1:
                break
            
            # Found start, look for end
            # We assume simple JSON object ending with } or }\n
            # Safest is to find "}" after start
            end_idx = buffer.find(b'}', start_idx)
            if end_idx == -1:
                break # Wait for more data
            
            # Extract candidate
            json_bytes = buffer[start_idx:end_idx+1]
            try:
                cmd_str = json_bytes.decode("utf-8")
                logger.info(f"Found Command Candidate: {cmd_str}")
                cmd = json.loads(cmd_str)
                if cmd_callback: 
                    cmd_callback(cmd)
                
                # Remove from buffer
                # Note: There might be a \n after match, it will be treated as garbage later (fine)
                buffer = buffer[:start_idx] + buffer[end_idx+1:]
                
            except Exception as e:
                logger.error(f"Manual Parse Failed: {e}")
                # We should probably discard this attempt to avoid infinite loop
                # checking the same invalid bytes?
                # For now, assume if it looks like {"action" it's valid.
                break 

        # 2. Parse WAV headers
        while True:
            if expected_size == 0:
                riff_idx = buffer.find(b"RIFF")
                if riff_idx == -1:
                    break 
                
                if len(buffer) < riff_idx + 8:
                    break 
                
                # Discard garbage (Log it!)
                if riff_idx > 0:
                    garbage = buffer[:riff_idx]
                    if len(garbage) > 4: # Ignore small newlines
                         logger.warning(f"Discarding {len(garbage)} bytes before RIFF: {garbage[:50]}...")
                    buffer = buffer[riff_idx:]
                
                val = struct.unpack("<I", buffer[4:8])[0]
                expected_size = val + 8
                # logger.info(f"WAV Detected. Size: {expected_size}")
            
            if expected_size > 0:
                if len(buffer) >= expected_size:
                    wav_data = buffer[:expected_size]
                    buffer = buffer[expected_size:] 
                    expected_size = 0 
                    play_wav(wav_data)
                else:
                    break

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
    while True:
        try:
            leds.set_state(LEDState.THINKING) # Yellow/Blue pulse to indicate initializing
            rec = sd.rec(int(2 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS)
            sd.wait()
            noise_floor = np.max(np.abs(rec)) * 2.0
            # Only overwrite THRESHOLD if it wasn't set by environment variable (optional logic)
            # THRESHOLD = max(THRESHOLD, noise_floor) 
            logger.info(f"Calibration captured noise floor: {noise_floor:.4f}. Using current Threshold: {THRESHOLD:.4f}")
            leds.set_state(LEDState.IDLE)
            head.look_neutral()
            break
        except Exception as e:
            leds.set_state(LEDState.ERROR) # Flash Red
            logger.warning(f"Audio device not ready, retrying in 5s... Error: {e}")
            time.sleep(5)

    logger.info("Listening... (Ctrl+C to exit)")

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=audio_callback):
            while True:
                audio_buffer = []
                silence_counter = 0
                started = False
                
                # Pre-roll buffer to capture audio before threshold trigger
                # Using a list of chunks, aimed at roughly 0.5s duration
                preroll_buffer = []
                
                while True:
                    try:
                        chunk = audio_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    volume = np.max(np.abs(chunk))
                    
                    if not started:
                        # Append to pre-roll
                        preroll_buffer.append(chunk)
                        
                        # Maintain roughly 0.5s of audio in pre-roll
                        # Heuristic: Total samples in buffer should be ~ SAMPLE_RATE * 0.5
                        current_samples = sum(len(c) for c in preroll_buffer)
                        while current_samples > int(SAMPLE_RATE * 0.5):
                            removed = preroll_buffer.pop(0)
                            current_samples -= len(removed)
                            
                        if volume > THRESHOLD:
                            logger.info(f"Speech detected (Pre-roll: {len(preroll_buffer)} chunks)...")
                            leds.set_state(LEDState.LISTENING)
                            started = True
                            
                            # Move pre-roll to main buffer
                            audio_buffer.extend(preroll_buffer)
                            preroll_buffer = [] # Clear logic
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
