import warnings
# Silence noisy 3rd-party FutureWarnings (transformers, torch, etc.) before imports
warnings.filterwarnings("ignore", category=FutureWarning)

import asyncio
import uvicorn
import io
import json
import time
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse

# Server imports

from mesh_common.logging import get_logger
from mesh_common.config import SAMPLE_RATE
from mesh_server import brain
from mesh_server import voice_engine
from mesh_server import config

logger = get_logger("mesh_server")
app = FastAPI(title="M.E.S.H. Server")

@app.middleware("http")
async def add_process_time_header(request, call_next):
    request.state.start_time = time.time()
    response = await call_next(request)
    return response

# --- STATE ---
USER_STATES = {}

# --- STREAM LOGIC ---

async def interact_generator(audio_bytes):
    start_total = time.time()
    audio_buffer = io.BytesIO(audio_bytes)
    
    # 1. Transcribe (In background thread!)
    user_text = await asyncio.to_thread(voice_engine.transcribe, audio_buffer)
    
    if user_text:
        logger.info(f"[HEARD] '{user_text}'")

    if not user_text:
        yield json.dumps({"status": "no_speech"}).encode()
        return

    # 2. Logic (Wake words, attention, etc.)
    user_id = "dustin"
    clean_input = user_text.lower().strip()
    last_seen = USER_STATES.get(user_id, 0)
    is_focused = (time.time() - last_seen) < config.ATTENTION_SPAN
    trigger_word = next((w for w in config.WAKE_WORDS if w in clean_input), None)
    
    final_prompt = user_text
    remaining_command = ""
    
    if trigger_word:
        parts = clean_input.partition(trigger_word)
        remaining_command = parts[2].strip(" .,?!")
        final_prompt = remaining_command

    # Determine feedback sound: 'ack' for pokes, 'processing' for logic
    is_poke = len(final_prompt) < 2
    ack_type = "ack" if is_poke else "processing"

    if trigger_word:
        logger.info(f"[TRIGGER] {trigger_word}")
        USER_STATES[user_id] = time.time()
        
        # Feedback on trigger
        ack_bytes = voice_engine.get_prebaked_sound(ack_type)
        if ack_bytes:
            logger.info(f"[FEEDBACK] Yielding {ack_type} sound...")
            yield ack_bytes
        
        if len(remaining_command) < 2: return 
    elif is_focused:
        # If already focused, only say "Checking..." for longer commands
        USER_STATES[user_id] = time.time()
        if not is_poke:
            ack_bytes = voice_engine.get_prebaked_sound("processing")
            if ack_bytes:
                logger.info(f"[FEEDBACK] Yielding processing sound (Focused mode)...")
                yield ack_bytes
    else:
        logger.debug(f"[IGNORED] {clean_input}")
        yield json.dumps({"status": "ignored"}).encode()
        return

    logger.info(f"User: {final_prompt}")

    # 3. Think (In background thread!)
    if "shut down" in final_prompt.lower() or "power off" in final_prompt.lower():
        def get_all_chunks(text): return list(voice_engine.speak_generator(text))
        chunks = await asyncio.to_thread(get_all_chunks, "Powering down. Goodnight.")
        for chunk in chunks: yield chunk
        return

    raw_response = await asyncio.to_thread(brain.think, final_prompt, user_id)
    parsed = brain.robust_json_parse(raw_response)
    spoken_text = parsed.get("response", "Data error.")
    hardware_command = parsed.get("action", "none")
    hardware_param = parsed.get("param", "null")

    if hardware_command != "none":
        logger.info(f"[COMMAND] {hardware_command} -> {hardware_param}")
        cmd_payload = json.dumps({"action": hardware_command, "param": hardware_param})
        yield (cmd_payload + "\n").encode("utf-8")

    import re

    # 4. Speak & Act (with Tag Parsing)
    # Split by tags: e.g. "Text [ACTION: LOOK_LEFT] More text"
    # Regex: (\[ACTION: [A-Z_]+\]) capturing group keeps the delimiter
    parts = re.split(r'(\[ACTION: [A-Z_]+\])', spoken_text)
    
    for part in parts:
        if not part.strip(): continue
        
        # Check if it's a tag
        if part.startswith("[ACTION:") and part.endswith("]"):
            # Extract action name: [ACTION: LOOK_LEFT] -> LOOK_LEFT -> look_left
            action_raw = part[8:-1].strip().lower()
            logger.info(f"[TIMED ACTION] {action_raw}")
            
            # Send as command
            cmd_payload = json.dumps({"action": action_raw, "param": "trigger"})
            yield (cmd_payload + "\n").encode("utf-8")
        else:
            # It's speech
            for chunk in voice_engine.speak_generator(part):
                yield chunk
        
    logger.info(f"[LATENCY] Total Interaction Time: {time.time() - start_total:.2f}s")

def vision_interaction_stream(image_bytes, prompt):
    logger.info(f"User (Vision): {prompt}")
    vision_response = brain.look(prompt, image_bytes)
    yield from voice_engine.speak_generator(vision_response)

# --- API ENDPOINTS ---

@app.post("/interact")
async def interact_endpoint(request: Request, audio_file: UploadFile = File(...)):
    start_time = getattr(request.state, "start_time", time.time())
    overhead = time.time() - start_time
    logger.info(f"[LATENCY] Request Overhead: {overhead:.2f}s")

    read_start = time.time()
    audio_bytes = await audio_file.read()
    logger.info(f"[LATENCY] Server File Read: {time.time() - read_start:.2f}s")
    
    return StreamingResponse(
        interact_generator(audio_bytes),
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
    # When running directly, we use host 0.0.0.0 for network access
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
