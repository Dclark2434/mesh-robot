# Audio setup on the robot

The robot now runs WebRTC audio processing in-process: echo cancellation, noise
suppression, a high-pass filter, and automatic gain control, all from
`livekit.rtc.AudioProcessingModule`. This has consequences for how the Pi's
audio stack should be configured.

## Use the raw capture device

> [!IMPORTANT]
> **Turn off the PipeWire RNNoise filter** that earlier versions of this project
> relied on, and point the robot at the raw hardware source.

Noise suppression applied *before* echo cancellation is actively harmful. The
canceller works by modelling the path from what was played to what came back,
and that model assumes the path is roughly linear. RNNoise is a neural
suppressor that reshapes the signal non-linearly, so the echo arriving at the
canceller no longer resembles what was sent. Cancellation degrades, and the
robot starts hearing itself again.

The processing module does noise suppression itself, in the correct order.

To check what the default source is:

```bash
wpctl status
```

If a filtered source such as `rnnoise_source` is starred, set the default back
to the hardware capture device:

```bash
wpctl set-default <hardware-source-id>
```

Alternatively, bypass the default entirely by setting `MESH_AUDIO_IN_DEVICE` to
the hardware device's index. The robot prints the device table on startup.

## One device is better than two

Echo cancellation aligns two streams in time, so it works best when capture and
playback share a clock. A single USB audio device doing both is ideal. Separate
devices drift relative to each other; WebRTC tolerates moderate drift but
converges less well.

## Verifying

Record and play back through the default source to confirm the hardware works
at all:

```bash
pw-record test.wav   # talk, then Ctrl+C
pw-play test.wav
```

Then start the robot and look for this line:

```
[INFO] WebRTC audio processing enabled (AEC, NS, HPF, AGC)
[INFO] Echo path delay set to 62ms
```

If you see the fallback warning instead, the microphone is being gated while
the robot speaks, and barge-in will not work:

```
[WARNING] Running without echo cancellation. The robot may hear itself.
```

## Audio glitches

The robot counts buffer under- and overruns and reports them every twenty
seconds:

```
[WARNING] 14 audio glitches in the last 20s (37 total). These break echo
          cancellation.
```

Each one shifts the alignment between what was played and what was captured,
which is precisely what the echo canceller depends on. A steady trickle is
normal; a burst is the usual explanation for the robot suddenly starting to
answer its own voice, and the count is also sent to the brain, so the **Echo
cancellation** light in the console turns amber while it is happening.

Common causes on a Pi: USB bandwidth shared with a camera, CPU contention, or
a power supply that dips under servo load.

## If the robot answers itself

That is the canceller failing, not a logic bug. In order of likelihood:

1. A noise filter is still in front of the capture device (see above).
2. Capture and playback are on separate devices with significant clock drift.
3. The speaker is loud enough to saturate the microphone preamp. The canceller
   cannot subtract what was clipped on the way in, so turn the volume down.
4. Reported device latency is wrong, so `set_stream_delay_ms` is misaligned.

To confirm it is the canceller rather than something else, set
`MESH_ECHO_CANCEL=0`. That restores the old behaviour of muting the microphone
while the robot speaks: self-triggering will stop, and so will interruption.
