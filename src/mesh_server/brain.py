import os
import json
import time
import requests
import base64
import io
import re
from typing import Dict, Any, List, Optional
from PIL import Image
from google import genai
from google.genai import types

from mesh_common.logging import get_logger
from mesh_server import config

logger = get_logger("mesh_brain")

# --- MEMORY STATE ---
SESSION_MEMORY = {}  # { user_id: { context, summary, turn_count, last_updated } }

# Initialize Gemini Client if enabled
client = None
if config.USE_GEMINI:
    if not config.GEMINI_API_KEY:
         raise ValueError("GEMINI_API_KEY environment variable not set. Set USE_GEMINI=False to use Ollama instead.")
    client = genai.Client(api_key=config.GEMINI_API_KEY)

def get_effective_system_prompt():
    """Returns the base system prompt, appending audio tags if ElevenLabs is enabled."""
    prompt = config.SYSTEM_PROMPT
    # Enable tags for ElevenLabs OR Chatterbox
    if config.USE_ELEVENLABS or config.TTS_ENGINE == "chatterbox":
        # Check if attribute exists to avoid crashes if config isn't reloaded yet
        if hasattr(config, 'AUDIO_TAGS_INSTRUCTIONS'):
            prompt += config.AUDIO_TAGS_INSTRUCTIONS
    return prompt

def get_gemini_config():
    """Returns the standard configuration for Gemini model calls."""
    return types.GenerateContentConfig(
        system_instruction=get_effective_system_prompt(),
        response_mime_type="application/json",
        response_schema={
            "type": "OBJECT",
            "properties": {
                "response": {"type": "STRING"},
                "action": {"type": "STRING"},
                "param": {"type": "STRING"}
            },
            "required": ["response", "action", "param"]
        }
    )

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
    global SESSION_MEMORY
    if os.path.exists(config.MEMORY_FILE):
        try:
            with open(config.MEMORY_FILE, "r") as f:
                logger.info("Restoring Memory from Disk...")
                data = json.load(f)
                
                # Migration: Old format was { user_id: [context_tokens] }
                # New format is { user_id: { context, summary, turn_count, last_updated } }
                migrated = {}
                for user_id, value in data.items():
                    if isinstance(value, list):
                        # Old format detected, migrate
                        logger.warning(f"Migrating old memory format for {user_id}")
                        migrated[user_id] = {
                            "context": value,
                            "summary": "",
                            "turn_count": 0,
                            "last_updated": time.time()
                        }
                    else:
                        migrated[user_id] = value
                SESSION_MEMORY = migrated
                return migrated
        except Exception as e:
            logger.error(f"Failed to load memory: {e}")
    return {}

def save_memory_to_disk():
    """Persist memory to disk."""
    try:
        if not os.path.exists(config.DATA_DIR):
            os.makedirs(config.DATA_DIR)
        with open(config.MEMORY_FILE, "w") as f:
            json.dump(SESSION_MEMORY, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save memory: {e}")

def summarize_context(context_tokens, user_id):
    """
    Ask the LLM to summarize the conversation so far.
    Used when context exceeds threshold to compress older history.
    """
    try:
        # Use a character-consistent summarization prompt.
        # Critical: Keep this factual, not conversational.
        summarize_prompt = (
            "Create a brief mission log entry. Facts only. "
            "What did Dustin ask about? What did we decide? "
            "Do NOT include phrases like 'expressed preference' or 'came to a close'. "
            "Just the facts. 2-3 sentences. Example format: "
            "'Discussed X. Dustin wanted Y. Decided on Z.'"
        )
        payload = {
            "model": config.OLLAMA_MODEL_NAME,
            "prompt": summarize_prompt,
            "context": context_tokens,
            "system": config.SYSTEM_PROMPT,  # Keep personality during summarization
            "stream": False
        }
        response = requests.post(config.OLLAMA_URL, json=payload)
        data = response.json()
        summary = data.get('response', '').strip()
        
        # Clean up any JSON formatting the model might include
        if '{' in summary:
            try:
                parsed = json.loads(summary[summary.find('{'):summary.rfind('}')+1])
                summary = parsed.get('response', summary)
            except:
                pass
        
        return summary[:config.SUMMARY_MAX_LENGTH]  # Truncate if too long
    except Exception as e:
        logger.error(f"Summarization error: {e}")
        return ""

def think_gemini(prompt, user_id="dustin"):
    """
    Gemini implementation of the brain.
    Leverages large context window to persist full chat history without immediate pruning.
    """
    # Get memory
    if user_id not in SESSION_MEMORY:
        SESSION_MEMORY[user_id] = get_default_user_memory()
    user_memory = SESSION_MEMORY[user_id]
    
    # Load history (list of dicts)
    # New Format: [{'role': 'user', 'parts': ['...']}, ...]
    history = user_memory.get("gemini_history", [])
    
    try:
        # Build contents from history + current prompt
        contents = []
        for turn in history:
            # New SDK expect Parts
            p_list = [types.Part(text=p) for p in turn['parts']]
            contents.append(types.Content(role=turn['role'], parts=p_list))
        
        # Add current user prompt
        contents.append(types.Content(role='user', parts=[types.Part(text=prompt)]))
        
        start_time = time.time()
        response = client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=contents,
            config=get_gemini_config()
        )
        duration = time.time() - start_time
        logger.info(f"[LATENCY] LLM (Gemini): {duration:.2f}s")
        
        # Update memory with new history
        history.append({"role": "user", "parts": [prompt]})
        history.append({"role": "model", "parts": [response.text]})
            
        user_memory["gemini_history"] = history
        user_memory["last_updated"] = time.time()
        
        SESSION_MEMORY[user_id] = user_memory
        save_memory_to_disk()
        
        return response.text
        
    except Exception as e:
        logger.error(f"Gemini API Error: {type(e).__name__} - {str(e)}")
        return json.dumps({"response": "Signal interference. Repeat.", "action": "none", "param": "null"})

