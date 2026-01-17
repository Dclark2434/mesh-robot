#!/bin/bash
# startup-client.sh - M.E.S.H. Client Startup Script

# Navigate to the project root (relative to this script)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

# --- ENVIRONMENT & EXECUTION ---
if [ -d "venv-client" ]; then
    source venv-client/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: Virtual environment not found."
    exit 1
fi

# --- CONFIGURATION ---
SERVER_WORK="172.23.142.13"
SERVER_HOME="192.168.4.89"
PORT="8000"

echo "Detecting Server Environment..."

# Check connectivity to Work Server (timeout 0.5s)
python -c "
import socket, sys
try:
    socket.create_connection(('$SERVER_WORK', $PORT), timeout=0.5)
    sys.exit(0)
except:
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    echo "Work Server Detected ($SERVER_WORK)"
    export MESH_SERVER_URL="http://$SERVER_WORK:$PORT/interact"
else
    echo "Defaulting to Home Server ($SERVER_HOME)"
    export MESH_SERVER_URL="http://$SERVER_HOME:$PORT/interact"
fi

# Ensure package is installed in editable mode if not already
# pip install -e .[client]

echo "Waiting 10s for system services..."
sleep 10

echo "Starting M.E.S.H. Client..."
python -m mesh_client.main
