#!/bin/bash
# startup-client.sh

# Navigate to the project directory
cd /home/dclark/mesh-robot

# Load environment variables
# REPLACE THESE with your actual values if they change
export MESH_SERVER_URL="http://192.168.4.89:8000/interact"
export MESH_ALSA_DEVICE="plughw:3,0"

# Activate the virtual environment
source venv-client/bin/activate

# Run the client
python -m mesh_client.main
