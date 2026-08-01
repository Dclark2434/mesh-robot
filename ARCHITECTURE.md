# MESH Architecture

How the robot hears, thinks, speaks and moves, and why each piece is where it is.

## Shape of the system

Two processes, one persistent WebRTC session, no request/response anywhere.

```
     Raspberry Pi                    LiveKit room                 Workstation
 ┌──────────────────┐                                        ┌──────────────────┐
 │  mic ──► AEC ────┼──── audio track ──────────────────────►│ STT ─► LLM ─► TTS│
 │  speaker ◄───────┼◄─── audio track ───────────────────────┼──────────────────│
 │  servos/LEDs ◄───┼◄─── data channel (actions, status) ────┼─ gesture timeline│
 │  battery ────────┼──── data channel (telemetry) ─────────►│                  │
 └──────────────────┘                                        └──────────────────┘
```

Both sides join the same room as participants. Nothing is polled, nothing is
recorded to disk and replayed, and there is no per-utterance connection setup.

## The brain

`src/mesh_server/app.py` builds one Pipecat pipeline:

| # | Stage | Why it sits here |
|---|-------|------------------|
| 1 | `transport.input()` | Audio from the robot's mic. |
| 2 | `ListeningStatusProcessor` | LEDs follow the user's turn, as early as possible. |
| 3 | `stt` (Deepgram) | Streaming transcription with interim results. |
| 4 | `context.user()` | Owns VAD, end-of-turn detection, muting, and turn accumulation. |
| 5 | `llm` (Gemini) | Standard text API. |
| 6 | `ActionTagProcessor` | Directives out of the text *before* it can be spoken. |
| 7 | `tts` (ElevenLabs) | Streaming synthesis with word alignment. |
| 8 | `transport.output()` | Audio out, and the clock that gates timestamped frames. |
| 9 | `GestureDispatcher`, `SpeakingStatusProcessor` | Downstream of that clock, so gestures land with the sound. |
| 10 | `context.assistant()` | Reply goes back into history, with auto-summarization. |

### Why gestures are dispatched after the output transport

This is the part worth understanding, because it is unusual.

A tag like `[ACTION: wave]` is read out of the LLM's token stream, which arrives
long before the corresponding audio does — measured ElevenLabs time-to-first-byte
on this project is about 1.5 seconds. Dispatching the gesture where it is parsed
means the robot waves, then says hello.

Three facts combine to fix that:

1. ElevenLabs' websocket returns character-level alignment for the audio it
   synthesizes.
2. Pipecat converts that into per-word `TTSTextFrame`s carrying a presentation
   timestamp.
3. `BaseOutputTransport` runs a clock task that holds any frame with a PTS until
   its presentation time, then pushes it downstream.

So `ActionTagProcessor` records *which word* each tag followed, and
`GestureDispatcher` — placed after `transport.output()` — counts word frames as
they emerge from that clock. Word N emerging means word N is being spoken. The
gesture fires against speech, not against tokens.

If word timestamps are ever unavailable (a different TTS, a fallback path), the
frames arrive without a PTS and are instead released after the preceding audio
has been queued. Sync degrades from word-accurate to roughly phrase-accurate
rather than breaking.

Gestures anchored past the final word are flushed at `BotStoppedSpeakingFrame`,
so a trailing tag still happens.

## Turn-taking

Three mechanisms stack, in increasing order of cleverness:

- **Silero VAD** decides whether there is speech at all. `min_volume` keeps
  servo whine from registering; `stop_secs=0.2` is the trailing silence window.
- **Smart Turn v3** (bundled ONNX, runs locally) decides from *content* whether
  the user is actually finished, so a mid-sentence pause no longer triggers a
  reply. This is Pipecat 1.1's default stop strategy.
- **Deepgram finalization** short-circuits the STT wait when a final transcript
  arrives early.

The latency budget behind `stop_secs`: Pipecat subtracts the VAD stop window
from its STT wait timeout, so `stop_secs` must stay below Deepgram's p99
transcript latency (~350ms) or final transcripts arrive after the turn has
already closed. 200ms leaves ~150ms of slack. Deepgram's own `endpointing` is
switched **off** — it is a second, dumber copy of a decision Smart Turn is
already making, and leaving it on only delays the final transcript.

## Echo, and why the robot can be interrupted

The robot's speaker is a few centimetres from its microphone. The original
system solved that by muting: server-side `AlwaysUserMuteStrategy` dropped all
user frames while the bot spoke, and the Pi additionally stopped reading its own
microphone. That does prevent the robot from answering itself, at the cost of
making interruption impossible — you cannot barge in on something that has
stopped listening.

Now the echo is cancelled instead. `livekit.rtc` exposes the WebRTC audio
processing module, the same AEC every browser uses. On the Pi:

- Capture and playback share **one duplex callback**, so the two streams are
  sample-aligned — which is what the canceller requires.
- Everything runs at **16kHz mono in 10ms frames**: 16kHz because that is what
  the VAD and turn model want, 10ms because the processing module accepts
  nothing else.
- `set_stream_delay_ms` is set from the device's real round-trip latency, so
  the canceller aligns against the right part of the signal.
- Noise suppression and high-pass filtering handle servo noise; automatic gain
  control replaces the previous `×10` multiply, which clipped loud speech into
  distortion and cost transcription accuracy.

The only mute that remains is `MuteUntilFirstBotCompleteUserMuteStrategy`,
covering the opening greeting — the window before the canceller has converged.
For the rest of the conversation the microphone stays open and barge-in works.

