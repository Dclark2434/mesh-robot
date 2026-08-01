#!/bin/bash
# Starts the robot half of MESH on the Raspberry Pi.
#
# Replaces the previous startup-client.sh (linear HTTP architecture) and
# startup-pipecat.sh (streaming). There is one client now.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d "venv-client" ]; then
    source venv-client/bin/activate
else
    echo "Error: no virtual environment found. Run: python3 -m venv venv" >&2
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "Warning: no .env found in $(pwd); LiveKit connection will use defaults" >&2
fi

# I2C, the audio devices and the network are not always up when systemd
# reaches us.
echo "Waiting for hardware and network..."
sleep 5

echo "Starting MESH robot..."
exec python -m mesh_client.app
