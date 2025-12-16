from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import uvicorn
import io
import time
import brain
import voice_engine
import config

app = FastAPI()

# --- STATE ---
USER_STATES = {}

# --- STREAM LOGIC ---

def text_interaction_stream(user_text):
    user_id = "dustin"
    clean_input = user_text.lower().strip()
    
    # 1. CHECK STATE
    last_seen = USER_STATES.get(user_id, 0)
    is_focused = (time.time() - last_seen) < config.ATTENTION_SPAN
    
    # 2. CHECK WAKE WORD
    trigger_word = next((w for w in config.WAKE_WORDS if w in clean_input), None)
    
    final_prompt = user_text
    
    if trigger_word:
        print(f"\033[92m[TRIGGER] {trigger_word}\033[0m")
        USER_STATES[user_id] = time.time()
        
        parts = clean_input.partition(trigger_word)
        remaining_command = parts[2].strip(" .,?!")
        
        # Determine sound based on whether there's an immediate command
        if len(remaining_command) > 2:
           ack_bytes = voice_engine.get_prebaked_sound("processing")
        else:
           ack_bytes = voice_engine.get_prebaked_sound("ack")
           
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
        yield from voice_engine.speak_generator("Powering down. Goodnight.")
        # We can yield a special header/byte here for the client to exit, 
        # but for now just saying it is enough.
        return

    raw_response = brain.think(final_prompt, user_id)
    
    # 4. PARSE
    parsed = brain.robust_json_parse(raw_response)
    spoken_text = parsed.get("response", "Data error.")
    hardware_command = parsed.get("action", "none")
    hardware_param = parsed.get("param", "null")

    if hardware_command != "none":
        print(f"\033[93m[COMMAND] {hardware_command} -> {hardware_param}\033[0m")

    # 5. SPEAK
    yield from voice_engine.speak_generator(spoken_text)

def vision_interaction_stream(image_bytes, prompt):
    print(f"\033[94mUser (Vision):\033[0m {prompt}")
    vision_response = brain.look(prompt, image_bytes)
    yield from voice_engine.speak_generator(vision_response)

# --- API ENDPOINTS ---

@app.post("/interact")
async def interact_endpoint(audio_file: UploadFile = File(...)):
    audio_bytes = await audio_file.read()
    audio_buffer = io.BytesIO(audio_bytes)
    
    user_text = voice_engine.transcribe(audio_buffer)

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
