"""The robot: joins the room, streams audio, performs what the brain asks.

Runs on the Raspberry Pi. Everything hardware-specific lives behind the
controllers in this package; this module is the seam between the network
session and the machine.

Compared with the previous client this adds the buzzer and battery telemetry
(present in the older HTTP client, silently dropped when the streaming client
was written), removes the microphone gate in favour of acoustic echo
cancellation, and takes its LED state from the brain's explicit status
messages rather than by guessing from the amplitude of incoming audio.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from dotenv import load_dotenv
from livekit import api, rtc

from mesh_client.animation_controller import AnimationController
from mesh_client.audio import SAMPLE_RATE, AudioConfig, DuplexAudio, describe_devices
from mesh_client.buzzer_controller import BuzzerController
from mesh_client.camera import CameraConfig, CameraPublisher
from mesh_client.led_controller import LEDManager, LEDState
from mesh_client.locomotion import LocomotionController
from mesh_client.motion import MotionDispatcher, steps_from
from mesh_client.power_monitor import PowerMonitor
from mesh_client.remote_log import RemoteLogShipper
from mesh_client.servo_controller import HeadController, ServoController
from mesh_common.logging import get_logger
from mesh_common.protocol import (
    ACTIONS,
    HelloMessage,
    MessageType,
    MovedMessage,
    Status,
    TelemetryMessage,
    decode,
    encode,
    resolve_action,
)

logger = get_logger("robot")

#: The robot's settings file, alongside the repository root.
#:
#: Loaded with override=True, matching the brain. The default is the opposite,
#: which means a variable exported in the shell silently beats the file, and an
#: address left over from an earlier session is very hard to spot: the file
#: says one thing and the robot connects somewhere else.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE, override=True)
else:
    ENV_FILE = None
    load_dotenv(override=True)

#: How often the robot reports battery state to the brain.
TELEMETRY_PERIOD_SECS = 30.0

#: Whether being interrupted also stops whatever the robot is physically doing.
#: Set MESH_STOP_ON_INTERRUPT=0 to let motion run to completion regardless.
STOP_ON_INTERRUPT = os.getenv("MESH_STOP_ON_INTERRUPT", "1") != "0"

_LED_FOR_STATUS = {
    Status.IDLE: LEDState.IDLE,
    Status.LISTENING: LEDState.LISTENING,
    Status.THINKING: LEDState.THINKING,
    Status.SPEAKING: LEDState.SPEAKING,
    Status.ERROR: LEDState.ERROR,
}


def _device_index(name: str) -> int | None:
    """Read an audio device index from the environment.

    Args:
        name: Environment variable holding a sounddevice index.

    Returns:
        The index, or None to let PortAudio pick the default.
    """
    raw = os.getenv(name, "").strip()
    return int(raw) if raw.isdigit() else None


class Robot:
    """Owns the robot's hardware and its session with the brain."""

    def __init__(self) -> None:
        """Prepare an unstarted robot. Hardware is opened in :meth:`run`."""
        self.leds: LEDManager | None = None
        self.servos: ServoController | None = None
        self.head: HeadController | None = None
        self.legs: LocomotionController | None = None
        self.anim: AnimationController | None = None
        self.buzzer: BuzzerController | None = None
        self.power: PowerMonitor | None = None

        self.room = rtc.Room()
        self.audio: DuplexAudio | None = None
        self.camera: CameraPublisher | None = None
        self.motion = MotionDispatcher(on_finished=self._action_finished)
        self.log_shipper = RemoteLogShipper(self._publish)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = asyncio.Event()

    def _announce(self) -> str:
        """Describe this robot's actual capabilities for the brain's dashboard.

        Returns:
            An encoded hello message.
        """
        return encode(
            HelloMessage(
                hardware=bool(self.servos and self.servos.is_rpi),
                echo_cancellation=bool(self.audio and self.audio.has_echo_cancellation),
                camera=bool(self.camera and self.camera.active),
                camera_backend=self.camera.backend_name if self.camera else "none",
                actions=sorted(set(ACTIONS) - set(self.motion.missing_handlers())),
            )
        )

    def _action_finished(self, action: str) -> None:
        """Tell the brain when the robot has finished travelling.

        Called on a motion worker thread, so the send is handed back to the
        event loop.

        Args:
            action: The action that just completed.
        """
        spec = resolve_action(action)
        if spec is None or not spec.travels or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._publish(encode(MovedMessage(spec.name))), self._loop)

    async def _publish(self, payload: str) -> None:
        """Send one message to the brain, tolerating a dropped packet.

        Args:
            payload: Encoded protocol message.
        """
        try:
            await self.room.local_participant.publish_data(payload)
        except Exception as exc:
            logger.debug(f"Could not publish message: {exc}")

    # -- hardware ---------------------------------------------------------

    def _open_hardware(self) -> None:
        """Bring up every hardware controller and register its actions."""
        logger.info("Opening hardware...")
        self.leds = LEDManager()
        self.servos = ServoController()
        self.head = HeadController(self.servos)
        # One token shared by the dispatcher, the legs and the animations, so a
        # single stop request reaches whichever of them is currently moving.
        self.legs = LocomotionController(self.servos, None, cancel=self.motion.cancel)
        self.anim = AnimationController(self.legs, self.head, cancel=self.motion.cancel)
        self.buzzer = BuzzerController()
        self.power = PowerMonitor()
        self._register_actions()

        missing = self.motion.missing_handlers()
        if missing:
            logger.warning(f"Actions with no handler: {', '.join(missing)}")

    def _register_actions(self) -> None:
        """Map every protocol action to the hardware that performs it.

        Handlers all take the action's optional parameter, so the dispatcher
        never has to guess at a signature.
        """
        head, anim, legs, servos, leds, buzzer = (
            self.head,
            self.anim,
            self.legs,
            self.servos,
            self.leds,
            self.buzzer,
        )
        assert head and anim and legs and servos and leds and buzzer

        def walk(direction: str):
            """Build a handler that drives the legs in one direction.

            Args:
                direction: Locomotion method suffix.

            Returns:
                A handler taking the step-count parameter.
            """

            def run(param: str | None) -> None:
                steps = steps_from(param)
                legs.body_pitch = 10.0 if direction == "move_forward" else 0.0
                try:
                    getattr(legs, direction)(steps, speed=1.5)
                finally:
                    legs.body_pitch = 0.0

            return run

        def relax(_param: str | None) -> None:
            head.look_neutral()
            servos.relax()

        def flash(_param: str | None) -> None:
            leds.set_state(LEDState.ERROR)

        def stop(_param: str | None) -> None:
            logger.info(self.motion.abort())

        handlers = {
            "look_left": lambda p: head.look_left(),
            "look_right": lambda p: head.look_right(),
            "look_up": lambda p: head.look_up(),
            "look_down": lambda p: head.look_down(),
            "look_center": lambda p: head.look_neutral(),
            "nod": lambda p: anim.nod_yes(),
            "shake": lambda p: anim.shake_no(),
            "smh": lambda p: anim.smh(),
            "roll_eyes": lambda p: anim.eye_roll(),
            "wave": lambda p: anim.hand_wave(),
            "tap": lambda p: anim.foot_tap(),
            "tippy_tap": lambda p: anim.simple_idle_step(),
            "laugh": lambda p: anim.laugh(),
            "bow": lambda p: anim.bow(),
            "wiggle": lambda p: anim.palp_wiggle(),
            "walk_forward": walk("move_forward"),
            "move_backward": walk("move_backward"),
            "turn_left": walk("turn_left"),
            "turn_right": walk("turn_right"),
            "stop": stop,
            "relax": relax,
            "reset": lambda p: legs.reset_posture_flat(),
            "led_flash": flash,
            "buzzer_beep": lambda p: buzzer.beep(),
            "buzzer_warn": lambda p: buzzer.warn(),
            "buzzer_alarm": lambda p: buzzer.alarm(),
        }
        for name, handler in handlers.items():
            self.motion.register(name, handler)

    # -- session ----------------------------------------------------------

    def _token(self) -> str:
        """Mint a LiveKit token for this robot.

        Returns:
            A signed JWT granting join rights to the configured room.
        """
        return (
            api.AccessToken(
                os.getenv("LIVEKIT_API_KEY", ""), os.getenv("LIVEKIT_API_SECRET", "")
            )
            .with_identity(os.getenv("MESH_ROBOT_IDENTITY", "mesh-robot"))
            .with_name("MESH Robot")
            .with_grants(
                api.VideoGrants(room_join=True, room=os.getenv("MESH_ROOM", "mesh-robot-room"))
            )
            .to_jwt()
        )

    def _on_message(self, payload: dict) -> None:
        """Act on one decoded control message from the brain.

        Args:
            payload: Decoded protocol message.
        """
        kind = payload.get("type")

        if kind == MessageType.ACTION.value:
            self.motion.submit(
                payload.get("action", ""),
                payload.get("param"),
                payload.get("expires_in"),
            )
        elif kind == MessageType.INTERRUPT.value:
            # Cut off mid-sentence: drop queued audio so the robot stops now
            # rather than a jitter-buffer later.
            if self.audio:
                self.audio.flush_playback()
            # Stop the legs too. Being interrupted means someone wants
            # attention now, and a robot that carries on walking away while
            # they talk to it is not listening in any sense that matters.
            if STOP_ON_INTERRUPT:
                self.motion.abort()
        elif kind == MessageType.STATUS.value:
            try:
                status = Status(payload.get("status", "idle"))
            except ValueError:
                logger.warning(f"Unknown status '{payload.get('status')}'")
                return
            if self.leds:
                self.leds.set_state(_LED_FOR_STATUS[status])

    async def _telemetry_loop(self) -> None:
        """Report battery state to the brain until shutdown."""
        while not self._closing.is_set():
            await asyncio.sleep(TELEMETRY_PERIOD_SECS)
            if not self.power:
                continue
            try:
                reading = self.power.get_status()
            except Exception as exc:
                logger.debug(f"Power read failed: {exc}")
                continue
            # The servo rail is the one that actually strands the robot; the
            # logic rail outlives it.
            await self._publish(
                encode(
                    TelemetryMessage(
                        battery_volts=reading.get("servo_voltage"),
                        battery_percent=reading.get("servo_percent"),
                    )
                )
            )

    async def _connect(self) -> None:
        """Join the room, publish the microphone, and wire up the callbacks."""
        source = rtc.AudioSource(SAMPLE_RATE, 1)
        self.audio = DuplexAudio(
            AudioConfig(
                input_device=_device_index("MESH_AUDIO_IN_DEVICE"),
                output_device=_device_index("MESH_AUDIO_OUT_DEVICE"),
                input_channels=int(os.getenv("MESH_AUDIO_IN_CHANNELS", "1")),
                echo_cancellation=os.getenv("MESH_ECHO_CANCEL", "1") != "0",
            ),
            source,
        )

        @self.room.on("track_subscribed")
        def _on_track(track, _publication, participant) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO and self.audio:
                logger.info(f"Receiving voice from {participant.identity}")
                asyncio.create_task(self.audio.play(track))

        @self.room.on("data_received")
        def _on_data(packet) -> None:
            payload = decode(packet.data)
            if payload:
                self._on_message(payload)
            else:
                logger.warning("Discarded an unreadable control message")

        @self.room.on("disconnected")
        def _on_disconnected(*_args) -> None:
            logger.warning("Disconnected from room")
            self._closing.set()

        url = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
        # Says where the address came from, because the usual cause of "it is
        # connecting to the wrong machine" is a stale value somewhere other
        # than the file being edited.
        source = str(ENV_FILE) if ENV_FILE else "environment (no .env found)"
        logger.info(f"Connecting to {url} (from {source})...")
        await self.room.connect(url, self._token())

        track = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await self.room.local_participant.publish_track(track)

        await self.audio.start()
        if not self.audio.has_echo_cancellation:
            logger.warning(
                "Running without echo cancellation. The robot may hear itself. "
                "Check that livekit-rtc is current."
            )

        # A missing camera is not fatal: the robot converses blind rather than
        # refusing to start.
        self.camera = CameraPublisher(
            CameraConfig(
                width=int(os.getenv("MESH_CAMERA_WIDTH", "1024")),
                height=int(os.getenv("MESH_CAMERA_HEIGHT", "768")),
                fps=int(os.getenv("MESH_CAMERA_FPS", "5")),
                rotation=int(os.getenv("MESH_CAMERA_ROTATION", "0")),
                autofocus=os.getenv("MESH_CAMERA_AUTOFOCUS", "1") != "0",
                swap_red_blue=os.getenv("MESH_CAMERA_SWAP_RB", "0") != "0",
                device=int(os.getenv("MESH_CAMERA_DEVICE", "0")),
                enabled=os.getenv("MESH_CAMERA", "1") != "0",
            )
        )
        await self.camera.start(self.room.local_participant)

        logger.info("Connected. Microphone live." + ("" if self.camera.active else " No camera."))

        # Tell the brain what actually came up, so its dashboard reports this
        # robot's real state rather than assuming everything worked.
        await self._publish(self._announce())
        self.log_shipper.attach()
        self.log_shipper.start()

    # -- lifecycle --------------------------------------------------------

    async def run(self) -> None:
        """Bring everything up and stay running until interrupted."""
        try:
            self._loop = asyncio.get_running_loop()
            self._open_hardware()
            self.motion.start()
            await self._connect()
            if self.leds:
                self.leds.set_state(LEDState.IDLE)
            asyncio.create_task(self._telemetry_loop())
            await self._closing.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Park the robot safely and release every resource."""
        logger.info("Shutting down...")
        self._closing.set()
        await self.log_shipper.stop()
        self.motion.stop()

        if self.camera:
            await self.camera.stop()
        if self.audio:
            await self.audio.stop()
        if self.room.isconnected():
            await self.room.disconnect()
        if self.servos:
            self.servos.relax()
        if self.leds:
            self.leds.set_state(LEDState.IDLE)
            self.leds.stop()
        logger.info("Stopped.")


async def _main() -> None:
    """Run the robot, stopping cleanly on SIGINT and SIGTERM."""
    print("\n--- AUDIO DEVICES ---")
    print(describe_devices())
    print("---------------------\n")

    robot = Robot()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, robot._closing.set)
        except NotImplementedError:
            pass  # Windows debug runs; KeyboardInterrupt covers it there.
    await robot.run()


def main() -> None:
    """Console entry point."""
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