Interruption carries its own message type rather than reusing an idle status.
Both leave the robot silent, but only interruption means "discard the audio you
have not played yet"; an idle status also arrives at the normal end of a turn,
when the queued audio is the tail of a sentence and must be allowed to finish.
Without the distinction, every reply loses its last few hundred milliseconds.

**Tradeoff worth knowing:** if TTS fails outright, the first bot turn never
completes, and the mic stays muted. Set `MESH_ECHO_CANCEL=0` to fall back to
the old gated behaviour when diagnosing the canceller itself.

## Actions

`src/mesh_common/protocol.py` holds one registry, used by both processes:

- the robot builds its dispatch table from it;
- the system prompt's action list is **generated** from it.

They cannot drift. The previous prompt advertised `strafe_left`, `emote` and
`see`; none of the three existed on the client.

Each action declares a **lane** and a **duration**.

- **Lanes** (head / body / aux) run concurrently. A glance no longer waits four
  seconds behind a walk cycle, because those are different servos.
- **Durations** drive expiry. Expressive gestures carry a deadline; if the robot
  was busy when one arrived, it is dropped rather than performed late, because a
  wave three seconds after "hello" reads as a fault. Commands — walk, lie flat —
  carry no deadline, because those are instructions rather than punctuation.

## Memory

`[MEMORY: ...]` writes a short fact to `data/facts.json`, deduplicated and
capped. Facts are loaded into the system prompt at startup. Within a session the
model already has the fact in context because it just said it; the store exists
so the robot still knows your name tomorrow.

## Measuring it

Metrics are **on** by default. Each turn logs:

```
[LATENCY] user stopped -> bot speaking: 812ms
[LATENCY] DeepgramSTTService=141ms GoogleLLMService=317ms ElevenLabsTTSService=298ms
```

That breakdown is the thing to optimize against. `MESH_METRICS=0` disables it.

## Configuration

Everything lives in `src/mesh_server/.env` (brain) and the robot's environment.

| Variable | Default | Meaning |
|----------|---------|---------|
| `MESH_PERSONALITY` | `mesh` | Persona prompt to load. |
| `MESH_ROOM` | `mesh-robot-room` | LiveKit room both sides join. |
| `MESH_METRICS` | `1` | Per-turn latency breakdown. |
| `MESH_VAD_STOP_SECS` | `0.2` | Trailing silence before end of turn. |
| `MESH_VAD_CONFIDENCE` | `0.6` | Silero speech threshold. |
| `MESH_VAD_MIN_VOLUME` | `0.1` | Floor that rejects servo noise. |
| `MESH_SMART_TURN` | `1` | Semantic end-of-turn model. |
| `MESH_ECHO_CANCEL` | `1` | AEC on the robot. `0` restores mic gating. |
| `MESH_AUDIO_IN_DEVICE` | default | sounddevice input index. |
| `MESH_AUDIO_OUT_DEVICE` | default | sounddevice output index. |
| `MESH_AUDIO_IN_CHANNELS` | `1` | Set to `2` for stereo-only USB capsules. |

## Vision: not built yet, and how it should be built

The robot does not see. It publishes no camera track, and never has on this
architecture — capture existed only in the pre-streaming HTTP client. Nothing
here precludes it, and two decisions already made keep it easy:

- The transport is WebRTC, so a video track is a track alongside the audio one.
- The LLM is Gemini's **standard** multimodal API rather than the Live API, so
  images can be attached to conversation context directly. (This is a real
  benefit of that earlier decision, not just a workaround for Live's
  audio-only output.)

The shape it should take:

1. **Robot publishes video.** A `rtc.VideoSource` fed from `picamera2`, at a low
   frame rate — 5fps is plenty and leaves the Pi's CPU for the echo canceller.
   `LiveKitParams(video_in_enabled=True)` on the brain subscribes to it.

2. **Do not stream frames into the LLM.** Every frame in context costs tokens
   and latency on *every* turn. Instead keep the newest frame in a one-slot
   buffer, and attach it only when it is wanted.

3. **Wanted when?** Two triggers, no more:
   - a `see` action tag, so the model can choose to look when the conversation
     calls for it ("what am I holding?");
   - optionally, the first user turn after motion, so the robot notices it has
     been moved.

   A `[ACTION: see]` tag fits the existing registry — an `AUX`-lane action whose
   handler is on the brain rather than the robot.

4. **Where in the pipeline.** A processor between `context.user()` and `llm`
   that, when a look is pending, attaches the buffered frame to the outgoing
   context. That is the only place with both the trigger and the context in
   hand.

The failed experiment in the old code — `MultimodalAudioAggregator`, which
buffered raw *audio* onto context — is not the seed of this. That was an attempt
to bypass Deepgram, and it should stay deleted. Vision is a separate feature
that happens to use the same attachment mechanism.

## Known gaps

- **A long body action cannot be cancelled.** Gait and animation loops are
  blocking `time.sleep` sequences. Interrupting the robot stops its speech but
  not its legs. Fixing it means threading a stop flag through the animation
  loops.
- **Servo writes are unbatched.** `set_leg_angles()` issues 18 `set_angle`
  calls, each four separate SMBus byte writes — 72 I2C transactions per gait
  tick. The PCA9685 supports auto-incrementing block writes, which would cut
  that roughly fourfold and is likely the ceiling on gait smoothness today.
