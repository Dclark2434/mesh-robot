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
| 1 | `transport.input()` | Audio and video from the robot. |
| 2 | `CameraFeedProcessor` | Swallows the video stream into a one-slot buffer; answers requests to look. |
| 3 | `AmbientVisionProcessor` | Applies scene notes between turns, where they cannot hijack a reply. |
| 4 | `ListeningStatusProcessor` | LEDs follow the user's turn, as early as possible. |
| 5 | `stt` (Deepgram) | Streaming transcription with interim results. |
| 6 | `context.user()` | Owns VAD, end-of-turn detection, muting, and turn accumulation. |
| 7 | `llm` (Gemini) | Standard multimodal API, with the `look` tool registered. |
| 8 | `ActionTagProcessor` | Directives out of the text *before* it can be spoken. |
| 9 | `VisionContextPruner` | Collapses stale images once the reply is complete. |
| 10 | `tts` (ElevenLabs) | Streaming synthesis with word alignment. |
| 11 | `transport.output()` | Audio out, and the clock that gates timestamped frames. |
| 12 | `GestureDispatcher`, `SpeakingStatusProcessor` | Downstream of that clock, so gestures land with the sound. |
| 13 | `context.assistant()` | Reply goes back into history, with auto-summarization. |

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
| `MESH_MIN_WORDS` | `2` | Words needed to start a turn. `0` starts on any sound. |
| `MESH_TURN_STOP_TIMEOUT` | `2.5` | Force-end a turn that produced no speech. |
| `MESH_SUMMARIZE_ABOVE_TOKENS` | `8000` | Context size that triggers summarization. |
| `MESH_ECHO_CANCEL` | `1` | AEC on the robot. `0` restores mic gating. |
| `MESH_AUDIO_IN_DEVICE` | default | sounddevice input index. |
| `MESH_AUDIO_OUT_DEVICE` | default | sounddevice output index. |
| `MESH_AUDIO_IN_CHANNELS` | `1` | Set to `2` for stereo-only USB capsules. |
| `MESH_DASHBOARD` | `1` | Serve the web console. |
| `MESH_DASHBOARD_PORT` | `8080` | Console port. |
| `MESH_DASHBOARD_HOST` | `0.0.0.0` | Bind address; all interfaces by default. |
| `MESH_VISION` | `1` | Offer the `look` tool. `0` makes the robot blind. |
| `MESH_KEEP_IMAGES` | `1` | Images keeping their pixels in context. |
| `MESH_AMBIENT_VISION` | `1` | Notice surroundings after travelling. |
| `MESH_AMBIENT_MODEL` | main model | Model for the one-shot scene description. |
| `MESH_CAMERA` | `1` | Publish the camera track from the robot. |
| `MESH_CAMERA_FPS` | `5` | Capture rate. Only the newest frame is ever used. |
| `MESH_CAMERA_WIDTH` / `_HEIGHT` | `1024` / `768` | Capture resolution. |
| `MESH_CAMERA_ROTATION` | `0` | Degrees, for a camera not mounted upright. |
| `MESH_CAMERA_AUTOFOCUS` | `1` | Continuous AF on sensors that have it. |
| `MESH_CAMERA_SWAP_RB` | `0` | Escape hatch if red and blue come out swapped. |
| `MESH_CAMERA_DEVICE` | `0` | OpenCV device index, USB cameras only. |

## Vision

The robot publishes a camera track continuously at 5fps, and almost none of it
reaches the model.

### The two costs, and what happens to each

**Bandwidth** stays on the LAN. Frames arrive at `CameraFeedProcessor`, which
sits immediately after `transport.input()`, overwrite a one-slot buffer, and
are **swallowed** — they are not passed downstream. Nothing else in the
pipeline ever sees them. Streaming them into the LLM instead would spend tokens
and latency on every turn for a capability that matters occasionally.

**Context** is the subtler one. An image attached to conversation history is
re-sent to the model on *every subsequent turn*, so one look would tax the rest
of the conversation forever. After each reply, `VisionContextPruner` collapses
older images into a text stand-in built from what the robot was looking for and
what it said about it:

```
[Earlier you looked to see what the user is holding, and said: "That's a blue mug"]
```

