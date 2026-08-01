# Talking to Rocky again

A runbook for starting the system and checking that everything works. Written
for someone who has been away from this project for a while — the commands
changed when the pipeline was rebuilt, so the ones in your shell history will
not work.

---

## 1. What changed since last time

| Then | Now |
|------|-----|
| `python src/mesh_server/launcher_gui.py` | The GUI is gone. Configuration is `src/mesh_server/.env`. |
| `python -m mesh_server.server` | `python -m mesh_server.app` |
| `python -m mesh_client.main` | `python -m mesh_client.app` |
| `python src/mesh_client/pipecat_client.py` | same as above — one client now |
| `pip install -e .[server]` | `pip install -e ".[brain]"` |
| `pip install -e .[client]` | `pip install -e ".[robot,hardware]"` |
| `MESH_SERVER_URL=http://...` | Not used. Both halves meet in a LiveKit room. |

**Settings that no longer do anything.** These are still in your `.env` and are
now ignored — local TTS and the Ollama brain are out of scope, and the pipeline
is no longer optional:

```
USE_GEMINI  USE_ELEVENLABS  USE_F5_TTS  TTS_ENGINE
CHATTERBOX_MODEL  USE_HF_BRAIN  HF_BRAIN_MODEL_ID  USE_PIPECAT
```

Harmless to leave. Delete them if you want a tidy file.

**Your `.env` is otherwise ready.** All five required keys are set and the
persona is already `rocky`.

---

## 2. Start LiveKit

Both halves meet in a room hosted by a LiveKit server. First find the
workstation's LAN address — the robot has to reach it, so `localhost` is no use:

```bash
ipconfig | findstr /C:"IPv4"
```

Take the one on your real network (`192.168.4.x`), not the VMware
(`192.168.156.1`, `192.168.17.1`) or link-local (`169.254.x`) adapters.

