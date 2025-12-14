# MESH

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
- Persistent memory with rolling summaries

---

## Architecture

```
Windows Client (client.py)
    |
    | HTTP POST (audio/wav)
    v
Linux/WSL Server (voice/server.py)
    |
    +-- Whisper (STT)
    +-- BRAIN SELECTION:
    |     |-- LOCAL: Ollama + Llama 3.2
    |     +-- CLOUD: Gemini Flash 2.5
    |
    +-- VOICE ENGINE:
    |     |-- F5-TTS
    |     +-- XTTS v2
    |
    v
Streaming WAV response
```

---

## Requirements

| Dependency | Notes |
|------------|-------|
| Python 3.11 | Required (3.12 has TTS compatibility issues) |
| CUDA 12.1 | GPU acceleration |
| Ollama | Local LLM runtime (optional if using Gemini) |
| Gemini API Key | Required if using Cloud Brain |
| SoX | Audio effects (`apt install sox libsox-fmt-all`) |
| ~8GB VRAM | For local inference |

---

## Setup

### Server (WSL/Linux)

```bash
# 1. Install System Dependencies (Ubuntu/WSL)
sudo apt update
sudo apt install python3.11 python3.11-venv sox libsox-fmt-all git -y

# 2. Setup Project
cd voice
python3.11 -m venv venv
source venv/bin/activate

# Install PyTorch with CUDA support first
pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121

# Then install remaining dependencies
pip install -r requirements.txt

# Create the Ollama model (if using local brain)
ollama create mesh -f Modelfile

# Generate pre-baked acknowledgment sounds
python bake_sounds.py

# Start the server
# (Ensure GEMINI_API_KEY is set in env if using Gemini)
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
├── voice/
│   ├── server.py       # FastAPI server (The Brain & Voice)
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
| Humor | 75% | Cynical, dry, sometimes sarcastic |
| Honesty | 90% | Blunt |
| Skepticism | 20% | Doubts human logic |

---

## Memory

Conversation history persists across restarts via `mesh_memory.json`. When context exceeds 12k tokens, older history is summarized via LLM and prepended to the system prompt.

| Config | Default | Description |
|--------|---------|-------------|
| `CONTEXT_THRESHOLD` | 12000 | Trigger summarization at this size |
| `SUMMARY_MAX_LENGTH` | 500 | Max chars for rolling summary |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| CUDA OOM | Use Whisper `tiny` or switch to Cloud Brain |
| `sox` not found | `apt install sox libsox-fmt-all` |
| Connection refused | Check server is running, port 8000 open |
| Gemini Error | Check `GEMINI_API_KEY` env var |

---

## License

MIT
