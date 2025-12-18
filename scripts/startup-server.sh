#!/bin/bash
# startup-server.sh - M.E.S.H. Server Startup Script

# Navigate to the project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

# --- CONFIGURATION ---
export GEMINI_API_KEY="your_api_key_here"

# --- EXECUTION ---
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found."
    exit 1
fi

echo "Starting M.E.S.H. Server..."
python -m mesh_server.server
