import sounddevice as sd
import soundfile as sf
import threading
import json
import numpy as np
import requests
import io
import queue
import time
import subprocess
import os
import shutil
import struct
from scipy.io.wavfile import write
from typing import Generator
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
from mesh_client.command_dispatcher import CommandDispatcher

logger = get_logger("mesh_client")

# --- CONFIGURATION ---
SERVER_URL = os.environ.get("MESH_SERVER_URL", f"http://192.168.4.80:{DEFAULT_SERVER_PORT}/interact")
THRESHOLD = float(os.environ.get("MESH_THRESHOLD", 0.2))
PLAYBACK_RATE = 24000  # TTS output sample rate (Chatterbox/ElevenLabs)
SILENCE_LIMIT = float(os.environ.get("MESH_SILENCE_LIMIT", 2.5))

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

# --- PLAYBACK ENGINE (Zero-Disk, In-Memory) ---

# Global State Flags
is_speaking = threading.Event()

class AudioPlayer:
    """
    High-performance audio player that streams decoded samples directly
    to the sound hardware via a persistent sd.OutputStream.
    
    Zero disk I/O: WAV bytes are decoded in RAM via soundfile.
    Zero process spawning: No subprocess calls (pw-play, aplay).
    The OutputStream is opened once at boot and kept alive.
    """
    def __init__(self, samplerate=PLAYBACK_RATE, channels=1):
        self._samplerate = samplerate
        self._channels = channels
        self._queue = queue.Queue()
        self._buffer = np.zeros((0, channels), dtype='float32')
        self._lock = threading.Lock()
        self._stream = None
        self._active = threading.Event()  # True when audio is queued/playing
    
    def start(self):
        """Open the persistent output stream. Call once at boot."""
        self._stream = sd.OutputStream(
            samplerate=self._samplerate,
            channels=self._channels,
            dtype='float32',
            callback=self._callback,
            blocksize=1024,
            latency='low'
        )
        self._stream.start()
        logger.info(f"AudioPlayer: OutputStream opened ({self._samplerate}Hz, {self._channels}ch, low-latency)")
    
    def stop(self):
        """Shutdown the output stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
    
    def _callback(self, outdata, frames, time_info, status):
        """Called by the audio driver. Pulls samples from internal buffer/queue."""
        if status:
            logger.warning(f"Playback Status: {status}")
        
        needed = frames
        written = 0
        
        with self._lock:
            while needed > 0:
                # Pull from leftover buffer first
                if len(self._buffer) > 0:
                    take = min(needed, len(self._buffer))
                    outdata[written:written + take] = self._buffer[:take]
                    self._buffer = self._buffer[take:]
                    written += take
                    needed -= take
                else:
                    # Try to get next chunk from queue
                    try:
                        chunk = self._queue.get_nowait()
                        self._buffer = chunk
                    except queue.Empty:
                        # No more data — fill remainder with silence
                        outdata[written:] = 0
                        self._active.clear()
                        return
            
            # If we filled the entire frame, we're still active
            if not self._queue.empty() or len(self._buffer) > 0:
                self._active.set()
    
    def play_wav_bytes(self, wav_data: bytes):
        """
        Decode a complete WAV buffer in RAM and push samples to the playback queue.
        Non-blocking: returns immediately after queueing.
        """
        if not wav_data or len(wav_data) < 44:  # Minimum WAV header size
            return
        
        try:
            # Decode WAV entirely in memory using C-level libsndfile
            data, rate = sf.read(io.BytesIO(wav_data), dtype='float32')
            
            # Ensure 2D array (samples, channels)
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            
            # Resample if the WAV rate doesn't match our output stream
            if rate != self._samplerate:
                from scipy.signal import resample_poly
                from math import gcd
                g = gcd(int(rate), self._samplerate)
                up = self._samplerate // g
                down = int(rate) // g
                data = resample_poly(data, up, down, axis=0).astype('float32')
            
            self._active.set()
            self._queue.put(data)
            
        except Exception as e:
            logger.error(f"AudioPlayer decode error: {e}")
    
    def play_wav_blocking(self, wav_data: bytes):
        """
        Decode and play a WAV buffer, blocking until playback completes.
        Used for boot sounds and other synchronous audio.
        """
        self.play_wav_bytes(wav_data)
        self.wait_until_done()
    
    def wait_until_done(self):
        """Block until the playback queue is fully drained."""
        while self._active.is_set() or not self._queue.empty():
            time.sleep(0.05)
    
    @property
    def is_playing(self):
        return self._active.is_set() or not self._queue.empty()

# --- STREAMING ENGINE ---

def stream_audio_response(response: requests.Response, leds, player: AudioPlayer, cmd_callback=None):
    """Streams audio segments from server, decodes in-memory, and pushes to AudioPlayer."""
    logger.info("Response stream started...")
    
    # CRITICAL: Hold speaking lock for entire stream + buffer
    # This prevents the mic from opening between chunks
    is_speaking.set()
    leds.set_state(LEDState.SPEAKING)
    
    buffer = b""
    expected_size = 0
    
    try:
        for chunk in response.iter_content(chunk_size=4096): 
            if not chunk: continue
            buffer += chunk
            
            # 1. Scan for Commands manually (Regex can be flaky on binary)
            while True:
                start_idx = buffer.find(b'{"action"')
                if start_idx == -1:
                    break
                
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
                    
                    buffer = buffer[:start_idx] + buffer[end_idx+1:]

                    # SPECIAL CASE: "see" action — abort stream to prevent hallucination
                    if cmd.get("action") == "see":
                        logger.info("Vision Command Detected: Aborting current audio stream.")
                        buffer = b""
                        return
                    
                except Exception as e:
                    logger.error(f"Manual Parse Failed: {e}")
                    break 
    
            # 2. Parse WAV headers and decode in-memory
            while True:
                if expected_size == 0:
                    riff_idx = buffer.find(b"RIFF")
                    if riff_idx == -1:
                        break 
                    
                    if len(buffer) < riff_idx + 8:
                        break 
                    
                    # Discard garbage before RIFF
                    if riff_idx > 0:
                        buffer = buffer[riff_idx:]
                    
                    val = struct.unpack("<I", buffer[4:8])[0]
                    expected_size = val + 8
                
                if expected_size > 0:
                    if len(buffer) >= expected_size:
                        wav_data = buffer[:expected_size]
                        buffer = buffer[expected_size:] 
                        expected_size = 0 
                        # ZERO-DISK: Decode in RAM and push to audio hardware
                        player.play_wav_bytes(wav_data)
                    else:
                        break
        
        # Wait for all queued audio to finish playing
        player.wait_until_done()
                        
    finally:
        # POST-SPEECH COOL DOWN
        # Wait a moment for room echoes to die down
        time.sleep(1.0)
        
        # Drain mic queue of any self-hearing
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
    leds = LEDManager()
    buzzer = BuzzerController()
    power = PowerMonitor()
    imu = IMUWrapper() # Moved up to inject into Locomotion
    
    # Initialize Controllers
    sc = ServoController()
    head = HeadController(sc)
    locomotion = LocomotionController(sc, imu)
    anim = AnimationController(locomotion, head)
    
    last_activity_time = time.time()
    
    # Traction State
    last_accel = {'x':0, 'y':0, 'z':0}
    traction_slip_quota = 0
    
    logger.info("Hardware Initialized.")

    # Initialize Audio Player (Zero-Disk, Persistent OutputStream)
    player = AudioPlayer(samplerate=PLAYBACK_RATE, channels=1)
    player.start()
    logger.info("AudioPlayer: Ready.")

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
                # Play non-blocking so we can move simultaneously
                player.play_wav_bytes(wav_data)
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
    # boot_sequence()

    # Movement State Flag
    is_moving = threading.Event()
    vision_requested = threading.Event()
    # is_speaking moved to global scope
    has_idled = False # Flag to prevent repeating idle anim

    # --- COMMAND DISPATCHER ---
    dispatcher = CommandDispatcher()

    def execute_hardware_command(param, action_name=None):
        nonlocal last_activity_time
        nonlocal has_idled
        
        # If action_name is passed (via closure), use it. 
        # But wait, Dispatcher calls func(param).
        # We need to know WHICH action triggered this if we register generic handler.
        # Strategy: Register distinct lambdas for each action.
        
        action = action_name
        last_activity_time = time.time()
        has_idled = False
        
        try:
            # Normalize action steps
            steps = 4
            if param:
                match = re.search(r'\d+', str(param))
                if match:
                    val = int(match.group())
                    
                    # TURN LOGIC: Convert Degrees to Steps
                    if action in ["turn_left", "turn_right"] and val > 15:
                            # Assume Degrees. Eff turn rate ~15 deg/step
                            steps = int(val / 15.0)
                            steps = min(steps, 60) # Cap turn
                            logger.info(f"Turn Logic: Converted {val} deg -> {steps} steps")
                    else:
                            steps = min(val, 20)

            logger.info(f"Command Execution: {action} (Param: {param})")

            # --- ACTIVE LEANING ---
            target_pitch = 0.0
            if action in ["walk", "walk_forward", "move_forward"]:
                    target_pitch = 10.0
            elif action == "move_backward":
                    target_pitch = -10.0
            
            locomotion.body_pitch = target_pitch
            
            if action in ["walk", "walk_forward", "move_forward", "move_backward", "turn_left", "turn_right"]:
                    logger.info(f"Movement Action: {action} for {steps} steps")
                    
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
                        time.sleep(0.2)
                        locomotion.body_pitch = 0.0
                        is_moving.clear()
                    return

            # IDLE CHECK (Keep simple update)
            if time.time() - last_activity_time > 30.0:
                    if not is_moving.is_set():
                        is_moving.set()
                        try: anim.palp_wiggle()
                        finally: is_moving.clear()
                        last_activity_time = time.time()

            time.sleep(0.05)
            
            if action == "null": return
            
            # Head Actions
            elif action == "look_left": head.look_left()
            elif action == "look_right": head.look_right()
            elif action == "look_down": head.look_down()
            elif action == "look_up": head.look_up()
            elif action == "look_center": head.look_neutral()
            
            # LED Actions
            elif action == "led_on": leds.set_state(LEDState.LISTENING)
            elif action == "led_off": leds.set_state(LEDState.IDLE)
            elif action == "led_flash":
                    for _ in range(3):
                        leds.set_state(LEDState.SPEAKING)
                        time.sleep(0.1)
                        leds.set_state(LEDState.IDLE)
                        time.sleep(0.1)

            # Buzzer Actions
            elif action == "buzzer_beep": buzzer.beep()
            elif action == "buzzer_warn": buzzer.warn()
            elif action == "buzzer_alarm": buzzer.alarm()

            # Emote Actions
            elif action in ["emote", "laugh", "bow", "wiggle"]:
                is_moving.set()
                try:
                    anim_name = param if action == "emote" else action
                    if anim_name == "laugh": anim.laugh()
                    elif anim_name == "bow": anim.bow()
                    elif anim_name == "wiggle": anim.palp_wiggle()
                finally:
                    is_moving.clear()
                
            # Locomotion Extensions
            elif action == "strafe_left":
                 is_moving.set()
                 try: locomotion.strafe_left(steps, speed=1.5)
                 finally: is_moving.clear()
            elif action == "strafe_right":
                 is_moving.set()
                 try: locomotion.strafe_right(steps, speed=1.5)
                 finally: is_moving.clear()

            # Animation Extensions
            elif action == "wave":
                 is_moving.set()
                 try: anim.hand_wave()
                 finally: is_moving.clear()
            elif action == "tap":
                 is_moving.set()
                 try: anim.foot_tap()
                 finally: is_moving.clear()
            elif action == "nod":
                 is_moving.set()
                 try: anim.nod_yes()
                 finally: is_moving.clear()
            elif action == "shake":
                 is_moving.set()
                 try: anim.shake_no()
                 finally: is_moving.clear()
            elif action == "smh":
                 is_moving.set()
                 try: anim.smh()
                 finally: is_moving.clear()
            elif action == "roll_eyes":
                 is_moving.set()
                 try: anim.eye_roll()
                 finally: is_moving.clear()
            
            elif action in ["relax", "stand_by"]:
                    head.look_neutral()
                    time.sleep(0.5)
                    sc.relax()
            elif action in ["reset", "lay_flat"]:
                    locomotion.reset_posture_flat()

            # Vision Action
            elif action == "see":
                    logger.info(f"[EXEC] Capturing Vision (Param: {param})...")
                    is_silent = (param == "silent" or (param and "silent" in param))
                    capture_and_send_vision(silent=is_silent)
        except Exception as e:
            logger.error(f"Command Error ({action}): {e}")
            is_moving.clear()

    # Register all known commands to the dispatcher
    KNOWN_COMMANDS = [
        "walk", "walk_forward", "move_forward", "move_backward", 
        "turn_left", "turn_right", 
        "strafe_left", "strafe_right",
        "look_left", "look_right", "look_down", "look_up", "look_center", 
        "led_on", "led_off", "led_flash", 
        "buzzer_beep", "buzzer_warn", "buzzer_alarm", 
        "emote", "laugh", "bow", "wiggle", 
        "wave", "tap", "nod", "shake", "smh", "roll_eyes",
        "relax", "stand_by", "reset", "lay_flat", 
        "see", "null"
    ]

    for cmd_name in KNOWN_COMMANDS:
        # Capture current cmd_name in closure default arg
        dispatcher.register(cmd_name, lambda p, name=cmd_name: execute_hardware_command(p, name))

    def on_server_command(cmd):
        nonlocal last_activity_time
        last_activity_time = time.time()
        
        plan = cmd.get("plan")
        if plan:
            dispatcher.push_plan(plan)
        else:
            # Treat single command as size-1 plan
            dispatcher.push_plan([cmd])



    # Camera Logic (Moved to Main Scope)
    last_vision_time = 0
    def capture_and_send_vision(silent=False):
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
                    stream_audio_response(r, leds, player, on_server_command)
                    
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
                                        stream_audio_response(r, leds, player, on_server_command)
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
        player.stop()
        leds.stop()

if __name__ == "__main__":
    main()
