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

MESH turns a Freenove Big Hexapod into a robot you talk to. It listens
continuously, answers in about a second, and gestures with its body in time
with its own words. No wake word, no push-to-talk. You can interrupt it
mid-sentence and it will stop and listen.

## What you need

- Freenove Big Hexapod kit with a Raspberry Pi 4/5
- A microphone and speaker on the robot, ideally one USB device doing both
- A camera (Camera Module 3 or a USB webcam), optional
- A workstation on the same network for the brain
- A [LiveKit](https://github.com/livekit/livekit) server on your LAN
- API keys for Deepgram, Google Gemini and ElevenLabs

## How it works

Two processes join the same LiveKit room and stay connected.

```
   Raspberry Pi                  LiveKit room               Workstation
 mic ─► echo cancel ─────── audio ──────────────► speech-to-text
                                                       │
 speaker ◄──────────────── audio ──────────────  text-to-speech ◄─ Gemini
                                                       │
 servos, LEDs ◄─────── control messages ────────  gesture timeline
```

- Gestures fire on the word they follow, not when the model emits the tag.
- The microphone stays open while the robot talks, using WebRTC echo
  cancellation on the Pi.
- The camera streams to the workstation but frames only reach Gemini when the
  robot decides a question needs one.
- A web console at `:8080` shows both logs, component health, per-turn latency
  and a camera preview.

[ARCHITECTURE.md](ARCHITECTURE.md) covers the design and the tradeoffs.

## What it can do

Speak normally; there is no command syntax.

| | |
|---|---|
| **Move** | walk forward, back up, turn left/right, spin around |
| **Look** | left, right, up, down, back at me |
| **Express** | wave, bow, nod, shake, roll its eyes, laugh, tap a foot, wiggle |
| **Signal** | flash its light, beep, warn, sound an alarm |
| **Rest** | relax the servos, lie flat so you can pick it up |
| **See** | "what am I holding?", "what colour is this?" |

It gestures while speaking without being asked:

> "Scanning for intelligent life. `[ACTION: look_left]` `[ACTION: look_right]` ...negative."

It also remembers things between sessions — names, what you are working on —
in `data/facts.json`.

### Personalities

Three ship: `mesh`, `rocky`, `tars`. Each is a prompt file in
`src/mesh_server/personalities/`. Select one with `MESH_PERSONALITY`. To add
another, drop in a `.txt` file — the shared rules and action list are appended
automatically.

## Quick start

Full instructions, including LiveKit and the camera, are in
[GETTING_STARTED.md](GETTING_STARTED.md).

**Workstation:**

```bash
git clone https://github.com/Dclark2434/mesh-robot.git
cd mesh-robot
python3 -m venv venv && source venv/bin/activate
pip install -e ".[brain]"
cp .env.example src/mesh_server/.env    # then fill in your keys
python -m mesh_server.app
```

**Raspberry Pi:**

```bash
sudo apt install -y python3-picamera2
python3 -m venv --system-site-packages venv && source venv/bin/activate
pip install -e ".[robot,hardware]"
python -m mesh_client.app
```

Then open `http://localhost:8080`.

Every setting is listed in
[ARCHITECTURE.md](ARCHITECTURE.md#configuration).

## Development

```bash
pip install -e ".[dev]"
pytest
```

Parsing, protocol, timing, vision-context and memory logic test without the
real-time stack installed. The gait tests run real gait cycles and take a
couple of minutes.

## Acknowledgments

> "Good artists copy, great artists steal." — Pablo Picasso

**[gptars](https://www.youtube.com/@gptars)** — the original inspiration; a
ChatGPT-powered TARS replica.

**[NikodemBartnik](https://www.youtube.com/@NikodemBartnik)** — for the
standard on blending hardware, software and 3D printing.

And the Freenove team for a well-documented kit. Their reference
implementation (`freenove_code/`) is the source of the inverse-kinematics and
gait maths, licensed CC BY-NC-SA — fine for a personal project, worth knowing
if this ever becomes commercial.

## License

MIT. See `LICENSE`.
