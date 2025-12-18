from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
import asyncio
import uvicorn
import io
import json
import time
import brain
import voice_engine
import config

app = FastAPI()

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
        print(f"\033[92m[TRIGGER] {trigger_word}\033[0m")
        USER_STATES[user_id] = time.time()
        
        # Feedback on trigger
        ack_bytes = voice_engine.get_prebaked_sound(ack_type)
        if ack_bytes:
            print(f"\033[93m[FEEDBACK] Yielding {ack_type} sound...\033[0m")
            yield ack_bytes
        
        if len(remaining_command) < 2: return 
    elif is_focused:
        # If already focused, only say "Checking..." for longer commands
        USER_STATES[user_id] = time.time()
        if not is_poke:
            ack_bytes = voice_engine.get_prebaked_sound("processing")
            if ack_bytes:
                print(f"\033[93m[FEEDBACK] Yielding processing sound (Focused mode)...\033[0m")
                yield ack_bytes
    else:
        print(f"\033[90m[IGNORED] {clean_input}\033[0m")
        yield json.dumps({"status": "ignored"}).encode()
        return

    print(f"\033[94mUser:\033[0m {final_prompt}")

    # 3. Think (In background thread!)
    if "shut down" in final_prompt.lower() or "power off" in final_prompt.lower():
        # Wrapping the generator exhaustion in to_thread is safer for blocking TTS
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
        print(f"\033[93m[COMMAND] {hardware_command} -> {hardware_param}\033[0m")

    # 4. Speak
    # We yield parts as they are generated. 
    # Since speak_generator themselves block per-sentence, we loop.
    for chunk in voice_engine.speak_generator(spoken_text):
        yield chunk
        
    print(f"\033[96m[LATENCY] Total Interaction Time: {time.time() - start_total:.2f}s\033[0m")

def vision_interaction_stream(image_bytes, prompt):
    print(f"\033[94mUser (Vision):\033[0m {prompt}")
    vision_response = brain.look(prompt, image_bytes)
    yield from voice_engine.speak_generator(vision_response)

# --- API ENDPOINTS ---

@app.post("/interact")
async def interact_endpoint(request: Request, audio_file: UploadFile = File(...)):
    # 0. Measure Request Overhead
    start_time = getattr(request.state, "start_time", time.time())
    overhead = time.time() - start_time
    print(f"\033[96m[LATENCY] Request Overhead (Network/Buffering): {overhead:.2f}s\033[0m")

    # 1. Read Bytes First (FastAPI needs to read the body before we can stream back)
    # This is the last bottleneck before the generator takes over.
    read_start = time.time()
    audio_bytes = await audio_file.read()
    print(f"\033[96m[LATENCY] Server File Read: {time.time() - read_start:.2f}s\033[0m")
    
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
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
