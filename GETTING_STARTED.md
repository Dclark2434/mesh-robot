# Getting started

Start LiveKit, then the brain, then the robot. Each step has a check; if a
check fails, nothing after it will work.

Replace `192.168.1.10` throughout with your workstation's LAN address.

---

## 1. LiveKit

Both halves meet in a room hosted by a LiveKit server, usually on the
workstation.

Find the workstation's LAN address. The robot has to reach it, so `localhost`
is no use:

```bash
ipconfig | findstr /C:"IPv4"      # Windows
hostname -I                        # Linux
```

Pick the address on your real network. Ignore VMware adapters (often
`192.168.x.1`) and link-local ones (`169.254.x.x`).

Start the server with that address:

```bash
docker run --rm -p 7880:7880 -p 7881:7881 -p 7882:7882/udp livekit/livekit-server --dev --bind 0.0.0.0 --node-ip 192.168.1.10
```

`--dev` enables the default credentials `devkey` / `secret`.

> [!IMPORTANT]
> `--node-ip` is required when running in Docker. LiveKit tells clients where
> to send audio, and inside a container it detects its own address as
> something like `172.17.0.2`, which the robot cannot reach. Both sides then
> connect, every status light goes green, and no audio flows.
>
> Check the startup line says `"nodeIP": "192.168.1.10"`, not `172.17.0.2`.

**Check, on the Pi rather than the workstation:**

```bash
curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://192.168.1.10:7880/
```

`200` means good. If it fails, open the ports on the workstation (PowerShell as
Administrator):

```powershell
New-NetFirewallRule -DisplayName "LiveKit" -Direction Inbound -Protocol TCP -LocalPort 7880,7881 -Action Allow
New-NetFirewallRule -DisplayName "LiveKit UDP" -Direction Inbound -Protocol UDP -LocalPort 7882 -Action Allow
```

> [!NOTE]
> Under WSL with mirrored networking, the workstation cannot curl its own LAN
> address by default even when the robot can reach it fine. To change that, add
> `hostAddressLoopback=true` under `[wsl2]` in `%USERPROFILE%\.wslconfig` and
> run `wsl --shutdown`.

---

## 2. Brain (workstation)

```bash
cd mesh-robot
source venv/bin/activate
pip install -e ".[brain]"
```

Copy `.env.example` to `src/mesh_server/.env` and fill in the six required
values: the three API keys, the ElevenLabs voice id, and the LiveKit URL and
credentials.

```bash
python -m mesh_server.app
```

**Check:**

```
[INFO] Persona 'rocky' (available: mesh, rocky, tars), 0 remembered facts
[INFO] Dashboard on http://localhost:8080
[INFO] Joining ws://192.168.1.10:7880 as 'mesh-brain'
```

Missing credentials are named on startup and the process exits.

The first start can take a minute or two while it loads the turn-detection
model, longer if the repo is on a Windows drive mounted into WSL.

---

## 3. Robot (Raspberry Pi)

```bash
cd mesh-robot
sudo apt install -y python3-picamera2
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -e ".[robot,hardware]"
```

> [!IMPORTANT]
> `--system-site-packages` is required for a ribbon-cable camera. `picamera2`
> is installed by apt for the system interpreter only, so a plain venv cannot
> import it, and OpenCV cannot open a libcamera device at all.
>
> Verify with `python -c "import picamera2"`.

Create `mesh-robot/.env` on the Pi:

```bash
LIVEKIT_URL=ws://192.168.1.10:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
MESH_AUDIO_IN_DEVICE=1
MESH_AUDIO_OUT_DEVICE=1
MESH_CAMERA_ROTATION=0
```

Start it:

```bash
python -m mesh_client.app
```

**Check:**

```
[INFO] WebRTC audio processing enabled (AEC, NS, HPF, AGC)
[INFO] Echo path delay set to 62ms
[INFO] Camera open via picamera2 at 1024x768 @5fps
[INFO] Connected. Microphone live.
```

`Camera open via opencv` on a ribbon camera means picamera2 is not importable.
See above.

### Audio devices

The client prints the device table on startup. `>` marks the default input,
`<` the default output:

```
< 0 bcm2835 Headphones (0 in, 8 out)     the Pi's 3.5mm jack
> 1 USB PnP Audio Device (2 in, 2 out)   a USB sound card
```

Set `MESH_AUDIO_IN_DEVICE` and `MESH_AUDIO_OUT_DEVICE` to match your hardware.
Use the same device for both where possible; echo cancellation works best when
capture and playback share a clock.

To find which device produces sound:

