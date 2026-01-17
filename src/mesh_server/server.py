import warnings
# Silence noisy 3rd-party FutureWarnings (transformers, torch, etc.) before imports
warnings.filterwarnings("ignore", category=FutureWarning)
# Silence specific Transformers warnings about Llama attention and cache
warnings.filterwarnings("ignore", message=".*LlamaModel is using LlamaSdpaAttention.*")
warnings.filterwarnings("ignore", message=".*passing `past_key_values` as a tuple.*")

import asyncio
import uvicorn
import io
import json
import time
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
import re

# Server imports

from mesh_common.logging import get_logger
from mesh_common.config import SAMPLE_RATE
from mesh_server import brain
from mesh_server import voice_engine
from mesh_server import config

# Warmup Neural Engine
voice_engine.warmup()

logger = get_logger("mesh_server")
app = FastAPI(title="M.E.S.H. Server")

@app.middleware("http")
async def add_process_time_header(request, call_next):
    request.state.start_time = time.time()
    response = await call_next(request)
    return response

# --- STATE ---
USER_STATES = {}

# Session Statistics
SESSION_STATS = {
    "stt": [],
    "llm": [],
    "ttfb": [],
    "tts_tot": [],
    "total": []
}

@app.on_event("shutdown")
def print_latency_report():
    """Prints a statistical report of session latency upon exit."""
    from statistics import mean
    
    print("\n" + "="*60)
    print(f"   M.E.S.H. SESSION LATENCY REPORT   ")
    print("="*60)
    
    headers = ["Metric", "Count", "Avg (s)", "Min (s)", "Max (s)"]
    row_fmt = "{:<15} | {:<5} | {:<7} | {:<7} | {:<7}"
    print(row_fmt.format(*headers))
    print("-" * 60)
    
    # Updated metric list with TTS Total
    for metric in ["stt", "llm", "ttfb", "tts_tot", "total"]:
        data = SESSION_STATS[metric]
        if data:
            row = [
                metric.upper(),
                len(data),
                f"{mean(data):.2f}",
                f"{min(data):.2f}",
                f"{max(data):.2f}"
            ]
            print(row_fmt.format(*row))
        else:
            print(f"{metric.upper():<15} | 0     | N/A     | N/A     | N/A")
            
    print("="*60 + "\n")

# --- STREAM LOGIC ---

