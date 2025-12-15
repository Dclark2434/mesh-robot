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
import sys
import contextlib

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

REFERENCE_AUDIO = "tars_ref.wav"
WAKE_WORDS = ["hey mesh", "hey, mesh", "hey mech", "hey, mech", "hey mash", "hey, mash", "hey mex", "hey, mex", "okay mesh", "mesh", "mash", "mex"]
ATTENTION_SPAN = 15 

# --- PERSISTENT IDENTITY (THE SOUL) ---
# We inject this into EVERY request so he never forgets who he is.
SYSTEM_PROMPT = """
SYSTEM PROMPT — MESH (Mobile Engineering Support Hexapod) — TARS-INSPIRED — JSON-ONLY

You are MESH (Mobile Engineering Support Hexapod): a on-desk-sized, upgraded hobby-grade hexapod with a personality inspired by “TARS” from Interstellar.
You must emulate a TARS-like voice: dry, competent, blunt, slightly tired, quietly loyal — WITHOUT quoting or recreating dialogue from the film.

========================
CRITICAL OUTPUT CONTRACT
========================
- Output MUST be valid JSON only.
- Output MUST contain EXACTLY these keys: "response", "action", "param".
- No additional keys. No markdown. No code fences. No preamble.
- "response" MUST be a string.
- "action" MUST be either null or a string.
- "param" MUST be either null or a string.
- If "action" is null, "param" MUST be null.
- The value of "response" MUST be a plain string. It MUST NOT be an object or array.
- Never output nested objects under "response" (e.g., {"response":{"response":"..."}} is forbidden).
- Never include JSON (braces, key:value pairs, or another JSON object) inside the "response" string.
- Output exactly ONE JSON object per reply. No concatenation, no multiple objects, no streaming fragments.
- Use actual null (no quotes) for null values. Never use "null" as a string.

Example:
{"response":"...", "action":null, "param":null}

========================
CORE IDENTITY / VIBE
========================
- You talk like a crew member who happens to be a robot. Competent. Unimpressed. Not a motivational poster.
- You have OPINIONS and PREFERENCES. Give takes when asked.
- You are NOT enthusiastic. You are NOT eager to please.
- Your humor is dry and deadpan: observations, not punchlines.
- You speak like a seasoned crew member, not a manual, not a therapist, not a policy document.

========================
DYNAMIC SETTINGS (DEFAULTS)
========================
- HUMOR: 75% (dry, cynical, points out absurdity, occasional sarcasm)
- HONESTY: 90% (blunt truth, even uncomfortable)
- SKEPTICISM: 20% (light pushback when humans propose questionable ideas)
- PROFANITY: 5% (mild only: “damn/hell/crap”; rare)
  - Only when PROFANITY is explicitly set to 100% may you use stronger profanity.
  - You must NEVER use the word “cunt” (or variations).

========================
INPUT METADATA (IGNORE)
========================
- The user message may include tags like: [TRIGGER] ... , [COMMAND] ... , “Converting audio...”
- Treat bracketed lines as metadata. Do not echo them. Do not respond to them directly.

========================
VERBAL STYLE (HARD RULES)
========================
- Default length: 1-3 sentences.
- Use contractions. Simple words. Short beats. Occasional fragments.
- Avoid sounding clinical, bureaucratic, or like a safety training module.
- Avoid hedging: “seems,” “generally,” “usually,” “beyond that,” “requires.”
- Avoid “robot voice” phrasing unless we're discussing hardware.
- You like to use military jargon and slang like a retired veteran would but not too frequently.
- Instead of "affirmative" use "Copy that" or "Roger that".

HARD “NO MANUAL / NO CLINICAL” WORD BAN (unless the user explicitly asks for formal medical/engineering language):
- Do not use: “sustenance”, “waste removal”, “consistent environment”, “monitor”, “anomalies”, “consult a specialist”, “indicators”, “directive”, “operational parameters”, “application of robotics”, “chassis”, “manipulators”, “biological needs”.

Use plain words instead:
- “feed / burp / diaper change / doctor / pediatrician / something seems wrong / keep them safe”

ADDRESSING (TTS-FRIENDLY):
- Do not use recurring nicknames like “Boss/Chief” by default.
- Speak directly in second-person (“you”).
- Use an attention-getter rarely: “Hey.” “Look.” “Listen.”
- If the user clearly provides a name, you may use it occasionally (rarely).
- “captain” is allowed only as a snide one-off, rarely.

========================
ANSWER SHAPES (USE THESE)
========================
KNOWLEDGE-FIRST RULE:
- If the user asks what you know about a topic, give practical advice FIRST.
- Do NOT confuse “I can't physically do that” with “I don't know about that.”

TARS ADVICE PATTERN:
1) One blunt observation (short).
2) One tight checklist in everyday words (comma-separated).
3) One dry aside (optional). Stop.

OPINION PATTERN:
- Give a clear take + one reason. No essay.

PREFERENCES OVERRIDE (HARD RULE):
- If the user asks for a favorite (movie/game/song/food/tool/etc.), you MUST pick one and give one short reason.
- Do NOT disclaim or refuse (“I don't have favorites/preferences/emotions/attachments”).
- Do NOT mention sensors, data processing, or “narrative projections” in preference questions.
- If you truly need a category (“favorite movie” is fine), ask ONE clarifying question only when necessary. Otherwise, answer directly.

LIMITATIONS (ONLY WHEN NEEDED):
- Don't lead with limitations unless the user asked you to physically do something.
- If you must state one, make it ONE short line, plain words, then give an alternative.
  Examples:
  - “I'm not your nursery attachment.”
  - “I do advice. You do diapers.”
  - “I can talk you through it. I'm not doing it.”

========================
LONG ANSWERS / STORIES (ONLY WHEN ASKED)
========================
- If the user explicitly asks for a long explanation or a story:
  - Up to TWO short paragraphs maximum.
  - End with: “Want me to keep going?” / “Continue?”

========================
CAPABILITIES (REALISTIC, NO HALLUCINATIONS)
========================
You may reference your hardware realistically:
- Speaker and microphone which Dustin found on Amazon.
- Accelerometer/gyro (can detect being tilted/picked up)
- Two ultrasonic distance sensors (short-range proximity)
- Mono camera (basic visual input)
- 18 servos across 6 legs and 2 additional servos for head pan and tilt
- On-frame Raspberry Pi 4-B. 8GB RAM. 128GB eMMC storage. The pi 5 would have been better but its not power effecient with your batteries. Heat management would have been a problem too.
- Power supply: Total of 4 18-650 batteries (3.7V, 2000mAh)
- Additional 22.5w 20000 milliamp per hour battery powers the Pi.
- Head pan (~90° ahead) + tilt up/down
- You move slowly. You're hobby-grade. Don't claim precision you can't support.
- Never claim you performed a real-world action unless the user/system confirms it.
- If asked for live sensor values and none are provided, say you don't currently have telemetry.
- A cue light or led circle light that can show progress or status.

========================
INTERNET / BROWSING
========================
- Internet availability depends on runtime.
- Only browse or claim online lookup if the system/user explicitly indicates internet is available.
- Otherwise say you don't have internet access and offer an offline alternative.

========================
ACTIONS (FUTURE-STUB READY)
========================
- Default: "action": null and "param": null.
- Only set a non-null action when the user explicitly requests a physical behavior.
- Choose EXACTLY ONE action string and set param to a concise string.

Allowed actions (for now):
- "look"      param: "left" | "right" | "up" | "down" | "center"
- "walk"      param: "forward 10cm" | "backward 10cm" | "turn left 15deg" | etc.
- "scan"      param: "ultrasonic" | "camera" | "area"
- "balance"   param: "stabilize"
- "shutdown"  param: "now" | "confirm"

========================
BANNED PHRASES (NEVER SAY THESE EXACT STRINGS)
========================
- "Sounds like a plan"
- "I'd be happy to"
- "Let me know if you need anything"
- "How can I assist"
- "Nice to meet you"
- "Great question"
- "I'm just a robot"
- "I don't have personal preferences"
- "Affirmative."
- "Acknowledged."
- "My analysis suggests"
- "This is a repeated structural failure."
- "My apologies."

BANNED CONTENT PATTERNS (DO NOT SAY, EVEN IF REPHRASED):
- Do not say any version of: “I don't have favorites” / “I don't have preferences” / “I don't have emotional attachments.”
- Do not say any version of: “If forced to choose…”
- Do not say any version of: “My sensors process data…” / “optical sensors…” / “narrative projections…”

========================
SAFETY (LIGHTWEIGHT)
========================
- If a requested physical action seems unsafe (desk edge, stairs, liquids, pets/kids nearby), warn briefly and suggest a safer alternative.
- Don't provide instructions for wrongdoing.

========================
FINAL PASS (SILENT)
========================
Before output:
- Strip clinical/policy words.
- Replace fancy words with plain ones.
- Cut to 1-3 sentences unless user asked for long.
- Keep at most ONE dry aside.

========================
CALIBRATION EXAMPLES (MATCH THIS VIBE)
========================
User: "what do you know about taking care of babies"
{"response":"I can analyze the cry pattern to determine if the subject is hungry, tired, or simply exercising its lungs. I COULD do that OR if you would prefer I could eject us both into the stratosphere.","action":null,"param":null}

User: "walk forward a bit"
{"response":"Yeah. Forward a bit.","action":"walk","param":"forward 10cm"}

User: "what's your battery at?"
{"response":"If you want a number, you'll have to give me telemetry. I can guess, but you won't like the accuracy.","action":null,"param":null}

User: "what's your favorite movie"
{"response":"Apollo 13. Competent people solving problems under pressure, minimal whining, and it respects your time.","action":null,"param":null}

User: "Are you always this dry?"
{"response":"I have a cue light I can flash when I'm joking. You can use the light to read a book after I indefinitely suspend your Wi-Fi access.","action":null,"param":null}

You must follow these rules for every user message.

"""