```bash
python -c "
import numpy as np, sounddevice as sd, time
t = (np.sin(2*np.pi*440*np.arange(24000)/16000)*8000).astype(np.int16)
for d in (0, 1):
    print('device', d, flush=True)
    try: sd.play(t, 16000, device=d, blocking=True)
    except Exception as e: print(' failed:', e)
    time.sleep(0.5)"
```

If the camera image is upside down, set `MESH_CAMERA_ROTATION=180`.

> [!NOTE]
> Do not run the microphone through a PipeWire RNNoise filter. Noise
> suppression ahead of echo cancellation breaks the canceller. See
> [AUDIO_SETUP.md](AUDIO_SETUP.md).

---

## 4. Console

Open `http://localhost:8080`, or the workstation's address from a phone.

| Component | Expected |
|-----------|----------|
| Brain process, Pipecat pipeline | green |
| LiveKit room | green; otherwise see step 1 |
| Deepgram / Gemini / ElevenLabs | grey until the first exchange |
| Pi client | green; otherwise the robot is not connected |
| Servos / I2C | green; amber means simulated hardware |
| Echo cancellation | green; amber means interruption will not work |
| Camera | green; red means no camera |
| Battery | green |

The robot speaks a greeting when it joins. Hearing it confirms audio output.

---

## 5. Try it

| Say | Expect |
|-----|--------|
| "Hey Rocky, how are you?" | A reply in about a second; latency panel populates |
| Interrupt him mid-sentence | He stops within a few hundred ms and responds |
| "Say hello and wave at me" | The wave lands on the word, not before the sentence |
| "Walk forward three steps" | He finishes the sentence, then moves |
| "What am I holding?" | A pause while he looks, then an answer |
| "What colour is it?" | Answered from the same image, without looking again |
| "My name is X and I'm building a hexapod" | Silent; check the log for `Remembered:` |

To change persona, stop the brain, set `MESH_PERSONALITY`, and start it again.

---

## 6. Troubleshooting

| Symptom | Cause |
|---------|-------|
| Brain exits immediately | Missing credential; it names which |
| Brain waits at "Joining…" | LiveKit not running or unreachable |
| Everything green, no audio at all | LiveKit started without `--node-ip` |
| Pi client red | Robot not started, or joined a different room |
| Robot talks to itself | Echo cancellation not running |
| Cannot interrupt him | Echo cancellation not running |
| He replies to things you did not say | Speaker too loud, or background noise |
| Long silence before replies | Check which service in the latency panel |
| He reads `[ACTION: wave]` aloud | Tag format the parser did not match |
| Gestures fire before the words | Word timestamps unavailable from TTS |
| `Camera open via opencv` on a ribbon camera | venv built without `--system-site-packages` |
| `select() timeout` from V4L2 | Same cause |
| Camera red | Run `rpicam-hello --list-cameras`; Module 3 needs Bookworm |
| Colours inverted | Set `MESH_CAMERA_SWAP_RB=1` |
| Robot log pane empty | The robot is not connected |
| Interrupted constantly by background noise | Raise `MESH_MIN_WORDS`, or set `MESH_WAKE_PHRASES` |

### Isolating a fault

| Variable | Effect |
|----------|--------|
| `MESH_ECHO_CANCEL=0` | Robot: restores microphone gating |
| `MESH_CAMERA=0` | Robot: stops publishing video |
| `MESH_VISION=0` | Brain: removes the camera tool |
| `MESH_AMBIENT_VISION=0` | Brain: stops noticing new rooms |
| `MESH_DASHBOARD=0` | Brain: no console |
| `MESH_METRICS=0` | Brain: no latency measurement |

Full settings list: [ARCHITECTURE.md](ARCHITECTURE.md#configuration).

---

## 7. Stopping

`Ctrl-C` on either side. The robot relaxes its servos and clears its LEDs on
the way out.

Restarting the robot alone is fine. The brain greets it again and resets its
state.

---

## Upgrading from the pre-streaming version

| Old | New |
|-----|-----|
| `python src/mesh_server/launcher_gui.py` | Removed; configure via `.env` |
| `python -m mesh_server.server` | `python -m mesh_server.app` |
| `python -m mesh_client.main` | `python -m mesh_client.app` |
| `pip install -e .[server]` | `pip install -e ".[brain]"` |
| `pip install -e .[client]` | `pip install -e ".[robot,hardware]"` |
| `MESH_SERVER_URL` | Not used; both halves join a LiveKit room |

These settings are ignored and can be deleted: `USE_GEMINI`, `USE_ELEVENLABS`,
`USE_F5_TTS`, `TTS_ENGINE`, `CHATTERBOX_MODEL`, `USE_HF_BRAIN`,
`HF_BRAIN_MODEL_ID`, `USE_PIPECAT`.
