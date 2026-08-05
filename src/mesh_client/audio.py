"""Full-duplex audio for the robot, with acoustic echo cancellation.

This replaces the previous arrangement, where the robot muted its own
microphone for as long as the speaker was active plus a 300ms tail. That did
stop the robot from answering itself, but it also made interruption impossible:
there is no way to barge in on something that has stopped listening.

Instead the mic stays open and the echo is cancelled. ``livekit.rtc`` ships the
WebRTC audio processing module, the same AEC that every browser uses for
video calls, and exposes both halves of it: ``process_reverse_stream`` for
the audio being played, ``process_stream`` for the audio being captured. Given
both, it subtracts the speaker out of the microphone.

Capture and playback run in a single duplex callback so the two streams are
sample-aligned, which is what the canceller needs. Everything runs at 16kHz
mono in 10ms frames: 16kHz because that is what the server's VAD and end-of-turn
model want, and 10ms because the processing module accepts nothing else.

Two hacks disappear as a consequence. The old code multiplied the microphone by
ten to compensate for a quiet capsule, which clipped loud speech into
distortion and cost transcription accuracy; automatic gain control does that
job properly. And the old code inferred "the robot is talking" by watching the
amplitude of incoming frames; the server now says so explicitly.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

import numpy as np
import sounddevice as sd
from livekit import rtc

from mesh_common.logging import get_logger

logger = get_logger("audio")

#: Everything on the wire and in the processor runs at this rate.
SAMPLE_RATE = 16000

#: The audio processing module accepts 10ms frames and nothing else.
FRAME_MS = 10
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2

#: Cap on queued playback audio. Past this the robot is talking noticeably
#: behind the conversation, and it is better to drop than to drift.
MAX_PLAYBACK_MS = 400
MAX_PLAYBACK_BYTES = SAMPLE_RATE * MAX_PLAYBACK_MS // 1000 * 2

#: How often to summarise audio glitches. Long enough that an occasional
#: one stays quiet, short enough to catch a burst while it is happening.
XRUN_REPORT_SECS = 20.0

#: Glitches per second above which the echo canceller cannot be expected
#: to hold alignment.
XRUN_RATE_CONCERNING = 0.5

_SILENCE = np.zeros(FRAME_SAMPLES, dtype=np.int16)


@dataclass
class AudioConfig:
    """Device selection for the robot's audio hardware.

    Attributes:
        input_device: sounddevice input index, or None for the default.
        output_device: sounddevice output index, or None for the default.
        input_channels: Channels the capture device insists on. Many USB
            capsules only open in stereo; channel 0 is taken as the mic.
        echo_cancellation: Enable AEC. Turning it off restores the old
            behaviour and is only useful when diagnosing the canceller itself.
    """

    input_device: int | None = None
    output_device: int | None = None
    input_channels: int = 1
    echo_cancellation: bool = True


class PlaybackBuffer:
    """Thread-safe byte queue between the network task and the audio callback."""

    def __init__(self) -> None:
        """Create an empty buffer."""
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def push(self, data: bytes) -> None:
        """Queue audio for playback, discarding the oldest if far behind.

        Args:
            data: 16-bit mono PCM at :data:`SAMPLE_RATE`.
        """
        with self._lock:
            self._buffer.extend(data)
            excess = len(self._buffer) - MAX_PLAYBACK_BYTES
            if excess > 0:
                del self._buffer[:excess]

    def pull(self, count: int) -> bytes:
        """Take exactly ``count`` bytes, zero-padding on underrun.

        Args:
            count: Number of bytes required by the audio device.

        Returns:
            Exactly ``count`` bytes of PCM.
        """
        with self._lock:
            if len(self._buffer) >= count:
                chunk = bytes(self._buffer[:count])
                del self._buffer[:count]
                return chunk
            chunk = bytes(self._buffer)
            self._buffer.clear()
        return chunk + b"\x00" * (count - len(chunk))

    def clear(self) -> None:
        """Drop all queued audio, e.g. when the robot is interrupted."""
        with self._lock:
            self._buffer.clear()


class DuplexAudio:
    """Runs the robot's microphone and speaker as one echo-cancelled stream."""

    def __init__(self, config: AudioConfig, source: rtc.AudioSource) -> None:
        """Create the duplex audio engine.

        Args:
            config: Device selection and processing options.
            source: LiveKit source that captured microphone frames are pushed
                into for publication.
        """
        self._config = config
        self._source = source
        self._playback = PlaybackBuffer()
        self._stream: sd.Stream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._capture: asyncio.Queue[bytes] | None = None
        self._pump: asyncio.Task | None = None
        self._reporter: asyncio.Task | None = None
        self._apm = self._make_apm()
        # Counted rather than logged from the callback, which runs on the
        # audio thread and must not do I/O. A dropped or duplicated block
        # shifts the alignment between what was played and what was captured,
        # which is exactly what makes the echo canceller lose convergence, so
        # these numbers are the first thing to look at when the robot starts
        # answering itself.
        self._xruns = 0
        self._xruns_reported = 0
        self._xruns_since_telemetry = 0

    def _make_apm(self) -> rtc.AudioProcessingModule | None:
        """Construct the WebRTC audio processing module.

        Returns:
            A configured module, or None if disabled or unavailable, in which
            case the caller falls back to gating the microphone.
        """
        if not self._config.echo_cancellation:
            logger.warning("Echo cancellation disabled by config")
            return None
        try:
            apm = rtc.AudioProcessingModule(
                echo_cancellation=True,
                noise_suppression=True,
                high_pass_filter=True,
                auto_gain_control=True,
            )
            logger.info("WebRTC audio processing enabled (AEC, NS, HPF, AGC)")
            return apm
        except Exception as exc:
            logger.error(f"Audio processing unavailable ({exc}); mic will be gated instead")
            return None

    @property
    def has_echo_cancellation(self) -> bool:
        """Whether acoustic echo cancellation is active."""
        return self._apm is not None

    async def start(self) -> None:
        """Open the duplex device and begin streaming.

        Raises:
            sounddevice.PortAudioError: If the device cannot be opened.
        """
        self._loop = asyncio.get_running_loop()
        self._capture = asyncio.Queue()

        self._stream = sd.Stream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            dtype="int16",
            channels=(self._config.input_channels, 1),
            device=(self._config.input_device, self._config.output_device),
            callback=self._on_audio,
        )
        self._stream.start()

        if self._apm is not None:
            # How long after we hand a sample to the driver its echo comes back
            # through the microphone. Without this the canceller is aligning
            # against the wrong part of the signal.
            latency_ms = int(sum(self._stream.latency) * 1000)
            self._apm.set_stream_delay_ms(latency_ms)
            logger.info(f"Echo path delay set to {latency_ms}ms")

        self._pump = asyncio.create_task(self._publish_loop())
        self._reporter = asyncio.create_task(self._report_xruns())
        logger.info(
            f"Duplex audio running at {SAMPLE_RATE}Hz "
            f"(in={self._config.input_device or 'default'}, "
            f"out={self._config.output_device or 'default'})"
        )

    def _on_audio(self, indata, outdata, frames: int, _time, status) -> None:
        """Handle one 10ms duplex tick on the audio thread.

        Playback is filled first so the processing module sees what is about to
        leave the speaker before it sees what arrived at the microphone.

        Args:
            indata: Captured samples, shape (frames, input_channels).
            outdata: Buffer to fill with samples for the speaker.
            frames: Number of frames in this tick.
            _time: PortAudio timing info, unused.
            status: Over/underflow flags from PortAudio.
        """
        if status:
            self._xruns += 1
            self._xruns_since_telemetry += 1

        rendered = np.frombuffer(self._playback.pull(frames * 2), dtype=np.int16)
        outdata[:, 0] = rendered

        mono = indata[:, 0] if indata.ndim > 1 else indata

        # The processing module accepts 10ms frames only. A device that hands
        # us a different block size would make it raise on every tick, so fall
        # back to passing audio through rather than losing the microphone.
        if self._apm is not None and frames == FRAME_SAMPLES:
            # The reverse frame is a copy: the processor works in place and the
            # speaker must get the audio untouched.
            self._apm.process_reverse_stream(self._as_frame(rendered.copy()))
            captured = self._as_frame(np.ascontiguousarray(mono))
            self._apm.process_stream(captured)
            payload = bytes(captured.data)
        else:
            payload = np.ascontiguousarray(mono).tobytes()

        if self._loop is not None and self._capture is not None:
            try:
                self._loop.call_soon_threadsafe(self._capture.put_nowait, payload)
            except RuntimeError:
                pass  # loop closed during shutdown; the device stops next tick

    @staticmethod
    def _as_frame(samples: np.ndarray) -> rtc.AudioFrame:
        """Wrap mono samples in a LiveKit audio frame.

        Args:
            samples: Contiguous 16-bit mono PCM.

        Returns:
            A frame the processing module can operate on in place.
        """
        return rtc.AudioFrame(
            data=bytearray(samples.tobytes()),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            samples_per_channel=len(samples),
        )

    def take_xrun_count(self) -> int:
        """Number of audio glitches since this was last called.

        Reported alongside battery so the brain can show the echo canceller as
        degraded while the audio path is actually glitching, rather than only
        after the robot has started talking over itself.

        Returns:
            Glitches since the previous call.
        """
        count, self._xruns_since_telemetry = self._xruns_since_telemetry, 0
        return count

    async def _report_xruns(self) -> None:
        """Log audio glitches periodically, loudly if they are frequent.

        Separate from the callback because logging from the audio thread would
        itself cause the glitches being counted.
        """
        while True:
            await asyncio.sleep(XRUN_REPORT_SECS)
            new = self._xruns - self._xruns_reported
            if not new:
                continue
            self._xruns_reported = self._xruns

            rate = new / XRUN_REPORT_SECS
            message = (
                f"{new} audio glitches in the last {XRUN_REPORT_SECS:.0f}s "
                f"({self._xruns} total). These break echo cancellation."
            )
            if rate >= XRUN_RATE_CONCERNING:
                logger.warning(message)
            else:
                logger.info(message)

    async def _publish_loop(self) -> None:
        """Forward processed microphone frames to LiveKit."""
        assert self._capture is not None
        while True:
            payload = await self._capture.get()
            try:
                await self._source.capture_frame(
                    rtc.AudioFrame(
                        data=payload,
                        sample_rate=SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=len(payload) // 2,
                    )
                )
            except Exception as exc:
                logger.warning(f"Dropped a mic frame: {exc}")

    async def play(self, track: rtc.Track) -> None:
        """Consume a remote audio track and queue it for the speaker.

        Args:
            track: The subscribed audio track carrying the robot's voice.
        """
        resampler: rtc.AudioResampler | None = None
        source_rate = 0

        async for event in rtc.AudioStream(track):
            frame = event.frame if hasattr(event, "frame") else event

            if frame.sample_rate != SAMPLE_RATE:
                if resampler is None or source_rate != frame.sample_rate:
                    source_rate = frame.sample_rate
                    resampler = rtc.AudioResampler(source_rate, SAMPLE_RATE, num_channels=1)
                    logger.info(f"Resampling voice {source_rate}Hz -> {SAMPLE_RATE}Hz")
                for out in resampler.push(frame):
                    self._playback.push(bytes(out.data))
            else:
                self._playback.push(bytes(frame.data))

    def flush_playback(self) -> None:
        """Drop queued speech, so an interruption takes effect immediately."""
        self._playback.clear()

    async def stop(self) -> None:
        """Stop streaming and release the device."""
        for task in (self._pump, self._reporter):
            if task is not None:
                task.cancel()
        self._pump = self._reporter = None
        if self._xruns:
            logger.info(f"{self._xruns} audio glitches over the session")
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning(f"Error closing audio device: {exc}")
            self._stream = None


def describe_devices() -> str:
    """Render the available audio devices for startup diagnostics.

    Returns:
        A human-readable device table, or an error note if enumeration fails.
    """
    try:
        return str(sd.query_devices())
    except Exception as exc:
        return f"Could not query audio devices: {exc}"
