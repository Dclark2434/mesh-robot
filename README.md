# MESH
**Mobile Engineering Support Hexapod (The Brain)**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)

> [!WARNING]
> **Status: Active Development**
> This project provides the "Brain" and "Senses" for a physical hexapod robot. It is an evolving architecture, not a finished product.

<p align="center">
  <img width="256" height="384" alt="edited hexapod" src="https://github.com/user-attachments/assets/cacb2021-0fb4-4f09-95fc-123475b88816" />
</p>

M.E.S.H. is a "Brain" for Freenove Big Hexapod robots. It features a custom speech-to-speech pipeline, on-the-fly voice cloning, and a modular architecture designed for high-performance inference. This will only work with a Freenove Big Hexapod robot with the following components:

- Raspberry Pi 3/4/5
- Added Microphone
- Added Speakers
- A beefy workstation or server with NVIDIA GPU (8GB+ VRAM recommended)

---

## Key Features

- **Dual Brain Core**: Seamlessly toggle between Google Gemini (Cloud) and Ollama (Local).
- **Pro-Grade Voice**: ElevenLabs integration for expressive speech (laughs, sighs) with seamless fallback to F5-TTS (Local and free).
- **Advanced TTS**: State-of-the-art voice cloning via F5-TTS or Chatterbox.
- **Real-time Senses**: Whisper-powered STT for hands-free interaction.
- **Vision System**: Image analysis and commentary via the `/see` endpoint.
- **Custom Identity**: Configurable cynical, dry, and military-aware persona.
- **Command Engine**: Integrated stubs for hardware control (walking, scanning, etc.).
- **Server Launcher GUI**: Launching server includes a GUI to help guide users through launching server with correct environment variables and api keys.

### Voice Engine Comparison

| Engine | Type | TTFB (Avg) | Total (Avg)* | Quality | Expressive | Requirement |
|:-------|:-----|:-----------|:-------------|:--------|:-----------|:------------|
| **Chatterbox Turbo** | Local | **1.45s** | **4.19s** | ⭐⭐⭐ (Good) | ✅ Yes | `HF_TOKEN` (Free) |
| **F5-TTS** | Local | 1.88s | 4.90s | ⭐⭐⭐⭐ (High) | ❌ No | None (Open Source) |
| **ElevenLabs** | Cloud | 1.51s | 6.00s | ⭐⭐⭐⭐⭐ (Pro) | ✅ Yes | `ELEVENLABS_API_KEY` |
| **Chatterbox 100m** | Local | 3.26s | 8.66s | ⭐⭐ (Base) | ❌ No | `HF_TOKEN` (Free) |

