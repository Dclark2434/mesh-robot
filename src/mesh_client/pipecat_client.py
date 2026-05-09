import asyncio
import json
import os
import numpy as np
import sounddevice as sd
from livekit import rtc, api
from dotenv import load_dotenv

# Client imports
from mesh_common.logging import get_logger
from mesh_client.led_controller import LEDManager, LEDState
from mesh_client.servo_controller import ServoController, HeadController
from mesh_client.locomotion import LocomotionController
from mesh_client.animation_controller import AnimationController
from mesh_client.command_dispatcher import CommandDispatcher

logger = get_logger("pipecat_client")
load_dotenv()

# --- CONFIG ---
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "http://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secret")
ROOM_NAME = "mesh-robot-room"
PARTICIPANT_NAME = "MESH-Robot"

# Audio Hardware Indices
AUDIO_IN_DEVICE = os.getenv("MESH_AUDIO_IN_DEVICE")
if AUDIO_IN_DEVICE: AUDIO_IN_DEVICE = int(AUDIO_IN_DEVICE)
AUDIO_OUT_DEVICE = os.getenv("MESH_AUDIO_OUT_DEVICE")
if AUDIO_OUT_DEVICE: AUDIO_OUT_DEVICE = int(AUDIO_OUT_DEVICE)

SAMPLE_RATE = 16000 # Standard for VAD/LLM
CHANNELS = 1

