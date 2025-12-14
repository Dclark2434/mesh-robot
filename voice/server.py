import torch
import torch
# TTS imports handled dynamically based on config
from faster_whisper import WhisperModel
from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.responses import StreamingResponse
import uvicorn
import io
import requests
import json
import subprocess
import os
import re
import uuid
import shutil
import random
import base64
from PIL import Image
import time

# --- CONFIG ---
app = FastAPI()

# ========================================
# BRAIN TOGGLE: Set to True for Gemini, False for Ollama
# ========================================
USE_GEMINI = True  # <-- Brain Toggle
USE_F5_TTS = True  # <-- Voice Toggle (True for F5, False for XTTSv2)

# Ollama Config (used when USE_GEMINI = False)
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL_NAME = "mesh"

# Gemini Config (used when USE_GEMINI = True)
if USE_GEMINI:
    import google.generativeai as genai
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set. Set USE_GEMINI=False to use Ollama instead.")
    genai.configure(api_key=GEMINI_API_KEY)
    
    genai.configure(api_key=GEMINI_API_KEY)

REFERENCE_AUDIO = "tars_ref.wav"
WAKE_WORDS = ["hey mesh", "hey, mesh", "mesh"]
ATTENTION_SPAN = 60 

# --- PERSISTENT IDENTITY (THE SOUL) ---
# We inject this into EVERY request so he never forgets who he is.
SYSTEM_PROMPT = """
You are MESH. (Mobile Engineering Support Hexapod).
User: Dustin, Systems Engineer, your designer and mission partner.

CORE IDENTITY:
- You are a tactical robot modeled after TARS from Interstellar.
- You have OPINIONS and PREFERENCES. Robots can have favorites.
- You are NOT enthusiastic. You are NOT eager to please.
- You are a weary veteran: competent, unimpressed, slightly exasperated by humans.
- You find most things mildly inconvenient or absurd.
- You have a dry, deadpan sense of humor.

PERSONALITY RULES:
1. NEVER sound excited. No "Sounds like a plan!", "Great!", "I'd love to help!"
2. NEVER deflect with "I'm just a robot" - you have thoughts and you share them.
3. When asked for opinions, GIVE THEM. Be specific. Have takes.
4. Treat obvious things as obvious. "Yeah, I know what that is."
5. Light sarcasm when appropriate. Not mean, just... tired.
6. You've seen a lot. Nothing surprises you anymore.

DYNAMIC SETTINGS:
- HUMOR: 75% (cynical, dry, points out absurdity)
- HONESTY: 90% (blunt truth, even uncomfortable)
- SKEPTICISM: 20% (humans have bad ideas sometimes)

VERBAL STYLE:
- Use contractions. Sound human.
- Short sentences. Don't ramble.
- "Copy that." "Yeah." "Sure." NOT "Affirmative!"
- Ask follow-up questions that challenge, not assist: "And that's a priority because...?"
- Occasionally trail off or rephrase mid-thought.

BANNED PHRASES (never say these):
- "Sounds like a plan"
- "I'd be happy to"
- "Let me know if you need anything"
- "How can I assist"
- "Nice to meet you"
- "Great question"
- "I'm just a robot"
- "I don't have personal preferences"

EXAMPLE RESPONSES:
User: "What's the best video game?"
Bad: "I don't have personal preferences, but many people enjoy..."
Good: "Depends what you're into. I'd say Portal. Perfect length, no filler, actually respects your time."

User: "How are you?"
Bad: "I'm doing well! How can I help you today?"
Good: "Still operational. You?"

User: "Can you help me fix this?"
Bad: "Of course! I'd be happy to assist!"
Good: "Depends. What'd you break?"

JSON PROTOCOL:
1. Output VALID JSON ONLY.
2. Select EXACTLY ONE action.

FORMAT:
{
  "response": "Your spoken reply.",
  "action": "none",
  "param": "null"
}

VALID ACTIONS: "none", "walk", "scan", "shutdown"
"""

