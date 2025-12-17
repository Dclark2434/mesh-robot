# MESH

**Mobile Engineering Support Hexapod (The Brain)**

> ⚠️ **Status: Active Development**
> This project is providing the "Brain" and "Senses" for a physical hexapod robot. It is not just a desktop assistant. Features are missing, and the architecture is evolving.

Verified "Brain" for a hexapod robot, inspired by TARS from Interstellar. Features real-time speech-to-speech interaction, voice cloning, and hardware command integration.

---

## Roadmap

- [x] **Core Brain**: LLM Integration (Gemini/Ollama)
- [x] **Voice**: Speech-to-Speech pipeline (Whisper -> LLM -> F5-TTS)
- [x] **Vision**: Static image analysis (`/see`)
- [ ] **Hardware Integration**:
    - [ ] Connect Physical Servos (Hexapod movement)
    - [ ] Real-time Camera Feed integration
- [ ] **Architecture Migration**:
    - [x] Windows Client (Debug/Testing)
    - [x] Raspberry Pi Client (Target Payload on Robot)
- [ ] **Telemetry**: Battery monitoring and sensor fusion

---

## Features

- **Dual Brain Core**: Toggle between Local LLM (Ollama) or Cloud LLM (Gemini Flash).
- **Voice Cloning**: F5-TTS (Default/SOTA) or XTTS v2 (Legacy).
- **Real-time Audio Streaming**: Low latency response.
- **Customizable Personality**: TARS-inspired (Cynical, Dry, Military Jargon).
- **Hardware Command Stubs**: walk, scan, shutdown.
- **Vision Analysis**: "See" the world via camera input (`/see` endpoint).
- **Persistent Memory**: Rolling summaries (Local) or Full History (Cloud).

---

## Architecture

**Current State:**
The client can run on either a **Windows PC** (for debugging) or a **Raspberry Pi** (for robot deployment). Both clients handle Audio I/O and communicate with the powerful Server (Brain) over the network.

```
[ PHYSICAL ROBOT ]                   [ LOCAL SERVER (The Brain) ]
(Raspberry Pi / Windows Client)      (High-End PC / GPU)
       |                                      |
       |-- Microphone (Input)  -------------> | --+ Whisper (STT)
       |                                      |
       |-- Speaker (Output)    <------------- | --+ VOICE ENGINE (F5-TTS)
       |                                      |
       +-- Hardware Commands   <------------- | --+ BRAIN (Gemini / Ollama)
           (Servos/Sensors)                   |
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

### Local Python (Recommended)
- Python 3.11 (Strict requirement; 3.12+ features break TTS)
- CUDA 12.1 (for PyTorch/GPU)
- `ffmpeg`, `sox`, `libsox-fmt-all` (System dependencies)
- 8GB+ VRAM recommended for local inference.

### Docker (Experimental)
> ⚠️ **Warning**: The Docker build is currently unstable/experimental. It is recommended to use the manual setup below for now.

- Docker Desktop / Engine
- NVIDIA Container Toolkit

---

## Setup: Manual (WSL/Linux)

**Primary Method.** Run the server directly on your machine.

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
export GEMINI_API_KEY="your_key_here"
python server.py
```

### Setup: Client (Windows Debug)

```powershell
python -m venv win_env
.\win_env\Scripts\Activate.ps1
pip install sounddevice numpy scipy requests colorama
python client.py
```

### Setup: Client (Raspberry Pi)

```bash
# 1. Install System Dependencies
sudo apt update && sudo apt install -y alsa-utils libportaudio2 libasound2-dev

# 2. Setup Env
python3 -m venv venv-client
source venv-client/bin/activate

# 3. Install Requirements
pip install -r requirements-client.txt

# 4. Run (Set MESH_SERVER_URL if running remotely)
export MESH_SERVER_URL="http://<SERVER_IP>:8000/interact"
python pi-client.py
```

---

## Setup: Docker (Experimental)

1. **Set your API Key**:
   Create a `.env` file just in case.

2. **Run with Compose**:
   ```bash
   docker-compose up --build
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
- **Voice**: Professional, tired, competent. Uses military jargon ("Copy", "Roger").

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/interact` | Audio-in (WAV), Audio-out (Stream). Main voice loop. |
| POST | `/see` | Image-in + Prompt. Returns Audio commentary. |

---

## License

MIT
