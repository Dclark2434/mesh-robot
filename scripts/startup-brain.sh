#!/bin/bash
# Starts the brain half of MESH on the workstation.
#
# Credentials come from src/mesh_server/.env -- do not put keys in this file.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."

if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Error: no virtual environment found. Run: python3 -m venv venv" >&2
    exit 1
fi

echo "Starting MESH brain..."
exec python -m mesh_server.app