def think(prompt, user_id="dustin"):
    """Query Text Model with Persistent Identity and Rolling Memory"""
    
    # --- GEMINI PATH ---
    if config.USE_GEMINI:
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
    if len(context) > config.CONTEXT_THRESHOLD:
        logger.info(f"Context large ({len(context)}), summarizing...")
        
        # Summarize the oldest half of the context
        half = len(context) // 2
        old_context = context[:half]
        new_summary = summarize_context(old_context, user_id)
        
        if new_summary:
            # Combine with existing summary, keeping it concise
            if existing_summary:
                combined = f"{existing_summary} {new_summary}"
                # Keep only the most recent SUMMARY_MAX_LENGTH chars
                existing_summary = combined[-config.SUMMARY_MAX_LENGTH:]
            else:
                existing_summary = new_summary
            
            user_memory["summary"] = existing_summary
            logger.info(f"Summary updated: {existing_summary[:80]}...")
        
        # Keep only the recent half of context
        context = context[half:]
        user_memory["context"] = context

    # 2. Build System Prompt with Summary
    # Prepend conversation history if we have a summary
    # CRITICAL: Reinforce personality after summary to prevent drift
    effective_system = get_effective_system_prompt()
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
        effective_system = summary_header + config.SYSTEM_PROMPT

    # 3. The Payload
    payload = {
        "model": config.OLLAMA_MODEL_NAME,
        "prompt": prompt,
        "context": context,
        "system": effective_system,
        "stream": False
    }
    
    try:
        start_time = time.time()
        response = requests.post(config.OLLAMA_URL, json=payload)
        duration = time.time() - start_time
        logger.info(f"[LATENCY] LLM (Ollama): {duration:.2f}s")
        data = response.json()
        
        # Update memory
        user_memory["context"] = data['context']
        user_memory["turn_count"] = user_memory.get("turn_count", 0) + 1
        user_memory["last_updated"] = time.time()
        SESSION_MEMORY[user_id] = user_memory
        save_memory_to_disk()
        
        return data['response']
    except Exception as e:
        logger.error(f"Brain Error: {e}")
        return "Connection lost."

def look(prompt, image_bytes):
    """Query Vision Model"""
    
    # --- GEMINI PATH ---
    if config.USE_GEMINI:
        try:
            # Construct parts: prompt + image
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/jpeg')
            
            start_time = time.time()
            response = client.models.generate_content(
                model='gemini-3-flash-preview',
                contents=[prompt, image_part],
                config=types.GenerateContentConfig(
                    system_instruction="SYSTEM: You are MESH. Tactical Robot. Analyze this image. Be cynical, dry, brief."
                )
            )
            duration = time.time() - start_time
            logger.info(f"[LATENCY] Vision (Gemini): {duration:.2f}s")
            return response.text
        except Exception as e:
            logger.error(f"Gemini Vision Error: {e}")
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
        response = requests.post(config.OLLAMA_CHAT_URL, json=payload)
        data = response.json()
        return data['message']['content']
    except Exception as e:
        logger.error(f"Vision Error: {e}")
        return "Visual sensors offline."

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

# Need re for robust_json_parse
import re
