import sounddevice as sd
import threading
import json
import numpy as np
import requests
import io
import queue
import time
import subprocess
import os
import sys
import shutil
import struct
from scipy.io.wavfile import write, read
from typing import Optional, Generator
import re
import cv2

# Client imports

from mesh_common.logging import get_logger
from mesh_common.config import SAMPLE_RATE, CHANNELS, DEFAULT_SERVER_PORT
from mesh_client.led_controller import LEDManager, LEDState
from mesh_client.servo_controller import ServoController, HeadController
from mesh_client.locomotion import LocomotionController
from mesh_client.animation_controller import AnimationController
from mesh_client.buzzer_controller import BuzzerController
from mesh_client.power_monitor import PowerMonitor
from mesh_client.imu_wrapper import IMUWrapper

logger = get_logger("mesh_client")

# --- CONFIGURATION ---
SERVER_URL = os.environ.get("MESH_SERVER_URL", f"http://127.0.0.1:{DEFAULT_SERVER_PORT}/interact")
THRESHOLD = float(os.environ.get("MESH_THRESHOLD", 0.2))
SILENCE_LIMIT = float(os.environ.get("MESH_SILENCE_LIMIT", 2.5))
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
    """Utility to play a single WAV buffer on Windows via SoundDevice (Avoids PowerShell overhead)."""
    if not wav_data.startswith(b"RIFF"): return
    try:
        # Use scipy to read the in-memory WAV bytes
        # read returns (sample_rate, data)
        rate, data = read(io.BytesIO(wav_data))
        
        # Determine device (optional, uses default if None)
        sd.play(data, samplerate=rate)
        sd.wait() # Block until playback finishes to maintain sync
        
    except Exception as e:
        logger.error(f"SoundDevice Playback Error: {e}")
        # Fallback to PowerShell if SD fails?
        # For now, let's assume SD works since we are using it for Input.

# Global State Flags
is_speaking = threading.Event()

def play_wav_linux(wav_data: bytes, alsa_device: Optional[str] = None):
    """Utility to play a single WAV buffer on Linux via pw-play (Primary) or aplay (Fallback)."""
    logger.info(f"play_wav_linux called with {len(wav_data)} bytes")
    if not wav_data.startswith(b"RIFF"): 
        logger.warning("Data does not start with RIFF")
        return
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

