"""Publishing the robot's camera as a video track.

Deliberately slow. Five frames a second is plenty for a robot that is asked to
look at something once every few minutes, and every frame beyond that costs
encoder time on a Pi that is already running echo cancellation on the audio
thread. The brain keeps only the newest frame anyway.

Two capture backends, tried in order, because "the camera" means different
hardware depending on what is plugged in:

* **picamera2** for CSI ribbon cameras, which is most Pi camera modules.
* **OpenCV** for USB webcams.

If neither works the robot runs blind rather than refusing to start -- losing
sight should not cost you the conversation.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass

import numpy as np
from livekit import rtc

from mesh_common.logging import get_logger

logger = get_logger("camera")


@dataclass
class CameraConfig:
    """How the robot's camera should run.

    Attributes:
        width: Capture width in pixels.
        height: Capture height in pixels.
        fps: Frames per second to publish. The brain discards all but the
            newest, so this only needs to be fast enough that a frame is
            never stale when someone asks the robot to look.
        device: OpenCV device index, used only by the USB backend.
        enabled: Set False to run without a camera.
    """

    width: int = 640
    height: int = 480
    fps: int = 5
    device: int = 0
    enabled: bool = True


class CaptureBackend:
    """Interface a camera implementation must provide."""

    name = "none"

    def read(self) -> np.ndarray | None:
        """Grab one frame.

        Returns:
            An HxWx3 RGB array, or None if the frame could not be read.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release the device."""


class PiCameraBackend(CaptureBackend):
    """CSI camera via picamera2."""

    name = "picamera2"

    def __init__(self, config: CameraConfig) -> None:
        """Open the CSI camera.

        Args:
            config: Capture settings.

        Raises:
            ImportError: If picamera2 is not installed.
            Exception: If no CSI camera is attached.
        """
        from picamera2 import Picamera2

        self._camera = Picamera2()
        self._camera.configure(
            self._camera.create_video_configuration(
                main={"size": (config.width, config.height), "format": "RGB888"}
            )
        )
        self._camera.start()
        time.sleep(0.5)  # let auto-exposure settle before the first frame

    def read(self) -> np.ndarray | None:
        """Grab one frame from the CSI camera.

        Returns:
            An HxWx3 RGB array, or None on failure.
        """
        try:
            return self._camera.capture_array()
        except Exception as exc:
            logger.debug(f"CSI capture failed: {exc}")
            return None

    def close(self) -> None:
        """Stop and release the CSI camera."""
        try:
            self._camera.stop()
            self._camera.close()
        except Exception:
            pass


class UsbCameraBackend(CaptureBackend):
    """USB webcam via OpenCV."""

    name = "opencv"

    def __init__(self, config: CameraConfig) -> None:
        """Open the USB camera.

        Args:
            config: Capture settings.

        Raises:
            ImportError: If OpenCV is not installed.
            RuntimeError: If the device cannot be opened.
        """
        import cv2

        self._cv2 = cv2
        self._capture = cv2.VideoCapture(config.device)
        if not self._capture.isOpened():
            raise RuntimeError(f"camera {config.device} would not open")
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
        # A large driver buffer means the robot looks at the past.
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> np.ndarray | None:
        """Grab one frame from the USB camera.

        Returns:
            An HxWx3 RGB array, or None on failure.
        """
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        return self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        """Release the USB camera."""
        try:
            self._capture.release()
        except Exception:
            pass


def open_camera(config: CameraConfig) -> CaptureBackend | None:
    """Open whichever camera is actually attached.

    Args:
        config: Capture settings.

    Returns:
        A working backend, or None if no camera could be opened.
    """
    if not config.enabled:
        logger.info("Camera disabled by config")
        return None

    for backend in (PiCameraBackend, UsbCameraBackend):
        try:
            opened = backend(config)
            logger.info(
                f"Camera open via {opened.name} at {config.width}x{config.height} @{config.fps}fps"
            )
            return opened
        except ImportError:
            logger.debug(f"{backend.name} not installed")
        except Exception as exc:
            logger.debug(f"{backend.name} unavailable: {exc}")

    logger.warning("No camera found; the robot will not be able to see")
    return None


class CameraPublisher:
    """Captures frames on a worker thread and publishes them to the room."""

    def __init__(self, config: CameraConfig) -> None:
        """Create the publisher.

        Args:
            config: Capture settings.
        """
        self._config = config
        self._backend: CaptureBackend | None = None
        self._source: rtc.VideoSource | None = None
        self._track: rtc.LocalVideoTrack | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def active(self) -> bool:
        """Whether a camera is open and publishing."""
        return self._backend is not None

    async def start(self, participant: rtc.LocalParticipant) -> bool:
        """Open the camera and publish it to the room.

        Args:
            participant: The robot's local participant.

        Returns:
            True if a video track was published.
        """
        self._backend = await asyncio.to_thread(open_camera, self._config)
        if self._backend is None:
            return False

        self._source = rtc.VideoSource(self._config.width, self._config.height)
        self._track = rtc.LocalVideoTrack.create_video_track("camera", self._source)

        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_CAMERA
        await participant.publish_track(self._track, options)

        self._stopping.clear()
        self._thread = threading.Thread(target=self._capture_loop, name="camera", daemon=True)
        self._thread.start()
        logger.info("Camera track published")
        return True

    def _capture_loop(self) -> None:
        """Push frames at the configured rate until stopped.

        Runs on a worker thread: both backends block in the driver, and doing
        that on the event loop would stall the audio pump.
        """
        interval = 1.0 / max(1, self._config.fps)
        failures = 0

        while not self._stopping.is_set():
            started = time.monotonic()
            frame = self._backend.read() if self._backend else None

            if frame is None:
                failures += 1
                if failures == 10:
                    logger.warning("Camera has stopped returning frames")
                self._stopping.wait(interval)
                continue

            failures = 0
            try:
                height, width = frame.shape[:2]
                self._source.capture_frame(
                    rtc.VideoFrame(
                        width=width,
                        height=height,
                        type=rtc.VideoBufferType.RGB24,
                        data=np.ascontiguousarray(frame).tobytes(),
                    )
                )
            except Exception as exc:
                logger.debug(f"Frame publish failed: {exc}")

            self._stopping.wait(max(0.0, interval - (time.monotonic() - started)))

    async def stop(self) -> None:
        """Stop capturing and release the camera."""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._backend is not None:
            self._backend.close()
            self._backend = None
        logger.info("Camera stopped")