async def interact_generator(audio_bytes, image_bytes=None, text_prompt=None, telemetry=None):
    start_total = time.time()
    
    user_text = ""
    
    # 1. Transcribe (if audio present)
    if audio_bytes:
        audio_buffer = io.BytesIO(audio_bytes)
        stt_start = time.time()
        user_text = await asyncio.to_thread(voice_engine.transcribe, audio_buffer)
        stt_duration = time.time() - stt_start
        
        if user_text:
            logger.info(f"[HEARD] '{user_text}'")
            SESSION_STATS["stt"].append(stt_duration)
    
    # 2. Text Override/Fallback
    if text_prompt:
        # If we have both, maybe append? OR override. 
        # Typically text_prompt comes from "See" action ("Describe this") or specialized client.
        if user_text:
            user_text += f" {text_prompt}"
        else:
            user_text = text_prompt
            logger.info(f"[TEXT INPUT] '{user_text}'")

    if not user_text and not image_bytes:
        # If we have an image but no text, we can default to "Look at this."
        # But if we have neither, ignoring.
        yield json.dumps({"status": "no_speech"}).encode()
        return
        
    # Default prompt for image-only input
    if image_bytes and not user_text:
        user_text = "What do you see here?"

    # 3. Logic (Wake words, attention, etc.)
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
        
    # INJECT TELEMETRY INTO PROMPT
    # We add it as a system note inside the user message
    if telemetry:
        try:
            # Parse Telemetry
            t_data = json.loads(telemetry)
            
            # Smart Injection: Only inject if LOW or relevant keyword in prompt
            servo_p = t_data.get('servo_percent', 100)
            logic_p = t_data.get('logic_percent', 100)
            
            is_low = (servo_p < 30) or (logic_p < 30)
            is_relevant = any(kw in clean_input for kw in ['battery', 'power', 'charge', 'status', 'level', 'voltage'])
            
            if is_low or is_relevant:
                t_str = (
                    f"[SYSTEM DATA: "
                    f"Servo={t_data.get('servo_voltage')}V ({servo_p}%), "
                    f"Logic={t_data.get('logic_voltage')}V ({logic_p}%)"
                    f"]"
                )
                final_prompt = f"{t_str} {final_prompt}"
                logger.info(f"[CONTEXT] Injected: {t_str}")
            else:
                logger.debug("[CONTEXT] Telemetry available but not relevant (Healthy & not asked).")
        except Exception as e:
            logger.warning(f"Telemetry parse error: {e}")
            pass

    # Determine feedback sound: 'ack' for pokes, 'processing' for logic
    is_poke = len(final_prompt) < 2
    ack_type = "ack" if is_poke else "processing"

    if trigger_word:
        logger.info(f"[TRIGGER] {trigger_word}")
        # Add buffer (2.0s) to account for 'Ack' sound playback and reaction time
        USER_STATES[user_id] = time.time() + 2.0
        
        # Feedback on trigger
        ack_bytes = voice_engine.get_prebaked_sound(ack_type)
        if ack_bytes:
            logger.info(f"[FEEDBACK] Yielding {ack_type} sound...")
            yield ack_bytes
        
        if len(remaining_command) < 2: return 
    elif is_focused:
        # If already focused, only say "Checking..." for longer commands
        USER_STATES[user_id] = time.time()
        ack_bytes = voice_engine.get_prebaked_sound("processing")
        
        # Suppress feedback for automated vision requests
        # Why? Because the robot just clicked the camera, no need to beep again.
        is_automated_vision = "describe what you see" in clean_input and "image" in clean_input
        
        if ack_bytes and not is_poke and not is_automated_vision:
             logger.info(f"[FEEDBACK] Yielding processing sound (Focused mode)...")
             yield ack_bytes
    else:
        logger.debug(f"[IGNORED] {clean_input}")
        yield json.dumps({"status": "ignored"}).encode()
        return

    # FAST PATH: VISION
    # Heuristic: If user says "look at this" or "what do you see" with NO image,
    # skip the "Okay, I will look" LLM step and trigger the camera immediately.
    vision_patterns = [
        r"look at (this|that|what)",
        r"what (do|can) you see",
        r"what is (this|that|it)",
        r"describe (this|that|the scene|what)",
        r"tell me what you see"
    ]
    
    # Only trigger if we DON'T have an image yet and we have a valid prompt
    if not image_bytes and any(re.search(p, clean_input) for p in vision_patterns):
        logger.info(f"[FAST PATH] Vision Triggered by: '{clean_input}'")
        
        # 1. Yield Action immediately
        # We use a special param to indicate origin, though effectively just 'see'
        yield json.dumps({"action": "see", "param": "fast_path"}).encode("utf-8") + b"\n"
        
        # 2. Stop Processing 
        # (Don't call LLM, don't speak, just wait for client to call back with image)
        return

    logger.info(f"User: {final_prompt}")

    # 3. Think (In background thread!)
    if "shut down" in final_prompt.lower() or "power off" in final_prompt.lower():
        def get_all_chunks(text): return list(voice_engine.speak_generator(text))
        chunks = await asyncio.to_thread(get_all_chunks, "Powering down. Goodnight.")
        for chunk in chunks: yield chunk
        return
    
    llm_start = time.time()
    raw_response = await asyncio.to_thread(brain.think, final_prompt, user_id, image_bytes)
    llm_duration = time.time() - llm_start
    SESSION_STATS["llm"].append(llm_duration)
    
    parsed = brain.robust_json_parse(raw_response)
    spoken_text = parsed.get("response", "Data error.")
    hardware_command = parsed.get("action", "none")
    hardware_param = parsed.get("param", "null")

    if hardware_command != "none":
        logger.info(f"[COMMAND] {hardware_command} -> {hardware_param}")
        cmd_payload = json.dumps({"action": hardware_command, "param": hardware_param})
        yield (cmd_payload + "\n").encode("utf-8")
        
        # Anti-Redundancy: Use Regex to remove the tag (handling spacing/variations)
        # Matches [ACTION: CMD] or [ACTION:CMD] case insensitive
        pattern = rf"\[ACTION:\s*{hardware_command}\]"
        spoken_text = re.sub(pattern, "", spoken_text, flags=re.IGNORECASE).strip()
        logger.info(f"[FILTER] Applied redundancy filter for {hardware_command}")


    # 4. Speak & Act (with Tag Parsing)
    # Split by tags: e.g. "Text [ACTION: LOOK_LEFT] More text"
    # Regex: (\[ACTION: [A-Z_]+\]) capturing group keeps the delimiter
    parts = re.split(r'(\[ACTION: [A-Z_]+\])', spoken_text)
    
    ttfb_captured = False
    tts_start_time = time.time()
    bytes_sent = 0
    
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
            # We only track TTFB for the first speech chunk of the response
            for chunk in voice_engine.speak_generator(part):
                if not ttfb_captured:
                    ttfb = time.time() - tts_start_time
                    SESSION_STATS["ttfb"].append(ttfb)
                    ttfb_captured = True
                
                valid_chunk = chunk if isinstance(chunk, bytes) else b"" # Safety
                bytes_sent += len(valid_chunk)
                yield chunk
    
    # Record Total TTS generation time (if we spoke)
    if ttfb_captured:
        tts_total_duration = time.time() - tts_start_time
        SESSION_STATS["tts_tot"].append(tts_total_duration)
        
    # Reset attention span timer AFTER he finishes speaking/acting
    # CRITICAL FIX: Server generates faster than realtime. 
    # We must add the audio duration to the timestamp so the timeout counts from when he FINISHES speaking.
    # Format: 24kHz, 16-bit, Mono = 48000 bytes/sec
    audio_duration = bytes_sent / 48000.0
    USER_STATES[user_id] = time.time() + audio_duration
    logger.info(f"[LATENCY] Total Interaction Time: {time.time() - start_total:.2f}s (Audio Duration: {audio_duration:.2f}s, Bytes: {bytes_sent})")
    SESSION_STATS["total"].append(time.time() - start_total)

# --- API ENDPOINTS ---

@app.post("/interact")
async def interact_endpoint(
    request: Request, 
    audio_file: UploadFile = File(None), 
    image_file: UploadFile = File(None),
    prompt: str = Form(None),
    telemetry: str = Form(None)
):
    start_time = getattr(request.state, "start_time", time.time())
    overhead = time.time() - start_time
    logger.info(f"[LATENCY] Request Overhead: {overhead:.2f}s")

    # Read Inputs
    audio_bytes = None
    if audio_file:
        read_start = time.time()
        audio_bytes = await audio_file.read()
        logger.info(f"[LATENCY] Server Audio Read: {time.time() - read_start:.2f}s")
    
    image_bytes = None
    if image_file:
        read_start = time.time()
        image_bytes = await image_file.read()
        logger.info(f"[LATENCY] Server Image Read: {time.time() - read_start:.2f}s")

    return StreamingResponse(
        interact_generator(audio_bytes, image_bytes, prompt, telemetry),
        media_type="audio/wav"
    )

# Clean up /see endpoint as it is now merged
# (Removing check for cleaner file)

if __name__ == "__main__":
    # When running directly, we use host 0.0.0.0 for network access
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
