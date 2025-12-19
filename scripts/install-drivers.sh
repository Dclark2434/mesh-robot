#!/bin/bash

# 1. Clone the Freenove Drivers
if [ ! -d "freenove_code" ]; then
    echo "[SYSTEM] Cloning Freenove Drivers..."
    git clone https://github.com/Freenove/Freenove_Big_Hexapod_Robot_Kit_for_Raspberry_Pi.git freenove_code
else
    echo "[SYSTEM] Freenove drivers already present."
fi

# 2. Install System Dependencies (Audio & Build Tools)
echo "[SYSTEM] Installing OS Dependencies..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-dev i2c-tools portaudio19-dev libasound2-dev espeak-ng

# 3. Install Python Libraries (Hardware Control)
echo "[SYSTEM] Installing Python Hardware Libs (Global)..."
# We use --break-system-packages because we NEED these to be available to the root user for GPIO access
sudo pip3 install rpi_ws281x adafruit-pca9685 adafruit-blinka --break-system-packages

# 4. Install Audio/Network Libs (Client Side)
echo "[SYSTEM] Installing Client Audio Libs..."
sudo pip3 install sounddevice numpy scipy requests --break-system-packages

# 5. Enable I2C (Hardware Config)
# This force-enables the I2C bus so the Servo Driver works immediately
if ! grep -q "dtparam=i2c_arm=on" /boot/config.txt; then
    echo "[CONFIG] Enabling I2C..."
    echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
    echo "[NOTICE] I2C Enabled. You MUST reboot for this to take effect!"
else
    echo "[CONFIG] I2C already enabled."
fi

echo "[SUCCESS] Robot Environment Ready."