The most recent look keeps its real pixels, so follow-ups — "what colour is
it?", "is it still there?" — still work. Everything before that is a sentence.
`MESH_KEEP_IMAGES` tunes how many stay; `0` collapses all of them.

### A tool, not an action tag

I originally sketched this as a `[ACTION: see]` tag. That was wrong. Action tags
are fire-and-forget with no return path, so the model could ask to look but
never see the result in the same breath — it would have to comment on the
picture a turn later.

`look` is a **function call** instead, which is a mid-turn round trip: the model
asks, the image lands in context, inference re-runs, and it answers in the same
reply. It takes a `question` argument ("what the user is holding") which serves
two purposes — it pushes the model toward looking deliberately rather than
reflexively, and it becomes the text of the stand-in once the image is purged.

The cost is one extra LLM round trip, but only on turns where the robot
actually looks.

### The ordering problem

Gemini requires a function call to be followed by its response. An image
message injected between the two breaks that contract.

Pipecat handles this properly: a `UserImageRequestFrame` carrying the
`tool_call_id` and `result_callback` is answered with a `UserImageRawFrame`, and
the assistant aggregator places the image into context *after* the tool result,
then re-runs inference. The `look` handler therefore does **not** call
`result_callback` itself on the success path — the image delivery does, and
that is what makes the model wait for the picture instead of answering without
it.

The catch is that `LiveKitTransport` has no `request_participant_image`; only
the Daily and SmallWebRTC transports implement it. `CameraFeedProcessor`
provides that responder for LiveKit, which keeps everything else on the
framework's rails.

### Not related to the old dead code

`MultimodalAudioAggregator`, the unused class in the previous codebase, buffered
raw *audio* onto context to bypass Deepgram. It is not an early version of this
and stays deleted. Vision happens to use the same attachment mechanism and
nothing else.

### Noticing that it moved

When the robot finishes walking somewhere, it should have some idea it is
somewhere new. The obvious implementation — attach a frame to the conversation
after each move — fails badly: you say "hey Rocky" and get a paragraph about
the room. Three things cause that, and only one is a prompt problem:

- **Images are salient.** Put a picture in front of a model and it talks about
  the picture.
- **Role framing.** An image attached as a `user` message reads as *"the human
  showed me this"*, which implies it wants a response.
- **Recency.** Whatever sits nearest the end of context dominates the reply.

So the frame never enters the conversation. It goes to a cheap one-shot call
that returns a single flat clause, and only that clause is injected:

```
[ambient, just now] Your camera shows: small office, desk with two monitors.
Background awareness only -- do not mention this unless it is directly relevant.
```

Each defence maps to one of the three causes: it is text rather than a picture,
it is written as instrumentation rather than as something you said, and it is
applied **between turns** — on `BotStoppedSpeakingFrame` or
`UserStartedSpeakingFrame`, both of which land before the next user transcript
is aggregated. Your actual words are therefore always more recent than the
note. That last one is why the trigger is "finished travelling" rather than
"user started talking", and why the summarizing call is fired in the background
rather than awaited.

The prompt rule then has an easy job, because it is no longer arguing with the
format: never open a reply with an ambient observation, never make it the
subject unless asked, one brief aside about a genuinely new place at most.

**Cost.** Exactly one note exists at a time and is replaced in place, so
ambient awareness is a flat ~20 tokens for the life of the conversation rather
than several hundred re-sent every turn. Two further guards keep the API bill
down: movements within 20 seconds of the last look are ignored, and a
description that overlaps the previous one by more than 60% is discarded as
"same room". Notes expire after five minutes so the robot never reasons about
somewhere it left.

The tradeoff is that ambient awareness is *coarse* — the robot knows "office,
two monitors", not what is on the desk. That is the intended division: ambient
for grounding, `look` when the question actually needs eyes. The prompt says so
explicitly.

Only actions marked `travels` in the action registry fire this. Turning the
head changes the view but not the place, and is not worth a call.

### Capture details worth knowing

