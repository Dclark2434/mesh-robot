
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

        self.title("M.E.S.H. System Launcher v1.1")
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
        
        # 1. Initialize Variables
        self.brain_var = ctk.StringVar(value="Gemini" if os.getenv("USE_GEMINI", "True") == "True" else "Ollama")
        
        engine_env = os.getenv("TTS_ENGINE", "chatterbox")
        start_voice = "Chatterbox"
        if os.getenv("USE_ELEVENLABS", "False") == "True":
             start_voice = "ElevenLabs"
        elif engine_env == "f5":
             start_voice = "F5-TTS"

        self.voice_var = ctk.StringVar(value=start_voice)
        start_model = os.getenv("CHATTERBOX_MODEL", "resemble-ai/chatterbox-100m")
        
        # Mapping for UI
        self.model_map = {
            "100m": "resemble-ai/chatterbox-100m",
            "Turbo": "resemble-ai/chatterbox-turbo"
        }
        # Reverse map for init
        inv_map = {v: k for k, v in self.model_map.items()}
        short_start = inv_map.get(start_model, "100m")
        
        self.chatterbox_model_var = ctk.StringVar(value=short_start)

        # 2. Create Input Widgets (Hidden by default or managed later)
        
        # Brain Inputs
        self.gemini_key_entry = ctk.CTkEntry(self.brain_frame, placeholder_text="Gemini API Key", width=300)
        start_gemini_key = os.getenv("GEMINI_API_KEY", "")
        if start_gemini_key: self.gemini_key_entry.insert(0, start_gemini_key)

        # Voice Section
        self.voice_frame = ctk.CTkFrame(self)
        self.voice_frame.grid(row=2, column=0, padx=20, pady=10, sticky="ew")

        ctk.CTkLabel(self.voice_frame, text="Vocal Synthesis", font=("Arial", 16, "bold")).pack(pady=5)

        # Voice Inputs: Chatterbox
        self.chatterbox_label = ctk.CTkLabel(self.voice_frame, text="Chatterbox Settings", font=("Arial", 14, "bold"))
        self.chatterbox_model_seg = ctk.CTkSegmentedButton(self.voice_frame, variable=self.chatterbox_model_var, 
                                                           values=["100m", "Turbo"])
        
        # Explicit HF Token Label and Entry
        self.hf_token_label = ctk.CTkLabel(self.voice_frame, text="Hugging Face Token (Required for Turbo):")
        self.hf_token_entry = ctk.CTkEntry(self.voice_frame, placeholder_text="hf_xxxxxxxx", width=300)
        start_hf_token = os.getenv("HF_TOKEN", "")
        if start_hf_token: self.hf_token_entry.insert(0, start_hf_token)

        # Voice Inputs: ElevenLabs
        self.eleven_api_entry = ctk.CTkEntry(self.voice_frame, placeholder_text="ElevenLabs API Key", width=300)
        start_eleven_key = os.getenv("ELEVENLABS_API_KEY", "")
        if start_eleven_key: self.eleven_api_entry.insert(0, start_eleven_key)
        
        self.eleven_voice_entry = ctk.CTkEntry(self.voice_frame, placeholder_text="Voice ID", width=300)
        start_eleven_voice = os.getenv("ELEVENLABS_VOICE_ID", "")
        if start_eleven_voice: self.eleven_voice_entry.insert(0, start_eleven_voice)

        # 3. Create Controllers (Now safe to trigger callbacks)
        self.brain_seg = ctk.CTkSegmentedButton(self.brain_frame, values=["Gemini", "Ollama"], variable=self.brain_var, command=self.toggle_brain_inputs)
        self.brain_seg.pack(pady=5)
        
        self.voice_seg = ctk.CTkSegmentedButton(self.voice_frame, values=["ElevenLabs", "Chatterbox", "F5-TTS"], variable=self.voice_var, command=self.toggle_voice_inputs)
        self.voice_seg.pack(pady=5)

        # 4. Initialize Visibility
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
        # Helper to hide all specific frames first
        self.eleven_api_entry.pack_forget()
        self.eleven_voice_entry.pack_forget()
        self.chatterbox_label.pack_forget()
        self.chatterbox_model_seg.pack_forget()
        self.hf_token_label.pack_forget()
        self.hf_token_entry.pack_forget()

        if value == "ElevenLabs":
            self.eleven_api_entry.pack(pady=5)
            self.eleven_voice_entry.pack(pady=5)
        elif value == "Chatterbox":
            self.chatterbox_label.pack(pady=5)
            self.chatterbox_model_seg.pack(pady=5)
            # Layout spacing
            self.hf_token_label.pack(pady=(10, 0))
            self.hf_token_entry.pack(pady=5)

    def save_env(self):
        # 1. Read current UI state
        use_gemini = str(self.brain_var.get() == "Gemini")
        gemini_key = self.gemini_key_entry.get()
        
        voice_sel = self.voice_var.get()
        use_eleven = str(voice_sel == "ElevenLabs")
        
        tts_engine = "chatterbox"
        if voice_sel == "F5-TTS":
             tts_engine = "f5"
        elif voice_sel == "Chatterbox":
             tts_engine = "chatterbox"
        
        eleven_key = self.eleven_api_entry.get()
        eleven_voice = self.eleven_voice_entry.get()
        hf_token = self.hf_token_entry.get()
        
        # Map short label back to full model string
        short_model = self.chatterbox_model_var.get()
        chatterbox_model = self.model_map.get(short_model, "resemble-ai/chatterbox-100m")

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
        env_dict["TTS_ENGINE"] = tts_engine
        # Remove legacy keys if present
        if "USE_F5_TTS" in env_dict: pop_keys = ["USE_F5_TTS"] 
        
        env_dict["ELEVENLABS_API_KEY"] = eleven_key
        env_dict["ELEVENLABS_VOICE_ID"] = eleven_voice
        env_dict["CHATTERBOX_MODEL"] = chatterbox_model
        env_dict["HF_TOKEN"] = hf_token

        # Write back
        with open(self.env_path, "w") as f:
            for k, v in env_dict.items():
                f.write(f"{k}={v}\n")
        
        print("Configuration saved to .env")

    def launch_system(self):
        self.save_env()
        print("Sanity Check: System Initializing...")
        self.should_launch = True
        self.quit() # Stop mainloop
        self.destroy() # Destroy window

if __name__ == "__main__":
    app = MeshLauncher()
    app.mainloop()
    
    # Process Launch Logic (Runs after GUI finishes)
    if getattr(app, 'should_launch', False):
        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")
        print("\n[LAUNCHER] Starting Server...")
        try:
            # We use call/run here because we are now in the main thread (no GUI to freeze)
            subprocess.run([sys.executable, server_path], check=True)
        except KeyboardInterrupt:
            print("\n[LAUNCHER] System Shutdown.")
        except Exception as e:
            print(f"\n[LAUNCHER] Error: {e}")
            input("Press Enter to exit...")