class MeshWebRTCClient:
    def __init__(self):
        # Hardware (Deferred init)
        self.leds = None
        self.sc = None
        self.head = None
        self.loco = None
        self.anim = None
        self.dispatcher = None
        
        # LiveKit (Deferred init)
        self.room = None
        self.audio_source = None
        self.audio_track = None
        self.playback_stream = None
        self.loop = None

    async def _init_hardware(self):
        """Initialize hardware within the async loop."""
        logger.info("Initializing Hardware...")
        self.leds = LEDManager()
        self.sc = ServoController()
        self.head = HeadController(self.sc)
        self.loco = LocomotionController(self.sc, None)
        self.anim = AnimationController(self.loco, self.head)
        self.dispatcher = CommandDispatcher()
        self._register_commands()

    async def _init_livekit(self):
        """Initialize LiveKit within the async loop."""
        logger.info("Initializing LiveKit objects...")
        self.loop = asyncio.get_running_loop()
        self.room = rtc.Room()
        self.audio_source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track("mic", self.audio_source)
        
        # Audio Playback
        logger.info(f"Opening Output Stream on device: {AUDIO_OUT_DEVICE if AUDIO_OUT_DEVICE is not None else 'default'}")
        self.playback_stream = sd.OutputStream(
            samplerate=24000, 
            channels=1,
            dtype='int16',
            device=AUDIO_OUT_DEVICE
        )
        self.playback_stream.start()

    def _register_commands(self):
        # Register all known actions to the dispatcher
        commands = [
            "walk", "walk_forward", "move_forward", "move_backward", 
            "turn_left", "turn_right", "look_left", "look_right", 
            "look_down", "look_up", "look_center", "wave", "tap", 
            "nod", "shake", "smh", "roll_eyes", "laugh", "bow", "wiggle",
            "relax", "stand_by", "reset", "lay_flat"
        ]
        for cmd in commands:
            self.dispatcher.register(cmd, lambda p, c=cmd: self._handle_physical_action(c, p))

    def _handle_physical_action(self, action, param):
        import re
        import time
        logger.info(f"Command Execution: {action} (Param: {param})")
        
        # Normalize steps from param
        steps = 4
        if param:
            match = re.search(r'\d+', str(param))
            if match:
                steps = min(int(match.group()), 60)

        try:
            # Movement Actions
            if action in ["walk", "walk_forward", "move_forward", "move_backward", "turn_left", "turn_right"]:
                target_pitch = 10.0 if "forward" in action or action == "walk" else (-10.0 if "backward" in action else 0.0)
                self.loco.body_pitch = target_pitch
                
                if action in ["walk", "walk_forward", "move_forward"]:
                    self.loco.move_forward(steps, speed=1.5)
                elif action == "move_backward":
                    self.loco.move_backward(steps, speed=1.5)
                elif action == "turn_left":
                    self.loco.turn_left(steps, speed=1.5)
                elif action == "turn_right":
                    self.loco.turn_right(steps, speed=1.5)
                
                time.sleep(0.2)
                self.loco.body_pitch = 0.0
                return

            # Head Actions
            if action == "look_left": self.head.look_left()
            elif action == "look_right": self.head.look_right()
            elif action == "look_down": self.head.look_down()
            elif action == "look_up": self.head.look_up()
            elif action == "look_center": self.head.look_neutral()
            
            # Animation/Emote Actions
            elif action == "wave": self.anim.hand_wave()
            elif action == "laugh": self.anim.laugh()
            elif action == "nod": self.anim.nod_yes()
            elif action == "shake": self.anim.shake_no()
            elif action == "smh": self.anim.smh()
            elif action == "roll_eyes": self.anim.eye_roll()
            elif action == "wiggle": self.anim.palp_wiggle()
            elif action == "bow": self.anim.bow()
            
            # System Actions
            elif action in ["relax", "stand_by"]:
                self.head.look_neutral()
                time.sleep(0.5)
                self.sc.relax()
            elif action in ["reset", "lay_flat"]:
                self.loco.reset_posture_flat()
                
        except Exception as e:
            logger.error(f"Hardware Error ({action}): {e}")

    async def connect(self):
        # Generate Token
        token = (
            api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(PARTICIPANT_NAME)
            .with_name(PARTICIPANT_NAME)
            .with_grants(api.VideoGrants(room_join=True, room=ROOM_NAME))
            .to_jwt()
        )

        @self.room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info(f"Subscribed to audio from {participant.identity}")
                asyncio.create_task(self._play_audio(track))

        @self.room.on("data_received")
        def on_data_received(data_packet):
            try:
                payload = json.loads(data_packet.data)
                if payload.get("type") == "action":
                    action = payload.get("action")
                    param = payload.get("param")
                    logger.info(f"Received Remote Action: {action} ({param})")
                    self.dispatcher.push_plan([{"action": action, "param": param}])
            except Exception as e:
                logger.error(f"Failed to parse data packet: {e}")

        logger.info(f"Connecting to {LIVEKIT_URL}...")
        await self.room.connect(LIVEKIT_URL, token)
        logger.info("Connected to LiveKit Room!")
        
        # Publish mic track
        await self.room.local_participant.publish_track(self.audio_track)
        logger.info("Mic track published.")

        # Start Mic Loop
        asyncio.create_task(self._record_audio())

    async def _record_audio(self):
        """Capture from sounddevice and push to LiveKit."""
        logger.info(f"Starting Microphone capture on device: {AUDIO_IN_DEVICE if AUDIO_IN_DEVICE is not None else 'default'}...")
        
        loop = asyncio.get_running_loop()
        audio_queue = asyncio.Queue()
        frame_count = 0
        
        def callback(indata, frames, time, status):
            nonlocal frame_count
            if status:
                # In WSL, intermittent "input overflow" is common due to virtualized audio latency
                pass
            
            # Convert Stereo (2ch) to Mono (1ch) and apply digital gain
            # Multiplying by 10 to significantly boost quiet microphones
            if indata.shape[1] > 1:
                mono_data = np.clip(indata[:, 0].astype(np.int32) * 10, -32768, 32767).astype(np.int16)
            else:
                mono_data = np.clip(indata.flatten().astype(np.int32) * 10, -32768, 32767).astype(np.int16)

            # Heartbeat print
            frame_count += 1
            if frame_count % 50 == 0:
                level = np.abs(mono_data).mean()
                peak = np.abs(mono_data).max()
                print(f"Mic Heartbeat - Frame {frame_count}, Mean: {level:.2f}, Peak: {peak}")

            # Canonical LiveKit audio injection
            try:
                audio_frame = rtc.AudioFrame(
                    data=mono_data.tobytes(),
                    sample_rate=SAMPLE_RATE,
                    num_channels=CHANNELS,
                    samples_per_channel=len(mono_data)
                )
                asyncio.run_coroutine_threadsafe(self.audio_source.capture_frame(audio_frame), loop)
            except Exception as e:
                logger.error(f"Error capturing frame: {e}")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE, 
                channels=2, 
                dtype='int16', 
                device=AUDIO_IN_DEVICE,
                blocksize=320,  # 20ms chunks (320 frames at 16kHz)
                callback=callback
            ):
                while self.room.isconnected():
                    await asyncio.sleep(1.0)
                    
        except Exception as e:
            logger.error(f"Microphone Stream Error: {e}")
            print(f"CRITICAL: Mic Stream Failed: {e}")

    async def _play_audio(self, track):
        """Receive from LiveKit and play to sounddevice."""
        logger.info("Audio Playback started...")
        audio_stream = rtc.AudioStream(track)
        async for event in audio_stream:
            # Handle both AudioFrame and AudioFrameEvent
            frame = event.frame if hasattr(event, "frame") else event
            self.playback_stream.write(np.frombuffer(frame.data, dtype='int16'))

    async def shutdown(self):
        """Graceful cleanup."""
        logger.info("Shutting down client...")
        if self.room and self.room.isconnected():
            await self.room.disconnect()
        
        if self.playback_stream:
            try:
                self.playback_stream.stop()
                self.playback_stream.close()
            except:
                pass
            
        if self.sc:
            self.sc.relax()
        
        if self.leds:
            self.leds.set_state(LEDState.IDLE)
        logger.info("Cleanup complete.")

    async def run(self):
        try:
            await self._init_hardware()
            await self._init_livekit()
            await self.connect()
            self.leds.set_state(LEDState.IDLE)
            while self.room.isconnected():
                await asyncio.sleep(1.0)
        finally:
            await self.shutdown()

if __name__ == "__main__":
    # Quick Device Audit
    print("\n--- AUDIO DEVICE AUDIT ---")
    try:
        print(sd.query_devices())
    except Exception as e:
        print(f"Could not query devices: {e}")
    print(f"Target IN Device: {AUDIO_IN_DEVICE}")
    print(f"Target OUT Device: {AUDIO_OUT_DEVICE}")
    print("--------------------------\n")
    
    client = MeshWebRTCClient()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass # run()'s finally block handles it
    except Exception as e:
        logger.error(f"Main Loop Error: {e}")
