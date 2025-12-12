# M.E.S.H.

**Mobile Engineering Support Hexapod**

A voice-controlled AI assistant for a hexapod robot. Inspired by TARS from Interstellar. Features real-time speech-to-speech interaction, voice cloning, and hardware command integration.

---

## Features

- Voice cloning via XTTS v2 with reference audio
- Real-time audio streaming (low latency)
- Customizable personality (humor, honesty, skepticism sliders)
- Wake word activation ("Hey Mesh")
- Hardware command stubs (walk, scan, shutdown)
- Vision analysis endpoint

---

## Architecture

```
Windows Client (client.py)
    |
    | HTTP POST (audio/wav)
    v
Linux/WSL Server (server.py)
    |
    +-- Whisper (STT)
    +-- Ollama + Llama 3.2 (LLM)
    +-- XTTS v2 (TTS)
    |
    v
Streaming WAV response
```

---

## Requirements

| Dependency | Notes |
|------------|-------|
| Python 3.10+ | |
| CUDA 12.1 | GPU acceleration |
| Ollama | Local LLM runtime |
| SoX | Audio effects (`apt install sox libsox-fmt-all`) |
| ~8GB VRAM | Whisper + XTTS v2 |

---

## Setup

### Server (WSL/Linux)

```bash
cd voice
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create the Ollama model
ollama create mesh -f Modelfile

# Generate pre-baked acknowledgment sounds
python bake_sounds.py

# Start the server
python server.py
```

### Client (Windows)

```powershell
python -m venv win_env
.\win_env\Scripts\Activate.ps1
pip install sounddevice numpy scipy requests
python client.py
```

---

## Project Structure

```
mesh-robot/
├── client.py           # Windows voice client (HTTP)
├── ear_client.py       # Windows voice client (file-based)
├── voice/
│   ├── server.py       # FastAPI server
│   ├── brain.py        # Standalone processor (file-based)
│   ├── bake_sounds.py  # Pre-generate sound effects
│   ├── Modelfile       # Ollama persona config
│   ├── requirements.txt
│   └── tars_ref.wav    # Voice reference
└── README.md
```

---

## API

### POST /interact

Voice interaction. Accepts WAV, returns streaming WAV.

```bash
curl -X POST http://localhost:8000/interact \
  -F "audio_file=@command.wav" \
  --output response.wav
```

### POST /see

Vision analysis. Accepts image + prompt, returns audio.

```bash
curl -X POST http://localhost:8000/see \
  -F "image=@photo.jpg" \
  -F "prompt=Analyze this." \
  --output response.wav
```

---

## Personality Settings

Defined in `Modelfile` and `server.py`:

| Setting | Default | Effect |
|---------|---------|--------|
| Humor | 75% | Cynical, dry |
| Honesty | 90% | Blunt |
| Skepticism | 20% | Doubts human logic |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| CUDA OOM | Use Whisper `tiny` model |
| `sox` not found | `apt install sox libsox-fmt-all` |
| Connection refused | Check server is running, port 8000 open |

---

## License

MIT
