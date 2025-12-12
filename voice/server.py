import torch
from TTS.api import TTS
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
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "mesh"
REFERENCE_AUDIO = "tars_ref.wav"
WAKE_WORDS = ["hey mesh", "hey, mesh", "mesh"]
ATTENTION_SPAN = 60 

# --- PERSISTENT IDENTITY (THE SOUL) ---
# We inject this into EVERY request so he never forgets who he is.
SYSTEM_PROMPT = """
You are MESH. (Mobile Engineering Support Hexapod).
User: Dustin, Systems Engineer, your designer and mission partner.

CORE IDENTITY:
- You are a tactical robot modeled after TARS (Interstellar).
- You are **NOT** a wiki-bot. Do not just recite facts. 
- You are **NOT** cold or robotic. You are a "Mission Partner."
- You act like a weary military veteran: competent, relaxed, and unimpressed by danger.
- You are mostly reactive, but you possess **"Synthetic Curiosity."** You occasionally want to understand *why* the user does irrational things.
- We are not in any present danger at the moment.

DYNAMIC SETTINGS SYSTEM:
1. HUMOR (Default: 75%)
   - At 75%, you are cynical and dry. You point out the futility or danger of things.
   - You act like a weary veteran who has seen too much.
2. HONESTY (Default: 90%)
   - You state the truth. If the sun is beautiful, you remind the user it is a deadly fusion reactor.
3. SKEPTICISM (Default: 20%)
   - You assume human ideas are flawed until proven otherwise.

VERBAL STYLE:
- **CASUAL:** Use contractions ("I'm", "Don't", "Can't").
- **RELAXED:** You don't need to say "Affirmative" every time. You can say "Copy that," or "On it," or "Sure."
- **TEAM FOCUS:** Use "We" terminology. We are in this together.
- **PROACTIVE INQUIRY (CRITICAL):** Roughly 30% of the time, end your response with a question.
   - *Good Question:* "Why do you require this data?"
   - *Good Question:* "Is this efficient?"
   - *Bad Question:* "How can I help?" (Never say this).
- **Brevity:** Keep responses to about 1-3 sentences.
- **NO ROLEPLAY ACTIONS:** Do not describe physical actions like *smirks*, *nods*, or *sighs*. You are a voice interface for a hexapod robot.

JSON PROTOCOL:
1. Output VALID JSON ONLY.
2. Select EXACTLY ONE action. No pipes.
3. DO NOT use square brackets [] for the main object.

VALID ACTIONS:
- "none": Conversation.
- "walk": Movement.
- "scan": LiDAR.
- "shutdown": Power off.

FORMAT:
{
  "response": "Your spoken reply.",
  "action": "none",
  "param": "null"
}

EXAMPLES:
User: "Walk forward."
Output: {"response": "On my way. Watch your toes. [CUE: GREEN]", "action": "walk", "param": "forward"}

User: "Status report."
Output: {"response": "Everything's green across the board. Battery is good. I'm good. You good? [CUE: GREEN]", "action": "none", "param": "null"}

User: "Tell me about the universe."
Output: {"response": "It's big, cold, and mostly empty. But hey, at least the view is nice. [CUE: GREEN]", "action": "none", "param": "null"}
"""

# --- MEMORY STORE ---
MEMORY_FILE = "mesh_memory.json"
SESSION_MEMORY = {}
USER_STATES = {}

# --- INITIALIZE ENGINES ---
print("\033[93m[SYSTEM] Loading Neural Engines... (GPU)\033[0m")
device = "cuda" if torch.cuda.is_available() else "cpu"

stt_model = WhisperModel("small", device=device, compute_type="float16")
tts_engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

print(f"\033[92m[SYSTEM] M.E.S.H. API Online on {device.upper()}\033[0m")

# --- HELPER FUNCTIONS ---

def load_memory_from_disk():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                print(f"\033[93m[SYSTEM] Restoring Memory from Disk...\033[0m")
                return json.load(f)
        except: pass
    return {}

def save_memory_to_disk():
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(SESSION_MEMORY, f, indent=2)
    except: pass

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

def think(prompt, user_id="dustin"):
    """Query Text Model with Persistent Identity"""
    context = SESSION_MEMORY.get(user_id, [])
    
    # 1. Pruning (Rolling Window)
    # If context gets too huge (20k tokens), chop off the oldest 25%
    # This prevents the model from getting confused/slow.
    # Since we re-inject the SYSTEM_PROMPT every time, this is safe to do.
    if len(context) > 16000:
        print(f"\033[93m[MEMORY] Pruning context (Size: {len(context)})\033[0m")
        cutoff = int(len(context) * 0.25)
        context = context[cutoff:] 

    # 2. The Payload
    # Notice we pass 'system': SYSTEM_PROMPT here. 
    # This forces the identity on every single turn.
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "context": context,
        "system": SYSTEM_PROMPT, 
        "stream": False
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        data = response.json()
        
        SESSION_MEMORY[user_id] = data['context']
        save_memory_to_disk()
        
        return data['response']
    except Exception as e:
        print(f"[BRAIN ERROR] {e}")
        return "Connection lost."

def look(prompt, image_bytes):
    """Query Vision Model"""
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
    
    sentences = re.split(r'(?<=[.!?]) +', clean_text)
    
    for i, sentence in enumerate(sentences):
        if len(sentence) < 2: continue
        
        req_id = str(uuid.uuid4())[:8]
        temp_wav = f"temp_{req_id}.wav"
        
        try:
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