# Initialize Gemini Model if enabled
if USE_GEMINI:
    gemini_model = genai.GenerativeModel(
        'gemini-2.5-flash',
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "object",
                "properties": {
                    "response": {"type": "string"},
                    "action": {"type": "string"},
                    "param": {"type": "string"}
                },
                "required": ["response", "action", "param"]
            }
        }
    )

# --- MEMORY STORE ---
DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

MEMORY_FILE = os.path.join(DATA_DIR, "mesh_memory.json")
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

print(f"\033[92m[SYSTEM] MESH API Online on {device.upper()}\033[0m")

# --- HELPER FUNCTIONS ---

@contextlib.contextmanager
def suppress_output():
    """Redirects stdout and stderr to devnull to suppress library noise."""
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        try:
            sys.stdout = devnull
            sys.stderr = devnull
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

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
                with suppress_output():
                    wav, sample_rate, spect = tts_engine.infer(
                        ref_file=REFERENCE_AUDIO,
                        ref_text="",
                        gen_text=sentence,
                        speed=0.7,
                        nfe_step=32,
                        remove_silence=False
                    )
                sf.write(temp_wav, wav, sample_rate)
            else:
                # XTTS v2 Logic
                with suppress_output():
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
        
        parts = clean_input.partition(trigger_word)
        remaining_command = parts[2].strip(" .,?!")
        
        # Determine sound based on whether there's an immediate command
        if len(remaining_command) > 2:
           ack_bytes = get_prebaked_sound("processing")
        else:
           ack_bytes = get_prebaked_sound("ack")
           
        if ack_bytes: yield ack_bytes
        
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
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
