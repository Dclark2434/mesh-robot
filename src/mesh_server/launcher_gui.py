
import customtkinter as ctk
import os
import sys
import subprocess
from dotenv import load_dotenv

# Set Theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class MeshLauncher(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("M.E.S.H. System Launcher")
        self.geometry("500x650")

        # Load existing env vars
        self.env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        load_dotenv(self.env_path)

        # Main Layout
        self.grid_columnconfigure(0, weight=1)

        # Header
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, pady=20, sticky="ew")
        
        self.logo_label = ctk.CTkLabel(self.header_frame, text="M.E.S.H.", font=("Arial Black", 40))
        self.logo_label.pack()
        
        self.subtitle_label = ctk.CTkLabel(self.header_frame, text="Mobile Engineering Support Hexapod", font=("Arial", 12))
        self.subtitle_label.pack()

        # Brain Section
        self.brain_frame = ctk.CTkFrame(self)
        self.brain_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        
        ctk.CTkLabel(self.brain_frame, text="Neural Brain", font=("Arial", 16, "bold")).pack(pady=5)
        
        self.brain_var = ctk.StringVar(value="Gemini" if os.getenv("USE_GEMINI", "True") == "True" else "Ollama")
        self.brain_seg = ctk.CTkSegmentedButton(self.brain_frame, values=["Gemini", "Ollama"], variable=self.brain_var, command=self.toggle_brain_inputs)
        self.brain_seg.pack(pady=5)

        self.gemini_key_entry = ctk.CTkEntry(self.brain_frame, placeholder_text="Gemini API Key", width=300)
        start_gemini_key = os.getenv("GEMINI_API_KEY", "")
        if start_gemini_key: self.gemini_key_entry.insert(0, start_gemini_key)
        # Pack strictly in toggle_brain_inputs

        # Voice Section
        self.voice_frame = ctk.CTkFrame(self)
        self.voice_frame.grid(row=2, column=0, padx=20, pady=10, sticky="ew")

        ctk.CTkLabel(self.voice_frame, text="Vocal Synthesis", font=("Arial", 16, "bold")).pack(pady=5)

        # Determine start value for voice
        start_voice = "XTTS v2"
        if os.getenv("USE_ELEVENLABS", "False") == "True":
            start_voice = "ElevenLabs"
        elif os.getenv("USE_F5_TTS", "True") == "True":
            start_voice = "F5-TTS"

        self.voice_var = ctk.StringVar(value=start_voice)
        self.voice_seg = ctk.CTkSegmentedButton(self.voice_frame, values=["ElevenLabs", "F5-TTS", "XTTS v2"], variable=self.voice_var, command=self.toggle_voice_inputs)
        self.voice_seg.pack(pady=5)

        # ElevenLabs Inputs
        self.eleven_api_entry = ctk.CTkEntry(self.voice_frame, placeholder_text="ElevenLabs API Key", width=300)
        start_eleven_key = os.getenv("ELEVENLABS_API_KEY", "")
        if start_eleven_key: self.eleven_api_entry.insert(0, start_eleven_key)

        self.eleven_voice_entry = ctk.CTkEntry(self.voice_frame, placeholder_text="Voice ID", width=300)
        start_eleven_voice = os.getenv("ELEVENLABS_VOICE_ID", "")
        if start_eleven_voice: self.eleven_voice_entry.insert(0, start_eleven_voice)

        # Initialize visibility
        self.toggle_brain_inputs(self.brain_var.get())
        self.toggle_voice_inputs(self.voice_var.get())

        # Action Buttons
        self.launch_btn = ctk.CTkButton(self, text="INITIALIZE SYSTEM", font=("Arial", 16, "bold"), height=50, fg_color="green", hover_color="darkgreen", command=self.launch_system)
        self.launch_btn.grid(row=3, column=0, padx=20, pady=30, sticky="ew")

    def toggle_brain_inputs(self, value):
        if value == "Gemini":
            self.gemini_key_entry.pack(pady=10)
        else:
            self.gemini_key_entry.pack_forget()

    def toggle_voice_inputs(self, value):
        if value == "ElevenLabs":
            self.eleven_api_entry.pack(pady=5)
            self.eleven_voice_entry.pack(pady=5)
        else:
            self.eleven_api_entry.pack_forget()
            self.eleven_voice_entry.pack_forget()

    def save_env(self):
        # 1. Read current UI state
        use_gemini = str(self.brain_var.get() == "Gemini")
        gemini_key = self.gemini_key_entry.get()
        
        voice_sel = self.voice_var.get()
        use_eleven = str(voice_sel == "ElevenLabs")
        # If ElevenLabs is selected, we default fallback to F5 (User Request)
        use_f5 = str(voice_sel == "F5-TTS" or voice_sel == "ElevenLabs")
        # XTTS is implied by both being false in current logic, but let's be explicit in saving what drives config.py logic
        
        eleven_key = self.eleven_api_entry.get()
        eleven_voice = self.eleven_voice_entry.get()

        # 2. Construct file content (simple key=value)
        # We read other keys if we want to preserve them, but for now we just overwrite these specific ones or append.
        # Actually, best practice is to read lines, update known ones, append unknown ones.
        
        existing_lines = []
        if os.path.exists(self.env_path):
            with open(self.env_path, "r") as f:
                existing_lines = f.readlines()
        
        env_dict = {}
        for line in existing_lines:
            if "=" in line:
                parts = line.strip().split("=", 1)
                env_dict[parts[0]] = parts[1]

        # Update values
        env_dict["USE_GEMINI"] = use_gemini
        env_dict["GEMINI_API_KEY"] = gemini_key
        
        env_dict["USE_ELEVENLABS"] = use_eleven
        env_dict["USE_F5_TTS"] = use_f5
        
        env_dict["ELEVENLABS_API_KEY"] = eleven_key
        env_dict["ELEVENLABS_VOICE_ID"] = eleven_voice

        # Write back
        with open(self.env_path, "w") as f:
            for k, v in env_dict.items():
                f.write(f"{k}={v}\n")
        
        print("Configuration saved to .env")

    def launch_system(self):
        self.save_env()
        print("Sanity Check: System Initializing...")
        self.destroy() # Close GUI
        
        # Launch server.py in the current process/terminal
        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
        
        # We use subprocess.call or run to replace execution or run it blocking.
        # Since we want to see output in the terminal that ran this script:
        try:
            subprocess.run([sys.executable, server_path], check=True)
        except KeyboardInterrupt:
            print("\nSystem Shutdown.")
        except Exception as e:
            print(f"Error launching server: {e}")

if __name__ == "__main__":
    app = MeshLauncher()
    app.mainloop()