def stream_audio_response(response: requests.Response, leds, cmd_callback=None):
    """Streams audio segments from server and plays them, executing commands if found."""
    logger.info("Response stream started...")
    
    # CRITICAL: Hold speaking lock for entire stream + buffer
    # This prevents the mic from opening between chunks
    is_speaking.set()
    leds.set_state(LEDState.SPEAKING)
    
    buffer = b""
    expected_size = 0
    
    try:
        # Match JSON commands: {"action":...} with optional newline
        command_pattern = re.compile(rb'(\{"action":.*?\})(\n)?')
    
        for chunk in response.iter_content(chunk_size=4096): 
            if not chunk: continue
            # logger.debug(f"Received stream chunk: {len(chunk)} bytes")
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

                    # SPECIAL CASE: "see" action
                    # If we are asked to see, we must silent the current stream (which contains hallucinated context)
                    # and jump straight to the vision loop.
                    if cmd.get("action") == "see":
                        logger.info("Vision Command Detected: Aborting current audio stream to prevent hallucination.")
                        # Drain buffer
                        buffer = b""
                        return # Exit function immediately
                    
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
                             pass
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
    finally:
        # POST-SPEECH COOL DOWN
        # Wait a moment for room echoes to die down
        time.sleep(1.0)
        
        # Drain queue of any self-hearing
        while not audio_queue.empty():
            try: audio_queue.get_nowait()
            except queue.Empty: break
            
    is_speaking.clear()
    leds.set_state(LEDState.IDLE)

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
    anim = AnimationController(locomotion, head)
    buzzer = BuzzerController()
    buzzer = BuzzerController()
    power = PowerMonitor()
    imu = IMUWrapper()
    
    last_activity_time = time.time()
    
    # Traction State
    last_accel = {'x':0, 'y':0, 'z':0}
    traction_slip_quota = 0
    
    logger.info("Hardware Initialized.")

    # Boot Sound & Animation
    # logger.info("Playing Boot Sound...")
    # play_wav(boot_sound_data)
    
    # anim.assume_tucked_pose() # Ensure start state
    # time.sleep(0.5)
    # anim.slow_boot_stand()
    
    # Instead, just assume safe standing pose
    locomotion.reset_posture()
    
    # Indicate Ready
    leds.set_state(LEDState.IDLE)

    def boot_sequence():
        """Choreographed startup: Audio + Slow Stand"""
        start_time = time.time()
        
        # Ensure Yellow "Thinking" State
        # leds.set_state(LEDState.THINKING)
        
        # 1. IMMEDIATE: Snap to Tucked State (Before Audio starts)
        try:
            # anim.assume_tucked_pose()
            pass # No longer assuming tucked pose at boot
        except Exception as e:
            logger.error(f"Failed to assume tucked pose: {e}")

        startup_wav = os.path.join(os.path.dirname(__file__), "startup.wav")
        if os.path.exists(startup_wav):
            logger.info("Playing Startup Audio...")
            try:
                with open(startup_wav, "rb") as f:
                    wav_data = f.read()
                # Play in thread so we can move simultaneously
                threading.Thread(target=play_wav, args=(wav_data,), daemon=True).start()
            except Exception as e:
                logger.error(f"Startup Audio Failed: {e}")
        
        # Wait for "Gyro... Online..." (6 seconds)
        time.sleep(6.0)

        # Trigger the MechWarrior Stand (~8.9 seconds)
        anim.slow_boot_stand()
        
        # HEAD SYNC: Pilot Quip at 15.25s
        elapsed_so_far = time.time() - start_time
        time_to_quip = 15.25 - elapsed_so_far
        if time_to_quip > 0:
            time.sleep(time_to_quip)
        
        # Look Up (Head Tilt +20 degrees)
        try:
            head.look_at(0, 20)
        except Exception as e:
            logger.warning(f"Head Lift Failed: {e}")

        # Padding: Block for full 21.0s (Audio Duration ~20.5s)
        elapsed = time.time() - start_time
        remaining = 21.0 - elapsed
        if remaining > 0:
            logger.info(f"Boot Sequence: Waiting {remaining:.2f}s for audio completion...")
            time.sleep(remaining)

    # Run Boot Sequence
    boot_sequence()

    # Movement State Flag
    is_moving = threading.Event()
    vision_requested = threading.Event()
    # is_speaking moved to global scope
    has_idled = False # Flag to prevent repeating idle anim

    def on_server_command(cmd):
        nonlocal last_activity_time
        nonlocal has_idled
        last_activity_time = time.time()
        has_idled = False
        
        def run_action():
            nonlocal last_activity_time
            try:
                """Handle hardware commands from server."""
                action = cmd.get("action")
                param = cmd.get("param")
                
                # Normalize action
                steps = 4
                if param:
                    match = re.search(r'\d+', str(param))
                    if match:
                        val = int(match.group())
                        steps = min(val, 10) # Cap at 10

                logger.info(f"Command Received: {action} (Param: {param})")
                
                if action in ["walk", "walk_forward", "move_forward", "move_backward", "turn_left", "turn_right"]:
                     logger.info(f"Movement Action: {action} for {steps} steps")
                     
                     # MUTE MICROPHONE
                     is_moving.set()
                     try:
                         if action in ["walk", "walk_forward", "move_forward"]:
                               locomotion.move_forward(steps, speed=1.5)
                         elif action == "move_backward":
                               locomotion.move_backward(steps, speed=1.5)
                         elif action == "turn_left":
                               locomotion.turn_left(steps, speed=1.5)
                         elif action == "turn_right":
                               locomotion.turn_right(steps, speed=1.5)
                     finally:
                         # Brief cool-down to let servos settle silence
                         time.sleep(0.2)
                         is_moving.clear()
                     
                     return # Movement handled
                
                # IDLE CHECK
                if time.time() - last_activity_time > 30.0: # 30s of silence
                     # Trigger idle animation if not already moving
                     if not is_moving.is_set():
                          logger.info("Auto-Idle: Palp Wiggle")
                          is_moving.set()
                          try:
                              anim.palp_wiggle()
                          finally:
                              is_moving.clear()
                          last_activity_time = time.time()

                time.sleep(0.05)
                
                # --- TRACTION CONTROL & DYNAMICS LOOP ---
                # 1. Active Lean (Anti-Wheelie / Dig-In)
                # If moving forward, lean forward (-Pitch? Or +Pitch depending on frame)
                # Locomotion logic: +Pitch rotates feet (y*cos - z*sin).
                # If Feet rotate +Pitch relative to Body, Body rotates -Pitch relative to Ground.
                # We want Body to Nose Down. 
                # Let's try +5 deg for Forward, -5 for Backward.
                target_pitch = 0
                if action in ["walk", "walk_forward", "move_forward"]:
                     target_pitch = 5.0 
                elif action == "move_backward":
                     target_pitch = -5.0
                
                # Smoothly update locomotion pitch
                locomotion.body_pitch = locomotion.body_pitch * 0.8 + target_pitch * 0.2
                
                # 2. Slip Detection (Jerk Monitor)
                # Only check if moving
                if is_moving.is_set():
                     accel = imu.read_accel_raw()
                     # Calculate Jerk (Delta Accel)
                     dx = accel['x'] - last_accel['x']
                     dy = accel['y'] - last_accel['y']
                     dz = accel['z'] - last_accel['z']
                     jerk = (dx**2 + dy**2 + dz**2)**0.5
                     
                     # Update historical
                     last_accel = accel
                     
                     # Threshold: High G impact (slip/catch)
                     # Typical walk noise ~ 0.2G?
                     # Slip/Catch spike ~ 0.8G?
                     if jerk > 0.8: # Tune this!
                          logger.warning(f"TRACTION LOSS DETECTED (Jerk={jerk:.2f}). Throttle back!")
                          traction_slip_quota += 1
                          if traction_slip_quota > 3:
                               # Persistent slip
                               logger.error("Excessive Slip! Aborting move.")
                               # How to abort? locomotion doesn't support async abort yet. 
                               # We can warn via LED.
                               leds.set_state(LEDState.ERROR)
                     else:
                          traction_slip_quota = max(0, traction_slip_quota - 1)

                
                # Non-movement actions
                if action == "null": return
                
                # Head Actions
                elif action == "look_left":
                     head.look_left()
                elif action == "look_right":
                     head.look_right()
                elif action == "look_down":
                     head.look_down()
                elif action == "look_up":
                     head.look_up()
                elif action == "look_center":
                     head.look_neutral()
                
                # LED Actions
                elif action == "led_on":
                     leds.set_state(LEDState.LISTENING) # Use white/listening for ON
                elif action == "led_off":
                     leds.set_state(LEDState.IDLE)
                elif action == "led_flash":
                     for _ in range(3):
                         leds.set_state(LEDState.SPEAKING)
                         time.sleep(0.1)
                         leds.set_state(LEDState.IDLE)
                         time.sleep(0.1)

                # Buzzer Actions
                elif action == "buzzer_beep":
                     buzzer.beep()

                # Emote Actions
                elif action in ["emote", "laugh", "bow", "wiggle"]:
                    is_moving.set()
                    try:
                        anim_name = param if action == "emote" else action
                        logger.info(f"Executing Emote: {anim_name}")
                        
                        if anim_name == "laugh": anim.laugh()
                        elif anim_name == "bow": anim.bow()
                        elif anim_name == "wiggle": anim.palp_wiggle()
                        else: logger.warning(f"Unknown emote: {anim_name}")
                    except Exception as e:
                        logger.error(f"Emote Failed: {e}")
                    finally:
                        is_moving.clear()

                elif action == "buzzer_warn":
                     buzzer.warn()
                elif action == "buzzer_alarm":
                     buzzer.alarm()
                elif action in ["relax", "stand_by"]:
                     head.look_neutral()
                     time.sleep(0.5)
                     sc.relax()
                elif action in ["reset", "lay_flat"]:
                     locomotion.reset_posture_flat()

                # Vision Action
                elif action == "see":
                     logger.info("Requesting Vision Action (Serialized)...")
                     vision_requested.set()
            except Exception as e:
                logger.error(f"Command execution error: {e}")
                is_moving.clear() # Ensure cleared on error


        # Start execution in background
        threading.Thread(target=run_action, daemon=True).start()



    # Camera Logic (Moved to Main Scope)
    last_vision_time = 0
    def capture_and_send_vision():
        nonlocal last_vision_time
        if time.time() - last_vision_time < 5.0:
            logger.warning("Vision: Debounced (Too soon).")
            return
        last_vision_time = time.time()

        """Captures image and sends to server."""
        logger.info("Vision: capturing image...")
        jpg_bytes = None
        
        # METHOD 1: Try Native CLI Tools (rpicam-jpeg / libcamera-jpeg)
        # This is most robust on Pi Bullseye/Bookworm as it bypasses python binding issues.
        for tool in ["rpicam-jpeg", "libcamera-jpeg"]:
            if shutil.which(tool):
                temp_img = "/tmp/mesh_vision_capture.jpg"
                try:
                    # Capture safely
                    subprocess.run([tool, "-o", temp_img, "-t", "500", "--width", "640", "--height", "480", "--nopreview"], check=True)
                    if os.path.exists(temp_img):
                        with open(temp_img, "rb") as f:
                            jpg_bytes = f.read()
                        os.remove(temp_img)
                        logger.info(f"Vision: Captured via {tool} ({len(jpg_bytes)} bytes).")
                        break
                except Exception as e:
                    logger.warning(f"Vision: CLI {tool} failed: {e}")
        
        # METHOD 2: Fallback to OpenCV
        if not jpg_bytes:
            logger.info("Vision: Falling back to OpenCV...")
            try:
                cap = cv2.VideoCapture(0)
                if not cap.isOpened():
                    logger.error("Vision: Could not open camera (cv2).")
                else:
                    # Warmup
                    for _ in range(5): cap.read()
                    
                    ret, frame = cap.read()
                    cap.release()
                    
                    if ret:
                        frame = cv2.resize(frame, (640, 480))
                        ret, buffer = cv2.imencode('.jpg', frame)
                        if ret:
                            jpg_bytes = buffer.tobytes()
                            logger.info(f"Vision: Captured via OpenCV ({len(jpg_bytes)} bytes).")
            except Exception as e:
                logger.error(f"Vision: OpenCV failed: {e}")

        if not jpg_bytes:
            logger.error("Vision: All capture methods failed.")
            leds.set_state(LEDState.ERROR)
            time.sleep(1)
            leds.set_state(LEDState.IDLE)
            return

        # Send to Server
        try:
            leds.set_state(LEDState.THINKING)
            
            files = {
                'image_file': ('view.jpg', io.BytesIO(jpg_bytes), 'image/jpeg')
            }
            data = {
                'prompt': "Describe what you see in this image."
            }
            
            with requests.post(SERVER_URL, files=files, data=data, stream=True, timeout=30) as r:
                if r.status_code == 200:
                    leds.set_state(LEDState.SPEAKING)
                    head.look_up(20)
                    stream_audio_response(r, leds, on_server_command)
                    
                    last_activity_time = time.time()
                    
                    leds.set_state(LEDState.IDLE)
                    head.look_neutral()
                else:
                    logger.error(f"Vision Server Error: {r.status_code}")
                    leds.set_state(LEDState.ERROR)
                    time.sleep(1)
                    leds.set_state(LEDState.IDLE)
                    
        except Exception as e:
            logger.error(f"Vision Network Error: {e}")
            leds.set_state(LEDState.ERROR)
            time.sleep(1)
            leds.set_state(LEDState.IDLE)


    # Calibration
    audio_input_available = True
    logger.info("Calibrating noise floor...")
    calibration_attempts = 0
    while True:
        try:
            leds.set_state(LEDState.THINKING) # Yellow/Blue pulse to indicate initializing
            rec = sd.rec(int(2 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS)
            sd.wait()
            noise_floor = np.max(np.abs(rec)) * 2.0
            # Adaptive Threshold Logic:
            # Set threshold relative to noise floor with a safety buffer: 1.5x Multiplier + 0.02 Offset.
            # Hard Cap: 0.12 to ensure sensitivity.
            # Prefer calibrated value unless it is critically low.
            calculated_threshold = max(0.08, min(noise_floor * 1.5 + 0.02, 0.20))
            
            THRESHOLD = calculated_threshold
            logger.info(f"Calibration captured noise floor: {noise_floor:.4f}. Setting Threshold: {THRESHOLD:.4f}")
            leds.set_state(LEDState.IDLE)
            head.look_neutral()
            break
        except Exception as e:
            calibration_attempts += 1
            leds.set_state(LEDState.ERROR) # Flash Red
            logger.warning(f"Audio device not ready ({calibration_attempts}/3)... Error: {e}")
            if calibration_attempts >= 3:
                logger.error("No microphone detected. Disabling audio input.")
                audio_input_available = False
                leds.set_state(LEDState.IDLE)
                break
            time.sleep(2)

    logger.info("Listening... (Ctrl+C to exit)")

    # Idle State Tracking
    last_activity_time = time.time()
    has_idled = False

    # Idle Logic Closure
    def check_idle_timeout():
        nonlocal last_activity_time, has_idled
        
        if (time.time() - last_activity_time > 45.0) and not has_idled:
            logger.info("Idle limit reached (45s). Auto-Relaxing...")
            # No animation, just cut power to sit still/silent
            try:
                head.look_neutral()
                time.sleep(0.5)
                sc.relax()
                has_idled = True
            except Exception as e:
                logger.error(f"Idle Relax Error: {e}")
            finally:
                # We don't need to set is_moving here since we aren't animating
                pass

    try:
        if audio_input_available:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=audio_callback):
                while True:
                    # MUTE during movement or speaking to prevent self-triggering
                    if is_moving.is_set() or is_speaking.is_set():
                        # Drain queue to discard servo noise and self-speech
                        while not audio_queue.empty():
                            try: audio_queue.get_nowait()
                            except queue.Empty: break
                        time.sleep(0.5) # Extended cooldown to reject servo spin-down noise
                        # Update activity to prevent immediate idle trigger after move
                        last_activity_time = time.time()
                        continue

                    # Outer Loop Idle Check
                    check_idle_timeout() 

                    # DEBUG: Print idle status every 5s
                    idle_dur = time.time() - last_activity_time
                    if int(idle_dur) % 5 == 0 and int(idle_dur) > 0:
                         # Use \r to overwrite line for a cleaner "dashboard" effect in terminal
                         print(f"DEBUG: Idle Duration: {idle_dur:.1f}s / 120.0s   ", end='\r') 

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
                            if not started:
                                check_idle_timeout()
                            continue 
                        
                        # CRITICAL FIX: Check if we started speaking while waiting for chunk
                        if is_speaking.is_set():
                            # Clear buffer and restart outer loop to drain
                            preroll_buffer = []
                            audio_buffer = []
                            started = False
                            break

                        volume = np.max(np.abs(chunk))
                        
                        if not started:
                            # Check idle while listening to silence
                            check_idle_timeout()

                            # Append to pre-roll
                            preroll_buffer.append(chunk)
                            
                            # Maintain roughly 0.5s of audio in pre-roll
                            # Heuristic: Total samples in buffer should be ~ SAMPLE_RATE * 0.5
                            current_samples = sum(len(c) for c in preroll_buffer)
                            while current_samples > int(SAMPLE_RATE * 0.5):
                                removed = preroll_buffer.pop(0)
                                current_samples -= len(removed)
                                
                            if volume > THRESHOLD:
                                last_activity_time = time.time()
                                has_idled = False
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
                            wav_io.seek(0) # Important: reset stream position to beginning
                            
                            # Send to server
                            leds.set_state(LEDState.THINKING)
                            
                            # 1. Gather Telemetry
                            telemetry = power.get_status()
                            telemetry_json = json.dumps(telemetry)

                            files = {
                                'audio_file': ('audio.wav', wav_io, 'audio/wav')
                            }
                            data = {
                                'telemetry': telemetry_json
                            }
                            
                            start_time = time.time()
                            try:
                                # Timeout increased to 30s to handle first-run TTS loading latency
                                with requests.post(SERVER_URL, files=files, data=data, stream=True, timeout=30) as r:
                                    if r.status_code == 200:
                                        leds.set_state(LEDState.SPEAKING)
                                        head.look_up(20)
                                        head.look_up(20)
                                        stream_audio_response(r, leds, on_server_command)
                                        logger.info(f"[LATENCY] Round-trip: {time.time() - start_time:.2f}s")
                                        
                                        # DEFERRED ACTION CHECK
                                        if vision_requested.is_set():
                                             logger.info("Executing Deferred Vision Action...")
                                             vision_requested.clear()
                                             capture_and_send_vision()

                                        last_activity_time = time.time() # Reset idle timer
                                        
                                        leds.set_state(LEDState.IDLE)
                                        head.look_neutral()
                                    else:
                                        leds.set_state(LEDState.ERROR)
                                        logger.error(f"Server error: {r.status_code}")
                                        time.sleep(1) # Show error state briefly
                                        leds.set_state(LEDState.IDLE)
                            except requests.exceptions.Timeout:
                                logger.error("Server Timed Out (10s)")
                                leds.set_state(LEDState.ERROR)
                                # buzzer.warn() (Disabled)
                                time.sleep(1)
                                leds.set_state(LEDState.IDLE)
                            except Exception as e:
                                logger.error(f"Network Error: {e}")
                                leds.set_state(LEDState.ERROR)
                                time.sleep(1)
                                leds.set_state(LEDState.IDLE)

                        else:
                            logger.debug("Captured audio too short, ignoring.")
                    
                    # Clear queue to avoid echoes
                    with audio_queue.mutex:
                        audio_queue.queue.clear()
        else:
            # Fallback loop (No Audio Input)
            logger.info("Entering Audio-Less Mode (Idle Only).")
            while True:
                # Still process idle animations
                check_idle_timeout()
                
                time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info("Exiting...")
    except Exception as e:
        leds.set_state(LEDState.ERROR)
        logger.error(f"Main loop error: {e}")
    finally:
        leds.stop()

if __name__ == "__main__":
    main()
