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

SAMPLE_RATE = 16000 # Standard for VAD/LLM
CHANNELS = 1

class MeshWebRTCClient:
    def __init__(self):
        # Hardware
        self.leds = LEDManager()
        self.sc = ServoController()
        self.head = HeadController(self.sc)
        self.loco = LocomotionController(self.sc, None) # No IMU for simple client
        self.anim = AnimationController(self.loco, self.head)
        self.dispatcher = CommandDispatcher()
        
        # LiveKit
        self.room = rtc.Room()
        self.audio_source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
        self.audio_track = rtc.LocalAudioTrack.create_audio_track("mic", self.audio_source)
        
        # Audio Playback
        self.playback_stream = sd.OutputStream(
            samplerate=24000, # Matches Chatterbox output
            channels=1,
            dtype='int16',
            blocksize=480 # 20ms at 24kHz
        )
        self.playback_stream.start()
        
        # Register commands
        self._register_commands()

    def _register_commands(self):
        # Reuse the existing hardware command logic
        from mesh_client.main import execute_hardware_command
        # Note: We need a wrapper because main.py's execute_hardware_command 
        # depends on local variables. We'll implement a clean version here or import correctly.
        
        # For this integration, we'll map common robot actions
        commands = [
            "walk", "walk_forward", "move_forward", "move_backward", 
            "turn_left", "turn_right", "look_left", "look_right", 
            "look_down", "look_up", "look_center", "wave", "tap", 
            "nod", "shake", "smh", "roll_eyes", "laugh", "bow", "wiggle"
        ]
        
        # Simple mapping to locomotion/animation
        for cmd in commands:
            self.dispatcher.register(cmd, lambda p, c=cmd: self._handle_physical_action(c, p))

    def _handle_physical_action(self, action, param):
        logger.info(f"Physical Action: {action} ({param})")
        # Logic to trigger anim/loco
        if action == "wave": self.anim.hand_wave()
        elif action == "laugh": self.anim.laugh()
        elif action == "nod": self.anim.nod_yes()
        elif action == "shake": self.anim.shake_no()
        elif action == "wiggle": self.anim.palp_wiggle()
        elif action == "look_center": self.head.look_neutral()
        # Add more as needed...

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
        logger.info("Starting Microphone capture...")
        
        def callback(indata, frames, time, status):
            if status:
                logger.warning(f"Mic Status: {status}")
            # Convert float32 to int16 for LiveKit if necessary, 
            # but sounddevice can provide int16
            asyncio.run_coroutine_threadsafe(
                self.audio_source.capture_frame(rtc.AudioFrame(indata.tobytes(), SAMPLE_RATE, CHANNELS)),
                asyncio.get_event_loop()
            )

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16', callback=callback):
            while self.room.isconnected():
                await asyncio.sleep(1.0)

    async def _play_audio(self, track):
        """Receive from LiveKit and play to sounddevice."""
        audio_stream = rtc.AudioStream(track)
        async for frame in audio_stream:
            # frame.data is bytes (int16 usually)
            self.playback_stream.write(np.frombuffer(frame.data, dtype='int16'))

    async def run(self):
        await self.connect()
        self.leds.set_state(LEDState.IDLE)
        while self.room.isconnected():
            await asyncio.sleep(1.0)

if __name__ == "__main__":
    client = MeshWebRTCClient()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