_*Benchmarks measured on NVIDIA 4070 Super GPU (Avg over ~12 sessions). Total time captures full generation duration._
* ElevenLabs requires a subscription.
* Chatterbox requires a free huggingface token. ([hu](https://huggingface.co/))
---

## Command Manual & Capabilities

You can control M.E.S.H. using natural language. Below are the supported capabilities and the types of phrases that trigger them.

### Movement
- **Walk**: "Walk forward 5 steps", "Move ahead".
- **Turn**: "Turn left", "Turn right", "Spin around".
- **Back up**: "Back up", "Walk backward".

### Head
- **Look**: "Look left", "Look right", "Look up", "Look down", "Look at me (center)".

### Cue Light (Back LED)
- **Control**: "Turn on your light", "Turn off the light". (Overrides speaking animation).
- **Express**: "Flash your light".

### Audio (Buzzer)
- **Beep**: "Beep once".
- **Warn**: "Give me a warning beep".
- **Alarm**: "Sound the alarm".

### Safety & Power
- **Relax**: "Relax", "Stand by", "Power down servos". (Saves battery, reduces jitter).
  - *Note: Auto-relaxes after 10s of inactivity.*
- **Reset / Lay Flat**: "Reset posture", "Lay flat". (Safe installation pose for picking up).

### Comedic Timing
The robot can "act" while speaking by embedding Action Tags in its response.
- "Scanning for intelligent life. [ACTION: LOOK_LEFT] [ACTION: LOOK_RIGHT] ...Negative."
- "Self-destruct in 3... 2... [ACTION: BUZZER_ALARM] ...Kidding."
- "Look at this mess. [ACTION: LOOK_DOWN] Disappointing."
- "Power management engaged. [ACTION: RELAX] Don't wake me."

### Expressive Audio (ElevenLabs and Chatterbox Turbo Only)
When using the elevenlabs voice engine, the robot uses audio tags to add emotion.
- `[laughing]`, `[sighs]`, `[clears throat]`, `[whispers]`.
- *Note: These are automatically stripped if the system falls back to local TTS.*

---

## Project Structure

The repository follows a `src` layout for better package management and testing.

```text
mesh-robot/
├── src/
│   ├── mesh_common/    # Shared utilities, logging, and constants
│   ├── mesh_client/    # Platform-agnostic client (Windows/Linux/Pi)
│   └── mesh_server/    # Core inference engine and voice pipeline
├── scripts/            # Deployment and automation scripts
├── tests/              # Comprehensive unit and integration tests
├── pyproject.toml      # Project configuration and metadata
└── README.md           # You are here
```

---

## Configuration

Control M.E.S.H. via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_GEMINI` | `True` | Use Google Gemini Flash (Cloud). `False` for local Ollama. |
| `USE_ELEVENLABS` | `False` | Use ElevenLabs API. `False` for local F5/XTTS. |
| `USE_F5_TTS` | `True` | Use F5-TTS (SOTA). `False` for XTTS v2 (Legacy). |
| `GEMINI_API_KEY`| - | **Required** for Cloud Brain. |
| `ELEVENLABS_API_KEY`| - | **Required** for Cloud Voice. |
| `ELEVENLABS_VOICE_ID`| - | Voice ID for ElevenLabs. |

> [!IMPORTANT]
> Ensure `GEMINI_API_KEY` is set in your environment if `USE_GEMINI` is enabled.
> Ensure `ELEVENLABS_API_KEY` is set in your environment if `USE_ELEVENLABS` is enabled.

---

## Setup & Installation

### System Requirements
- **OS**: Linux (WSL2 recommended for Windows users).
- **Python**: 3.11 (3.12+ currently incompatible with TTS libraries).
- **GPU**: NVIDIA GPU with CUDA 12.1 (8GB+ VRAM recommended).
- **Dependencies**: `ffmpeg`, `sox`, `libsox-fmt-all`, `python3.11-tk` (for GUI).

### 1. Server Installation (The Brain)
Runs on your high-end workstation or server.

```bash
# Clone the repository
git clone https://github.com/Dclark2434/mesh-robot.git
cd mesh-robot

# Install system deps
sudo apt update && sudo apt install python3.11-venv python3.11-tk sox libsox-fmt-all ffmpeg -y

# Setup and install
python3.11 -m venv venv
source venv/bin/activate
pip install -e .[server] --index-url https://download.pytorch.org/whl/cu121 --extra-index-url https://pypi.org/simple

```
> [!TIP]
> If you replace `src/mesh_server/reference.wav`, you MUST re-run `bake_sounds.py` to regenerate the system sounds in the new voice. Otherwise your robot will have split personality.

```bash
# Start the brain (GUI Launcher)
python src/mesh_server/launcher_gui.py

# OR Start via Command Line
export GEMINI_API_KEY="your_api_key_here"
python -m mesh_server.server
```

### 1.1 External Access (Windows 11 WSL)
If you are running the server on Windows 11 via WSL and want to access it from another device, you have two options:

**Run this PowerShell script as Administrator:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_network.ps1
```

The script will offer two modes:
1.  **Standard Setup (Port Proxy)**: Works on all Windows versions. Manually forwards port 8000.
2.  **Mirrored Mode (Recommended for Win11 22H2+)**: Configuring WSL to share the host IP address. Simpler and more robust.

### 2. Client Installation (The Interface)
Runs on the robot (Pi) or a debug machine (Windows).

```bash
# 1. Setup environment
python -m venv venv-client
# Windows (PowerShell): .\venv-client\Scripts\Activate.ps1
source venv-client/bin/activate

# 2. Install client-side dependencies (including hardware drivers)
pip install -e ".[client]"
# pip install -e ".[robot]" if on raspberry pi. This includes special hardware drivers!

# 3. Configure Connection (Replace <SERVER_IP> with the IP of your Brain/PC)
# Windows (PowerShell):
$env:MESH_SERVER_URL="http://<SERVER_IP>:8000/interact"
# Linux/Pi (Bash): 
export MESH_SERVER_URL="http://<SERVER_IP>:8000/interact"

# 4. Start client
python -m mesh_client.main
```

### 3. Autostart on Boot (Raspberry Pi)
To have M.E.S.H. start automatically when the Pi boots:

```bash
# Make the setup script executable
chmod +x scripts/setup-autostart.sh

# Run the installer (it handles systemd for you)
bash scripts/setup-autostart.sh
```

> [!TIP]
> Use `sudo systemctl status mesh-client.service` to verify it's running.

---

## License

Distributed under the MIT License. See `LICENSE` for more information.







