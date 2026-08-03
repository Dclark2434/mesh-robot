#!/bin/bash
# setup-autostart.sh - Configures M.E.S.H. as a systemd service on Raspberry Pi

# Navigate to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/.."
PROJECT_ROOT=$(pwd)
SERVICE_NAME="mesh-client.service"
SERVICE_FILE="scripts/$SERVICE_NAME"
DEST_FILE="/etc/systemd/system/$SERVICE_NAME"

echo "⚙️ Setting up M.E.S.H. autostart..."

# --- CLEANUP PREVIOUS VERSION ---
if [ -f "$DEST_FILE" ]; then
    echo "♻️ Found existing service. Cleaning up..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null
    sudo rm "$DEST_FILE"
    sudo systemctl daemon-reload
fi

# Update the service file with the current user and path
USER_NAME=$(whoami)
sed -i "s|User=.*|User=$USER_NAME|g" "$SERVICE_FILE"
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$PROJECT_ROOT|g" "$SERVICE_FILE"
sed -i "s|ExecStart=.*|ExecStart=/bin/bash $PROJECT_ROOT/scripts/startup-robot.sh|g" "$SERVICE_FILE"

# Copy to systemd directory
echo "📦 Installing service to $DEST_FILE..."
sudo cp "$SERVICE_FILE" "$DEST_FILE"

# Reload and enable
echo "🚀 Enabling and starting $SERVICE_NAME..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "✅ Autostart configured! Use 'sudo systemctl status $SERVICE_NAME' to check status."
