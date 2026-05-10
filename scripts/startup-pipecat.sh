#!/bin/bash
# startup-pipecat.sh - M.E.S.H. Pipecat Client Startup Script

# Navigate to the project root (relative to this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

# --- ENVIRONMENT & EXECUTION ---
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d "venv-client" ]; then
    source venv-client/bin/activate
else
    echo "Error: Virtual environment not found."
    exit 1
fi

# Ensure .env exists
if [ ! -f ".env" ]; then
    echo "Warning: .env file not found in $(pwd)"
fi

echo "Waiting for network and hardware..."
sleep 5

echo "Starting M.E.S.H. Pipecat Client..."
# Run using the python from the venv directly
python src/mesh_client/pipecat_client.py
