# MESH

**Mobile Engineering Support Hexapod**

A voice-controlled AI assistant for a hexapod robot. Inspired by TARS from Interstellar. Features real-time speech-to-speech interaction, voice cloning, and hardware command integration.

---

## Features

- **Dual Brain Core**: Toggle between Local LLM (Ollama) or Cloud LLM (Gemini Flash).
- **Voice Cloning**: Toggle between XTTS v2 (Local) or F5-TTS (Local/GPU).
- **Real-time Audio Streaming**: Low latency response.
- **Customizable Personality**: TARS-inspired (Cynical, Dry, Competent).
- **Hardware Command Stubs**: walk, scan, shutdown.
- **Vision Analysis**: "See" the world via camera input (`/see` endpoint).
- **Persistent Memory**: Rolling summaries (Local) or Full History (Cloud).

---

## Architecture

```
Windows Client (client.py)
    |
    | HTTP POST (audio/wav)
    v
    +-----------------------+
    | MESH SERVER (Voice)   |
    | (Docker / WSL / Pi)   |
    +-----------------------+
            |
            +-- Whisper (STT)
            |
            +-- BRAIN SELECTION (Env: USE_GEMINI):
            |     |-- TRUE:  Gemini 2.5 Flash (Cloud, Smart, Fast)
            |     +-- FALSE: Ollama + Llama 3.2 (Local, Offline)
            |
            +-- VOICE ENGINE (Env: USE_F5_TTS):
            |     |-- TRUE:  F5-TTS (Better Quality, Needs GPU)
            |     +-- FALSE: XTTS v2 (Faster, Legacy)
            |
            v
    Streaming WAV response
```

---

## Configuration Toggles

Control the brain and voice engines via Environment Variables (or `server.py` constants).

| Toggle | Variable | Default | Description |
|--------|----------|---------|-------------|
| **Brain** | `USE_GEMINI` | `True` | `True` = Google Gemini (API Key required). `False` = Local Ollama. |
| **Voice** | `USE_F5_TTS` | `True` | `True` = F5-TTS (SOTA). `False` = XTTS v2. |
| **Key** | `GEMINI_API_KEY`| - | **REQUIRED** if `USE_GEMINI=True`. |

> [!IMPORTANT]
> You must set `GEMINI_API_KEY` in your environment (or `.env` file) for the Cloud Brain to work.

---

## Requirements

### Docker (Recommended)
- Docker Desktop / Engine
- NVIDIA Container Toolkit (for GPU support)
- Gemini API Key (optional, for Cloud Brain)

### Local Python
- Python 3.11 (Strict requirement; 3.12+ breaks TTS)
- CUDA 12.1 (for PyTorch/GPU)
- `ffmpeg`, `sox`, `libsox-fmt-all` (System dependencies)
- 8GB+ VRAM recommended for local inference.

---

## Quick Start (Docker)

1. **Set your API Key** (if using Gemini):
   Create a `.env` file or export the variable:
   ```bash
   export GEMINI_API_KEY="your_key_here"
   ```

2. **Run with Compose**:
   ```bash
   docker-compose up --build
   ```
   *Note: This mounts the `voice/` directory, so code changes apply immediately.*

3. **Client (Windows)**:
   ```powershell
   cd client
   # (Setup venv if needed)
   python client.py
   ```

---

## Manual Setup (WSL/Linux)

If running without Docker:

```bash
# 1. Install System Dependencies (Ubuntu/WSL)
sudo apt update && sudo apt install python3.11 python3.11-venv sox libsox-fmt-all git -y

# 2. Setup Env
cd voice
python3.11 -m venv venv
source venv/bin/activate

# 3. Install PyTorch (CUDA 12.1)
pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121

# 4. Install Requirements
pip install -r requirements.txt

# 5. Bake Sounds (Once)
python bake_sounds.py

# 6. Start
export GEMINI_API_KEY="xyz"
python server.py
```

---

## Personality & Modelfile

The robot's personality is defined in `voice/server.py` (System Prompt).

> [!NOTE]
> `voice/Modelfile` is a mirror of the prompt in `server.py` for use with Ollama. If you edit the personality, prioritize `server.py` and sync changes to `Modelfile`.

**Current Vibe: TARS (Interstellar)**
- **Honesty**: 90% (Blunt)
- **Humor**: 75% (Dry, Sarcastic)
- **Skepticism**: 20%
- **Voice**: Professional, tired, competent. NOT a cheerful assistant.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/interact` | Audio-in (WAV), Audio-out (Stream). Main voice loop. |
| POST | `/see` | Image-in + Prompt. Returns Audio commentary. |

---

## License

MIT
