#!/bin/bash
set -e

# Ensure output directories exist
mkdir -p sounds
mkdir -p data

# Check if sounds exist (~check for one known category like 'ack')
if ! ls sounds/ack* >/dev/null 2>&1; then
    echo "[STARTUP] Sounds missing. Baking soundboard... (This runs ONCE)"
    python3 bake_sounds.py
else
    echo "[STARTUP] Soundboard detected. Skipping bake."
fi

echo "[STARTUP] Starting M.E.S.H. Server..."
exec python3 -u server.py
