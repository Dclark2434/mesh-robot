#!/bin/bash
# startup-client.sh - M.E.S.H. Client Startup Script

# Navigate to the project root (relative to this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

# --- CONFIGURATION ---
# Replace with your WSL/Server IP
export MESH_SERVER_URL="http://192.168.4.89:8000/interact"
export MESH_ALSA_DEVICE="plughw:3,0"

# --- EXECUTION ---
if [ -d "venv-client" ]; then
    source venv-client/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found."
    exit 1
fi

# Ensure package is installed in editable mode if not already
# pip install -e .[client]

echo "Waiting 10s for system services..."
sleep 10

echo "Starting M.E.S.H. Client..."
python -m mesh_client.main
