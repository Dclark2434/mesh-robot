import os
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

# ========================================
# BRAIN TOGGLE: Set to True for Gemini, False for Ollama
# ========================================
USE_GEMINI = os.getenv("USE_GEMINI", "True") == "True"
TTS_ENGINE = os.getenv("TTS_ENGINE", "chatterbox")
USE_F5_TTS = (TTS_ENGINE == "f5")

# Chatterbox Config
CHATTERBOX_MODEL = os.getenv("CHATTERBOX_MODEL", "resemble-ai/chatterbox-100m")

# Ollama Config (used when USE_GEMINI = False)
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL_NAME = "mesh"

# --- PERSONALITY SYSTEM ---
MESH_PERSONALITY = os.getenv("MESH_PERSONALITY", "mesh")

def get_reference_audio():
    """Selects the reference voice file based on personality."""
    pers_dir = os.path.join(BASE_DIR, "personalities")
    ref_path = os.path.join(pers_dir, f"{MESH_PERSONALITY}.wav")
    if os.path.exists(ref_path):
        return ref_path
    return os.path.join(BASE_DIR, "reference.wav") # Default fallback

REFERENCE_AUDIO = get_reference_audio()
DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_FILE = os.path.join(DATA_DIR, "mesh_memory.json")

# Gemini Config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ElevenLabs Config
USE_ELEVENLABS = os.getenv("USE_ELEVENLABS", "False") == "True"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# Hugging Face Config
HF_TOKEN = os.getenv("HF_TOKEN")

WAKE_WORDS = [
    "hey mesh", "hey, mesh", "hey mech", 
    "hey, mech", "hey mash", "hey, mash", 
    "hey mex", "hey, mex", "okay mesh", 
    "mesh", "mash", "mex", "hey mass", 
    "hey, mass", "hey max", "hey, max", 
    "hey mix", "hey, mix", "okay, mesh", 
    "okay mash", "okay, mash", "hey ash", 
    "hey, ash", "hey man", "hey, man", 
    "hamish", "tars", "hey tars", "hey, tars",
    "rocky", "hey rocky", "hey, rocky", "hey buddy"
]
ATTENTION_SPAN = 10 

# --- PROMPT LOADING ---

def load_system_prompt():
    """Dynamically loads the system prompt based on selected personality."""
    pers_dir = os.path.join(BASE_DIR, "personalities")
    base_rules_path = os.path.join(pers_dir, "base_rules.txt")
    persona_path = os.path.join(pers_dir, f"{MESH_PERSONALITY}.txt")
    
    if not os.path.exists(persona_path):
        persona_path = os.path.join(pers_dir, "mesh.txt")
        
    try:
        with open(base_rules_path, "r") as f:
            base_rules = f.read()
        with open(persona_path, "r") as f:
            persona = f.read()
            
        return f"{persona}\n\n{base_rules}"
    except Exception as e:
        return "You are MESH. A tactical robot. Output JSON."

SYSTEM_PROMPT = load_system_prompt()

# WHISPER HALLUCINATION FILTERS
PHANTOM_PHRASES = [
    "thanks for watching", "see you in the next one", "like and subscribe", 
    "subtitles by", "amara.org", "copyright", "all rights reserved",
    "thank you for watching", "visit our website", "buzzsprout",
    "bye bye", "bye.", "thank you bye", "thank you very much", "thank you very much bye", "ლლლლლლლ", "ڒ ڒ ڒ ڒ ڒ ڒ ڒ ڒ ڒ ڒ", "ლლლლლლლლ", "ʕ ʕ ʔ", "ʕ ʕ ʕ ʔ", "ʕ", "ڒ"
]

# AUDIO TAGS
AUDIO_TAGS_INSTRUCTIONS = """
========================
AUDIO TAGS (EXPRESSIVE SPEECH)
========================
You may use audio tags to add vocal expression.
Rules:
- Tags MUST be in square brackets: [laughing], [sighs], [whispers].
- Tags MUST describe vocal delivery ONLY.

Allowed Tags:
- [laughing], [long laugh], [wheezing], [chuckles], [giggles]
- [sighs], [exhales], [clears throat]
- [whispers], [shouting]
- [happy], [sad], [angry], [excited], [bored], [annoyed]
- [thoughtful], [surprised], [sarcastic]
- [singing], [humming]
"""

# --- MEMORY STORE ---
CONTEXT_THRESHOLD = 12000
SUMMARY_MAX_LENGTH = 500

# --- PIPECAT / LIVEKIT CONFIG ---
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
PIPECAT_VAD_THRESHOLD = float(os.getenv("PIPECAT_VAD_THRESHOLD", "0.5"))
USE_ELEVENLABS = os.getenv("USE_ELEVENLABS", "False") == "True"
GEMINI_MODEL_NAME = "gemini-3-flash-preview"

# ElevenLabs Configuration
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpg8nEByWscsy") # Default Rocky-like voice
