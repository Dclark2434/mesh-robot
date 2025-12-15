# Use NVIDIA CUDA base image for GPU support
FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive

# 1. Install System Dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    git \
    sox \
    libsox-fmt-all \
    ffmpeg \
    curl \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Set standard python3 to point to 3.11
# This is cleaner than calling python3.11 everywhere
RUN ln -s /usr/bin/python3.11 /usr/local/bin/python3
RUN ln -s /usr/bin/python3.11 /usr/local/bin/python

# 2. Setup App Directory
WORKDIR /app
COPY voice/requirements.txt .

# 3. Install Python Dependencies
# Upgrade pip first to avoid issues
RUN python3 -m pip install --upgrade pip

# Install PyTorch with specific CUDA version FIRST
RUN pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu121

# Install requirements
RUN pip install -r requirements.txt

# Install F5-TTS (often needs git if not on PyPI properly or aiming for specific commit)
# Uncomment/adjust if F5-TTS is not in requirements.txt or needs repo install
RUN pip install git+https://github.com/SWivid/F5-TTS.git

# 4. Copy Source Code
COPY voice/ .

# 5. Runtime Setup
COPY start.sh .
RUN dos2unix start.sh && chmod +x start.sh

# Expose port (default FastAPI/Uvicorn)
EXPOSE 8000

# Use start script as entrypoint
ENTRYPOINT ["./start.sh"]