Then start the server, **substituting that address**:

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev --bind 0.0.0.0 --node-ip 192.168.4.51
```

> [!IMPORTANT]
> `--node-ip` is not optional when running in Docker. LiveKit is an SFU: it
> tells clients where to send their audio, and inside a container it detects
> its own address as something like `172.17.0.2`. The robot cannot reach that.
> The room connects, both sides appear healthy, and **no audio ever flows** —
> which looks like a broken microphone rather than a networking problem.
>
> Check the startup line. `"nodeIP": "172.17.0.2"` is wrong;
> `"nodeIP": "192.168.4.51"` is right.

`--dev` is what makes `devkey` / `secret` valid.

### Check the robot can actually reach it

Testing from the workstation is not a real test. **Run this on the Pi:**

```bash
curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://192.168.4.51:7880/
```

`200` means you are good. Anything else and nothing downstream will work.

If it fails, open the ports on the workstation (PowerShell as Administrator):

```powershell
New-NetFirewallRule -DisplayName "LiveKit" -Direction Inbound -Protocol TCP -LocalPort 7880,7881 -Action Allow
New-NetFirewallRule -DisplayName "LiveKit UDP" -Direction Inbound -Protocol UDP -LocalPort 7882 -Action Allow
```

> **On WSL with mirrored networking**, the workstation itself may not be able to
> curl its own LAN address even while everything is fine — the host cannot loop
> back to a WSL-bound port by default. Add `hostAddressLoopback=true` under
> `[wsl2]` in `%USERPROFILE%\.wslconfig` and run `wsl --shutdown` if you want
> that to work. It does not affect the robot either way.

---

## 3. Start the brain (workstation)

```bash
cd /path/to/mesh-robot
source venv/bin/activate
pip install -e ".[brain]"
python -m mesh_server.app
```

What good looks like:

```
[INFO] Persona 'rocky' (available: mesh, rocky, tars), 0 remembered facts
[INFO] Dashboard on http://localhost:8080
[INFO] Joining ws://192.168.4.80:7880 as 'mesh-brain'
```

If credentials are missing it says exactly which ones and exits, rather than
failing halfway through a conversation.

> **Running the brain inside WSL?** The robot has to reach it, and the LiveKit
> server has to be reachable from WSL. If either fails, run
> `scripts/setup_network.ps1` as Administrator and pick mirrored mode.

---

## 4. Start the robot (Pi)

```bash
cd ~/mesh-robot
source venv/bin/activate
pip install -e ".[robot,hardware]"
sudo apt install -y python3-picamera2      # Camera Module 3
python -m mesh_client.app
```

It prints the audio device table on startup. If the defaults are wrong, note
the indices and set them:

```bash
export MESH_AUDIO_IN_DEVICE=1
export MESH_AUDIO_OUT_DEVICE=0
```

What good looks like:

```
[INFO] WebRTC audio processing enabled (AEC, NS, HPF, AGC)
[INFO] Echo path delay set to 62ms
[INFO] Camera open via picamera2 at 1024x768 @5fps
[INFO] Connected. Microphone live.
```

> **One-time check.** Turn off the PipeWire RNNoise filter if it is still the
> default source — noise suppression in front of the echo canceller makes the
> echo non-linear and breaks cancellation. Run `wpctl status`; if a filtered
> source is starred, point `MESH_AUDIO_IN_DEVICE` at the raw hardware instead.
> See [AUDIO_SETUP.md](AUDIO_SETUP.md).

---

## 5. Open the console

**http://localhost:8080** — or the workstation's IP from your phone.

Before saying anything, confirm the status panel:

| Component | Should be | If not |
|-----------|-----------|--------|
| Brain process | green | — |
| Pipecat pipeline | green | — |
| LiveKit room | green | step 2 |
| Deepgram / Gemini / ElevenLabs | **grey** | expected — they turn green after the first exchange |
| Pi client | green | the robot is not connected; check step 4 |
| Servos / I2C | green | amber means it is running simulated hardware, not on the Pi |
| Echo cancellation | green | **amber means no barge-in** — see AUDIO_SETUP.md |
| Camera | green | red means no camera; `rpicam-hello --list-cameras` |
| Battery | green | — |

Rocky should greet you on connect. If you hear that, audio out works.

---

## 6. Test script

Roughly increasing ambition. Watch the console while you go.

### Basic exchange
> "Hey Rocky, how are you?"

Expect a reply within about a second. The **Response latency** panel shows the
breakdown — Deepgram, Gemini, ElevenLabs — and the three status lights turn
green. If one service is dominating, that panel is where you will see it.

### Interruption — the big one
Ask him something open-ended, then **cut him off mid-sentence.**

He should stop within a couple of hundred milliseconds and respond to the new
thing. This is what the echo cancellation bought; it was impossible before.

If he ignores you while talking, echo cancellation is not actually running —
check that light in the console.

### Gesture timing
> "Rocky, say hello and wave at me."

The wave should land **on the word**, not before the sentence starts. Gestures
appear in the console as they fire.

### Movement
> "Walk forward three steps."

He should finish the sentence before the legs move. Locomotion is deliberately
not treated as a mid-sentence gesture.

### Vision
Hold something up.
> "What am I holding?"

He calls the camera tool, so expect a beat of silence before the answer. Ask a
follow-up — *"what colour is it?"* — and he should answer from the same image
without looking again.

> **Colour check:** hold up something unmistakably red or blue. If he inverts
> them, set `MESH_CAMERA_SWAP_RB=1` on the Pi and tell me.

### Memory
> "My name is Dustin and I'm building a hexapod."

Nothing visible happens — memory tags are silent. Check `data/facts.json`, or
the console's log pane for `Remembered:`. Restart the brain and he should still
know.

### Ambient awareness — check it does *not* misbehave
Walk him to another room, then just say:
> "Hey Rocky."

He should **answer the greeting**. He may add one short aside about the new
place, but scenery must not be the subject of the reply. The current note is
shown under the camera preview.

If he leads with a description of the room, that is the failure mode this was
designed around — tell me and I will tighten it.

### Personalities
Stop the brain, set `MESH_PERSONALITY=mesh` or `tars`, start it again.

---

## 7. When something is wrong

| Symptom | Likely cause |
|---------|--------------|
| Nothing at all, brain exits immediately | Missing credential — it names which |
| Brain waits forever at "Joining…" | LiveKit not running or unreachable |
| Both sides connect, all lights green, but no audio at all | LiveKit started without `--node-ip` — see step 2 |
| Console shows Pi client red | Robot not started, or pointed at a different room |
| Robot talks to itself in a loop | Echo cancellation not running; check that light |
| Cannot interrupt him | Same cause as above |
| He answers things you did not say | Mic picking up the speaker — turn the volume down |
| Long silence before every reply | Check the latency panel to see which service |
| He reads "[ACTION: wave]" aloud | A tag form the parser missed — send me the log line |
| Gestures fire before the words | Word timestamps unavailable; check the ElevenLabs light |
| Camera red | `rpicam-hello --list-cameras`; needs Bookworm for Module 3 |
| Colours inverted | `MESH_CAMERA_SWAP_RB=1` |
| Robot log pane empty | Robot connected before the brain, or it is not running |

### Turning things off to isolate a problem

| Variable | Effect |
|----------|--------|
| `MESH_ECHO_CANCEL=0` | Robot. Restores mic gating — proves whether AEC is the problem |
| `MESH_VISION=0` | Brain. Removes the camera tool entirely |
| `MESH_AMBIENT_VISION=0` | Brain. Stops him noticing rooms |
| `MESH_CAMERA=0` | Robot. Stops publishing video |
| `MESH_DASHBOARD=0` | Brain. No console |
| `MESH_METRICS=0` | Brain. No latency measurement |

Full list of settings: [ARCHITECTURE.md](ARCHITECTURE.md#configuration).

---

## 8. Stopping

`Ctrl-C` either side. The robot relaxes its servos and parks the LEDs on the
way out, so it will not sit there straining against a pose.