**Channel order.** LiveKit's `RGB24` buffer means literally red, green, blue in
memory. Picamera2 names its pixel formats in the *opposite* order to the bytes
they produce, so the capture is configured as `BGR888` — which is what actually
delivers RGB. Configuring the intuitive-looking `RGB888` yields BGR, and
nothing crashes; the robot just calmly describes a blue mug as red.
`MESH_CAMERA_SWAP_RB` is the escape hatch if it ever comes out wrong.

**Autofocus.** IMX708 sensors (Camera Module 3) have a focus motor, and a robot
that walks around has no fixed subject distance — focus set once at startup
goes soft the moment it moves. Continuous AF is enabled where the sensor
supports it, and the controls are simply rejected on fixed-focus modules.

**Resolution.** The default is 1024×768 rather than something smaller because a
wide-angle lens spreads the subject thin: across a 120° field of view, an object
held up at arm's length occupies a small fraction of the frame, and there is not
much of it left at 640×480 for the model to read. At 5fps the encode cost on a
Pi 4 is modest, and it is the *look* frames that this resolution buys.

### Why this was easy

Two earlier decisions paid off. The transport is WebRTC, so a camera is just
another track. And the LLM is Gemini's **standard** multimodal API rather than
the Live API — images attach to context directly, and function calling is
available. The Live API's audio-only output would have made both awkward.

## The dashboard

`http://<workstation>:8080` while the brain is running. Read-only: it watches
the robot, it cannot drive it. That is a deliberate limit for a first version —
a control surface reachable by anything on the LAN deserves its own thought
about who gets to make a robot walk.

**Both logs, side by side.** The Pi's terminal is the least convenient one in
the system: over SSH, on a machine that walks away. Its log lines are batched
every 400ms and shipped over the existing data channel, then shown in their own
pane next to the brain's. Batching matters — the robot logs a line per gait
cycle, and a packet per line would be its own performance problem. Lines
dropped when the robot outruns the link are counted and reported rather than
silently lost.

**Status.** Components are the things that can independently fail, not a tidy
architectural diagram. Two states are worth explaining:

- `unknown` (grey) means never exercised. Deepgram is not "down" before anyone
  has spoken to the robot, and showing it red would train you to ignore red.
- `stale` (purple) means something that reports periodically has stopped, which
  is different from a reported failure and usually means a link died.

The robot's own subsystems — servos, echo cancellation, camera — cannot be
observed from the workstation, so the robot announces them in a `hello` message
on connect. That is what makes "is AEC actually running?" answerable without
SSHing in, which matters because a robot with the canceller silently disabled
looks fine until you try to interrupt it.

**Latency.** The per-turn breakdown from `UserBotLatencyObserver` rendered as
bars plus a sparkline of recent turns. This is the panel the whole rebuild was
about: it turns "it feels laggy" into "ElevenLabs took 900ms on that turn".

**Camera.** Polled as an ordinary JPEG endpoint every 5 seconds rather than
pushed down the WebSocket, which keeps the event stream text-only and lets the
browser handle caching. The preview is downscaled to 480px — it is a monitoring
thumbnail, not what the model sees.

Also shown: transcript, gestures as they fire, the current ambient note, and
battery. The page is a single file with no external assets, because a
dashboard that needs a CDN is useless on a LAN with no internet.

Events are published to a process-wide bus (`dashboard/events.py`). That is a
singleton by choice: observability is cross-cutting in the same way logging is,
and threading a dashboard reference through every pipeline processor would put
presentation concerns into code with no other reason to know about them.
Publishing with nothing subscribed is a deque append.

## Known gaps

- **Nothing prompts the robot to look on its own.** It looks when it decides a
  question needs it. It will not notice that it has been picked up and moved,
  or that someone walked in. A cheap improvement would be a look triggered by
  the first user turn after locomotion.
- **A long body action cannot be cancelled.** Gait and animation loops are
  blocking `time.sleep` sequences. Interrupting the robot stops its speech but
  not its legs. Fixing it means threading a stop flag through the animation
  loops.
- **Servo writes are unbatched.** `set_leg_angles()` issues 18 `set_angle`
  calls, each four separate SMBus byte writes — 72 I2C transactions per gait
  tick. The PCA9685 supports auto-incrementing block writes, which would cut
  that roughly fourfold and is likely the ceiling on gait smoothness today.
