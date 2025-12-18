# MESH 🤖
**Mobile Engineering Support Hexapod (The Brain)**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)

> [!WARNING]
> **Status: Active Development**
> This project provides the "Brain" and "Senses" for a physical hexapod robot. It is an evolving architecture, not a finished consumer product.

M.E.S.H. is a professional-grade "Brain" for hexapod robots, inspired by the TARS unit from *Interstellar*. It features a low-latency speech-to-speech pipeline, real-time voice cloning, and a modular architecture designed for high-performance inference.

---

## 🚀 Quick Start (Server)

```bash
# Clone and enter
git clone https://github.com/Dclark2434/mesh-robot.git
cd mesh-robot

# Setup environment (Python 3.11 Required)
python3.11 -m venv venv
source venv/bin/activate

# Install package and dependencies
pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121
pip install -e .[server]

# Start the brain
export GEMINI_API_KEY="your_api_key_here"
python -m mesh_server.server
```

---

## ✨ Key Features

- **🧠 Dual Brain Core**: Seamlessly toggle between Google Gemini (Cloud) and Ollama (Local).
- **🗣️ Advanced TTS**: State-of-the-art voice cloning via F5-TTS or legacy XTTS v2.
- **👂 Real-time Senses**: Whisper-powered STT for hands-free interaction.
- **👁️ Vision System**: Image analysis and commentary via the `/see` endpoint.
- **🎭 TARS Personality**: Customizable cynical, dry, and military-aware persona.
- **🛠️ Command Engine**: Integrated stubs for hardware control (walking, scanning, etc.).

---

## 📂 Project Structure

The repository follows a professional `src` layout for better package management and testing.

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

## 🛠️ Configuration

Control M.E.S.H. via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_GEMINI` | `True` | Use Google Gemini Flash (Cloud). `False` for local Ollama. |
| `USE_F5_TTS` | `True` | Use F5-TTS (SOTA). `False` for XTTS v2. |
| `GEMINI_API_KEY`| - | **Required** for Cloud Brain. |

> [!IMPORTANT]
> Ensure `GEMINI_API_KEY` is set in your environment if `USE_GEMINI` is enabled.

---

## 💻 Setup & Installation

### System Requirements
- **OS**: Linux (WSL2 recommended for Windows users).
- **Python**: 3.11 (3.12+ currently incompatible with TTS libraries).
- **GPU**: NVIDIA GPU with CUDA 12.1 (8GB+ VRAM recommended).
- **Dependencies**: `ffmpeg`, `sox`, `libsox-fmt-all`.

### 1. Server Installation (The Brain)
Runs on your high-end workstation or server.

```bash
# Install system deps
sudo apt update && sudo apt install python3.11-venv sox libsox-fmt-all ffmpeg -y

# Setup and install
python3.11 -m venv venv
source venv/bin/activate
pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121
pip install -e .[server]

# Generate soundboard
python src/mesh_server/bake_sounds.py
```

### 2. Client Installation (The Interface)
Runs on the robot (Pi) or a debug machine (Windows).

```bash
# Setup environment
python -m venv venv-win
# Windows: .\venv-win\Scripts\Activate.ps1 | Linux: source venv-client/bin/activate

# Install
pip install -e .[client]

# Start client
$env:MESH_SERVER_URL="http://<SERVER_IP>:8000/interact"
python -m mesh_client.main
```

---

## 🧪 Testing

We use `pytest` for quality assurance.

```bash
# Run all tests
pytest
```

---

## 📜 License

Distributed under the MIT License. See `LICENSE` for more information.