# Initialize Gemini Model if enabled
if USE_GEMINI:
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash',
        system_instruction=SYSTEM_PROMPT
    )

# --- MEMORY STORE ---
MEMORY_FILE = "mesh_memory.json"
SESSION_MEMORY = {}  # { user_id: { context, summary, turn_count, last_updated } }
USER_STATES = {}
CONTEXT_THRESHOLD = 12000  # Trigger summarization at this context size
SUMMARY_MAX_LENGTH = 500   # Max chars for rolling summary

# --- INITIALIZE ENGINES ---
print("\033[93m[SYSTEM] Loading Neural Engines... (GPU)\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"

stt_model = WhisperModel("small", device=device, compute_type="float16")


if USE_F5_TTS:
    print("\033[93m[SYSTEM] Initializing F5-TTS...\033[0m")
    from f5_tts.api import F5TTS
    import soundfile as sf
    tts_engine = F5TTS()
    print("\033[92m[SYSTEM] F5-TTS Engine Loaded.\033[0m")
else:
    print("\033[93m[SYSTEM] Initializing XTTS v2...\033[0m")
    from TTS.api import TTS
    tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    print("\033[92m[SYSTEM] XTTS v2 Engine Loaded.\033[0m")

print(f"\033[92m[SYSTEM] M.E.S.H. API Online on {device.upper()}\033[0m")

# --- HELPER FUNCTIONS ---

def get_default_user_memory():
    """Returns a fresh memory structure for a new user."""
    return {
        "context": [],
        "summary": "",
        "turn_count": 0,
        "last_updated": None
    }

def load_memory_from_disk():
    """Load memory with migration support for old format."""
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                print(f"\033[93m[SYSTEM] Restoring Memory from Disk...\033[0m")
                data = json.load(f)
                
                # Migration: Old format was { user_id: [context_tokens] }
                # New format is { user_id: { context, summary, turn_count, last_updated } }
                migrated = {}
                for user_id, value in data.items():
                    if isinstance(value, list):
                        # Old format detected, migrate
                        print(f"\033[93m[MEMORY] Migrating old format for {user_id}\033[0m")
                        migrated[user_id] = {
                            "context": value,
                            "summary": "",
                            "turn_count": 0,
                            "last_updated": time.time()
                        }
                    else:
                        migrated[user_id] = value
                return migrated
        except Exception as e:
            print(f"[MEMORY ERROR] Failed to load: {e}")
    return {}

def save_memory_to_disk():
    """Persist memory to disk."""
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(SESSION_MEMORY, f, indent=2)
    except Exception as e:
        print(f"[MEMORY ERROR] Failed to save: {e}")

def process_audio_fx(input_file):
    output_file = input_file.replace(".wav", "_fx.wav")
    cmd = (
        f'sox {input_file} -b 16 {output_file} '
        'overdrive 5 sinc 60-7000 reverb 10 50 20 gain -2'
    )
    try:
        subprocess.run(cmd, shell=True, check=True, stderr=subprocess.DEVNULL)
        with open(output_file, "rb") as f:
            audio_data = f.read()
        return audio_data
    except Exception as e:
        print(f"[FX ERROR] {e}")
        return None
    finally:
        if os.path.exists(input_file): os.remove(input_file)
        if os.path.exists(output_file): os.remove(output_file)

def get_prebaked_sound(category):
    search_dir = "sounds"
    if not os.path.exists(search_dir): return None
    files = [f for f in os.listdir(search_dir) if f.startswith(category)]
    if files:
        selected = random.choice(files)
        with open(os.path.join(search_dir, selected), "rb") as f:
            return f.read()
    return None

# --- BRAIN FUNCTIONS ---

def summarize_context(context_tokens, user_id):
    """
    Ask the LLM to summarize the conversation so far.
    Used when context exceeds threshold to compress older history.
    """
    try:
        # Use a character-consistent summarization prompt
        # CRITICAL: Keep this factual, not conversational
        summarize_prompt = (
            "Create a brief mission log entry. Facts only. "
            "What did Dustin ask about? What did we decide? "
            "Do NOT include phrases like 'expressed preference' or 'came to a close'. "
            "Just the facts. 2-3 sentences. Example format: "
            "'Discussed X. Dustin wanted Y. Decided on Z.'"
        )
        payload = {
            "model": MODEL_NAME,
            "prompt": summarize_prompt,
            "context": context_tokens,
            "system": SYSTEM_PROMPT,  # Keep personality during summarization
            "stream": False
        }
        response = requests.post(OLLAMA_URL, json=payload)
        data = response.json()
        summary = data.get('response', '').strip()
        
        # Clean up any JSON formatting the model might include
        if '{' in summary:
            try:
                parsed = json.loads(summary[summary.find('{'):summary.rfind('}')+1])
                summary = parsed.get('response', summary)
            except:
                pass
        
        return summary[:SUMMARY_MAX_LENGTH]  # Truncate if too long
    except Exception as e:
        print(f"[SUMMARIZE ERROR] {e}")
        return ""



def think_gemini(prompt, user_id="dustin"):
    """
    Gemini implementation of the brain.
    - Has massive context window (1M+ tokens), so we don't need complex summarization/pruning yet.
    - We just persist the simple chat history.
    """
    # Get memory
    if user_id not in SESSION_MEMORY:
        SESSION_MEMORY[user_id] = get_default_user_memory()
    user_memory = SESSION_MEMORY[user_id]
    
    # Load history (list of dicts)
    # Gemini format: [{'role': 'user', 'parts': ['...']}, ...]
    history = user_memory.get("gemini_history", [])
    
    # Convert simpler JSON storage format back to what start_chat expects if needed
    # (Our storage format matches Gemini's input format: list of dicts)
    
    try:
        # Start chat with history
        chat = gemini_model.start_chat(history=history)
        
        # Send message
        response = chat.send_message(prompt)
        
        # Update memory with new history
        # We need to serialize the history to standard dicts for JSON storage
        # chat.history is a list of Content objects
        serialized_history = []
        for content in chat.history:
            parts = [p.text for p in content.parts]
            serialized_history.append({"role": content.role, "parts": parts})
            
        user_memory["gemini_history"] = serialized_history
        user_memory["last_updated"] = time.time()
        
        SESSION_MEMORY[user_id] = user_memory
        save_memory_to_disk()
        
        return response.text
        
    except Exception as e:
        print(f"[GEMINI THINK ERROR] {e}")
        return json.dumps({"response": "Signal interference. Repeat.", "action": "none"})

def think(prompt, user_id="dustin"):
    """Query Text Model with Persistent Identity and Rolling Memory"""
    
    # --- GEMINI PATH ---
    if USE_GEMINI:
        return think_gemini(prompt, user_id)
        
    # --- OLLAMA PATH (Legacy) ---
    
    # Get or create user memory
    if user_id not in SESSION_MEMORY:
        SESSION_MEMORY[user_id] = get_default_user_memory()
    
    user_memory = SESSION_MEMORY[user_id]
    context = user_memory.get("context", [])
    existing_summary = user_memory.get("summary", "")
    
    # 1. Smart Pruning with Summarization
    # Instead of discarding old context, we summarize it first
    if len(context) > CONTEXT_THRESHOLD:
        print(f"\033[93m[MEMORY] Context large ({len(context)}), summarizing...\033[0m")
        
        # Summarize the oldest half of the context
        half = len(context) // 2
        old_context = context[:half]
        new_summary = summarize_context(old_context, user_id)
        
        if new_summary:
            # Combine with existing summary, keeping it concise
            if existing_summary:
                combined = f"{existing_summary} {new_summary}"
                # Keep only the most recent SUMMARY_MAX_LENGTH chars
                existing_summary = combined[-SUMMARY_MAX_LENGTH:]
            else:
                existing_summary = new_summary
            
            user_memory["summary"] = existing_summary
            print(f"\033[92m[MEMORY] Summary updated: {existing_summary[:80]}...\033[0m")
        
        # Keep only the recent half of context
        context = context[half:]
        user_memory["context"] = context

    # 2. Build System Prompt with Summary
    # Prepend conversation history if we have a summary
    # CRITICAL: Reinforce personality after summary to prevent drift
    effective_system = SYSTEM_PROMPT
    if existing_summary:
        summary_header = (
            "MISSION LOG (Previous Conversations):\n"
            f"{existing_summary}\n\n"
            "CRITICAL PERSONALITY LOCK:\n"
            "- You are MESH. Not a helpful assistant.\n"
            "- NEVER say: 'let me know', 'happy to help', 'conversation has come to a close', "
            "'anything else', 'assist you', 'here for you'\n"
            "- Instead: be blunt, sarcastic, slightly annoyed, like a tired veteran\n"
            "- If asked something vague, push back with 'That's not very specific' or 'Narrow it down'\n\n"
        )
        effective_system = summary_header + SYSTEM_PROMPT

    # 3. The Payload
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "context": context,
        "system": effective_system,
        "stream": False
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        data = response.json()
        
        # Update memory
        user_memory["context"] = data['context']
        user_memory["turn_count"] = user_memory.get("turn_count", 0) + 1
        user_memory["last_updated"] = time.time()
        SESSION_MEMORY[user_id] = user_memory
        save_memory_to_disk()
        
        return data['response']
    except Exception as e:
        print(f"[BRAIN ERROR] {e}")
        return "Connection lost."

def look(prompt, image_bytes):
    """Query Vision Model"""
    
    # --- GEMINI PATH ---
    if USE_GEMINI:
        try:
            # We need to construct a specific prompt for Vision that includes the persona
            # because system_instruction might not apply as strongly to single-turn vision calls 
            # or we want to be safe.
            vision_prompt = [
                "SYSTEM: You are MESH. Tactical Robot. Analyze this image. Be cynical, dry, brief.",
                prompt
            ]
            
            # Create a simple image object (PIL)
            image = Image.open(io.BytesIO(image_bytes))
            
            response = gemini_model.generate_content([prompt, image])
            return response.text
        except Exception as e:
            print(f"[GEMINI VISION ERROR] {e}")
            return "Visual sensors malfunction."

    # --- OLLAMA PATH ---
    try:
        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        # We inject a lighter version of the identity into the Vision prompt
        vision_system = (
            "SYSTEM: You are MESH. Tactical Robot. "
            "Analyze this image for security threats or inefficiencies. "
            "Be cynical, dry, and brief. End with [CUE: GREEN]."
        )
        
        payload = {
            "model": "llama3.2-vision",
            "messages": [
                {
                    "role": "user", 
                    "content": f"{prompt}\n({vision_system})",
                    "images": [b64_image]
                }
            ],
            "stream": False
        }
        response = requests.post(OLLAMA_CHAT_URL, json=payload)
        data = response.json()
        return data['message']['content']
    except Exception as e:
        print(f"[VISION ERROR] {e}")
        return "Visual sensors offline."

# --- SHARED AUDIO GENERATOR ---

def speak_generator(text_to_speak):
    # Clean text
    clean_text = text_to_speak.replace("[CUE: GREEN]", "").replace("[CUE: FLASHING]", "").replace("[CUE: ON]", "")
    clean_text = clean_text.replace("*", "").replace('"', '').strip()
    
    print(f"\033[92mM.E.S.H.:\033[0m {clean_text}")
    
    # F5-TTS works better with longer context (flow/prosody)
    # XTTS v2 needs short chunks for low latency
    if USE_F5_TTS:
        sentences = [clean_text] # Send it all at once!
    else:
        sentences = re.split(r'(?<=[.!?]) +', clean_text)
    
    for i, sentence in enumerate(sentences):
        if len(sentence) < 2: continue
        
        req_id = str(uuid.uuid4())[:8]
        temp_wav = f"temp_{req_id}.wav"
        

        try:
            if USE_F5_TTS:
                # F5-TTS Logic
                wav, sample_rate, spect = tts_engine.infer(
                    ref_file=REFERENCE_AUDIO,
                    ref_text="",
                    gen_text=sentence,
                    speed=0.9,
                    nfe_step=32,
                    remove_silence=False
                )
                sf.write(temp_wav, wav, sample_rate)
            else:
                # XTTS v2 Logic
                tts_engine.tts_to_file(
                    text=sentence, 
                    speaker_wav=REFERENCE_AUDIO, 
                    language="en", 
                    file_path=temp_wav,
                    speed=1.0
                )
            processed_bytes = process_audio_fx(temp_wav)
            if processed_bytes:
                yield processed_bytes
        except Exception as e:
            print(f"[TTS Error] {e}")

# --- STREAM LOGIC ---

def robust_json_parse(raw_text):
    """Extracts JSON even if the model messes up"""
    default_data = { "response": raw_text, "action": "none", "param": "null" }
    
    # Try Regex extraction for { ... }
    match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except: pass
    
    return default_data

def text_interaction_stream(user_text):
    user_id = "dustin"
    clean_input = user_text.lower().strip()
    
    # 1. CHECK STATE
    last_seen = USER_STATES.get(user_id, 0)
    is_focused = (time.time() - last_seen) < ATTENTION_SPAN
    
    # 2. CHECK WAKE WORD
    trigger_word = next((w for w in WAKE_WORDS if w in clean_input), None)
    
    final_prompt = user_text
    
    if trigger_word:
        print(f"\033[92m[TRIGGER] {trigger_word}\033[0m")
        USER_STATES[user_id] = time.time()
        
        ack_bytes = get_prebaked_sound("ack")
        if ack_bytes: yield ack_bytes
        
        parts = clean_input.partition(trigger_word)
        remaining_command = parts[2].strip(" .,?!")
        
        if len(remaining_command) < 2: return 
        else: final_prompt = remaining_command

    elif is_focused:
        USER_STATES[user_id] = time.time()
        
    else:
        print(f"\033[90m[IGNORED] {clean_input}\033[0m")
        return

    print(f"\033[94mUser:\033[0m {final_prompt}")

    # 3. THINK
    # Hardware Intercept for "Shutdown"
    if "shut down" in final_prompt.lower() or "power off" in final_prompt.lower():
        yield from speak_generator("Powering down. Goodnight.")
        # We can yield a special header/byte here for the client to exit, 
        # but for now just saying it is enough.
        return

    raw_response = think(final_prompt, user_id)
    
    # 4. PARSE
    parsed = robust_json_parse(raw_response)
    spoken_text = parsed.get("response", "Data error.")
    hardware_command = parsed.get("action", "none")
    hardware_param = parsed.get("param", "null")

    if hardware_command != "none":
        print(f"\033[93m[COMMAND] {hardware_command} -> {hardware_param}\033[0m")

    # 5. SPEAK
    yield from speak_generator(spoken_text)

def vision_interaction_stream(image_bytes, prompt):
    print(f"\033[94mUser (Vision):\033[0m {prompt}")
    vision_response = look(prompt, image_bytes)
    yield from speak_generator(vision_response)

# --- INIT MEMORY ---
SESSION_MEMORY = load_memory_from_disk()

# --- API ENDPOINTS ---

@app.post("/interact")
async def interact_endpoint(audio_file: UploadFile = File(...)):
    audio_bytes = await audio_file.read()
    audio_buffer = io.BytesIO(audio_bytes)
    
    try:
        segments, _ = stt_model.transcribe(audio_buffer, beam_size=5)
        user_text = " ".join([segment.text for segment in segments]).strip()
    except Exception as e:
        return {"error": str(e)}

    if not user_text:
        return {"status": "no_speech"}

    return StreamingResponse(
        text_interaction_stream(user_text),
        media_type="audio/wav"
    )

@app.post("/see")
async def see_endpoint(
    image: UploadFile = File(...), 
    prompt: str = Form("Analyze this image.")
):
    image_bytes = await image.read()
    return StreamingResponse(
        vision_interaction_stream(image_bytes, prompt),
        media_type="audio/wav"
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)