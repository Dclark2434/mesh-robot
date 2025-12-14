# test_robot.py
import os
import sys

# 1. Ask the Brain
from cloud.brain import ask_tars

# 2. Wake up the Mouth
from voice.tts_engine import speak

def run_test():
    user_query = input("You: ")
    
    # Step 1: Get text from Gemini
    print("\nSending to Gemini...")
    tars_response = ask_tars(user_query)
    print(f"Gemini says: {tars_response}")

    # Step 2: Send text to F5-TTS
    print("\nGenerating Audio...")
    speak(tars_response, "tars_response.wav")

# Step 3: Play it
    print(f"Playing audio... (File saved at: {os.path.abspath('tars_response.wav')})")
    
    # Check if we are in WSL (Windows Subsystem for Linux)
    if "microsoft-standard" in os.uname().release:
        # Convert Linux path to Windows path and play with cmd.exe (no PowerShell profile noise)
        import subprocess
        linux_path = os.path.abspath("tars_response.wav")
        # wslpath converts /mnt/e/... to E:\...
        win_path = subprocess.check_output(["wslpath", "-w", linux_path]).decode().strip()
        # Use cmd.exe /c start "" to play - cleaner than PowerShell
        os.system(f'cmd.exe /c start "" "{win_path}"')
    else:
        # Standard Linux (like on the robot/Pi later)
        os.system("aplay tars_response.wav") 

if __name__ == "__main__":
    # Ensure our venv is loaded or packages are installed
    run_test()