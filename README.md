# MESH
**Mobile Engineering Support Hexapod**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

> [!WARNING]
> **Status: Active Development**
> An evolving architecture, not a finished product.

<p align="center">
  <img width="256" height="384" alt="edited hexapod" src="https://github.com/user-attachments/assets/cacb2021-0fb4-4f09-95fc-123475b88816" />
</p>

MESH turns a Freenove Big Hexapod into something you talk to rather than
operate. It listens continuously, replies while you are still in the room, and
gestures with its body in time with its own words. There is no wake word, no
push-to-talk, and no turn-around time between recording and answering — one
persistent real-time session, held open for as long as the robot is on.

You can interrupt it mid-sentence and it will stop and listen.

Requires:

- Freenove Big Hexapod kit with a Raspberry Pi 4/5
- A microphone and speaker mounted on the robot
- A workstation on the same network to run the brain
- Accounts for Deepgram, Google Gemini and ElevenLabs
- A LiveKit server (the open-source one, self-hosted, on your LAN)

---

## How it works

Two processes join the same LiveKit room and stay there.

```
   Raspberry Pi                  LiveKit room               Workstation
 mic ─► echo cancel ─────── audio ──────────────► speech-to-text
                                                       │
 speaker ◄──────────────── audio ──────────────  text-to-speech ◄─ Gemini
                                                       │
 servos, LEDs ◄─────── control messages ────────  gesture timeline
```

The interesting parts, in short:

- **Gestures are timed against speech, not tokens.** A `[ACTION: wave]` tag is
  anchored to the word it follows and fired when that word actually leaves the
  speaker — about 1.5 seconds later than where it was parsed.
- **The microphone stays open while the robot talks.** WebRTC echo cancellation
  runs on the Pi, so barge-in works instead of the robot deafening itself.
- **One action registry** generates both the robot's dispatch table and the
  prompt's list of what it can do, so the two cannot drift apart.

[`ARCHITECTURE.md`](ARCHITECTURE.md) explains all of this properly, including
the latency budget and the tradeoffs taken.

---

## What it can do

Say any of this out loud; there is no command syntax.

| | |
|---|---|
| **Move** | walk forward, back up, turn left/right, spin around |
| **Look** | left, right, up, down, back at me |
| **Express** | wave, bow, nod, shake, roll its eyes, laugh, tap a foot, wiggle |
| **Signal** | flash its light, beep, warn, sound an alarm |
| **Rest** | relax the servos, lie flat so you can pick it up |

It also acts while it talks, without being asked:

> "Scanning for intelligent life. `[ACTION: look_left]` `[ACTION: look_right]` ...negative."

And it remembers things across sessions — names, what you are working on, what
you care about — via silent `[MEMORY: ...]` tags written to `data/facts.json`.

### Personalities

Three personas ship (`mesh`, `rocky`, `tars`), each a prompt file in
`src/mesh_server/personalities/`. Pick one with `MESH_PERSONALITY`. Adding a
fourth means dropping in a `.txt` file; the shared behavioural rules and the
action list are appended automatically.

---

## Setup

### 1. The brain (workstation)

```bash
git clone https://github.com/Dclark2434/mesh-robot.git
cd mesh-robot
python3.11 -m venv venv
source venv/bin/activate
pip install -e ".[brain]"
```

Create `src/mesh_server/.env`:

```bash
LIVEKIT_URL=ws://192.168.1.10:7880
LIVEKIT_API_KEY=your_key
LIVEKIT_API_SECRET=your_secret
DEEPGRAM_API_KEY=...
GEMINI_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
MESH_PERSONALITY=mesh
```

Then:

```bash
python -m mesh_server.app
```

It refuses to start with a list of what is missing rather than failing halfway
through a conversation.

### 2. The robot (Raspberry Pi)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[robot,hardware]"
```

Set the same LiveKit variables in the robot's environment, plus its audio
devices if the defaults are wrong. The client prints the device table on
startup so you can read the indices off it:

```bash
export MESH_AUDIO_IN_DEVICE=1
export MESH_AUDIO_OUT_DEVICE=0
export MESH_AUDIO_IN_CHANNELS=1   # 2 if your USB mic only opens in stereo
python -m mesh_client.app
```

Every tuning knob is listed in [`ARCHITECTURE.md`](ARCHITECTURE.md#configuration).

### 3. Autostart on boot

```bash
bash scripts/setup-autostart.sh
```

Check it with `sudo systemctl status mesh-client.service`.

---

## Audio notes

Echo cancellation needs the microphone and speaker to be on the same clock, so
prefer one USB audio device that does both. Separate devices work — the
canceller tolerates moderate drift — but converge less well.

If the robot starts answering itself, that is the canceller failing rather than
a logic bug. Check the startup log for `WebRTC audio processing enabled`, and
try `MESH_ECHO_CANCEL=0` to confirm by falling back to the old behaviour of
gating the microphone while speaking.

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

The parsing, protocol, timing and memory logic has no dependency on the
real-time stack and tests without it. Note that the gait tests genuinely run
gait cycles in real time and take a couple of minutes.

---

## Acknowledgments

> "Good artists copy, great artists steal." — Pablo Picasso

**[gptars](https://www.youtube.com/@gptars)** — the original inspiration; a
ChatGPT-powered TARS replica that made this look possible.

**[NikodemBartnik](https://www.youtube.com/@NikodemBartnik)** — for the standard
on blending hardware, software and 3D printing.

And the Freenove team, for a kit with documentation good enough to build on.
Freenove's reference implementation (`freenove_code/`) is the source of the
inverse-kinematics and gait maths, and is licensed CC BY-NC-SA — fine for a
personal project, worth knowing if this ever becomes anything commercial.

## License

MIT. See `LICENSE`.
