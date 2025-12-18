import os
import json
import time
import requests
import base64
import io
from PIL import Image
import google.generativeai as genai
import config

# --- MEMORY STATE ---
SESSION_MEMORY = {}  # { user_id: { context, summary, turn_count, last_updated } }

# Initialize Gemini Model if enabled
gemini_model = None
if config.USE_GEMINI:
    if not config.GEMINI_API_KEY:
         raise ValueError("GEMINI_API_KEY environment variable not set. Set USE_GEMINI=False to use Ollama instead.")
    genai.configure(api_key=config.GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel(
        'gemini-3-flash-preview',
        system_instruction=config.SYSTEM_PROMPT,
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
                SESSION_MEMORY = migrated
                return migrated
        except Exception as e:
            print(f"[MEMORY ERROR] Failed to load: {e}")
    return {}

def save_memory_to_disk():
    """Persist memory to disk."""
    try:
        if not os.path.exists(config.DATA_DIR):
            os.makedirs(config.DATA_DIR)
        with open(config.MEMORY_FILE, "w") as f:
            json.dump(SESSION_MEMORY, f, indent=2)
    except Exception as e:
        print(f"[MEMORY ERROR] Failed to save: {e}")

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
    
    try:
        # Start chat with history
        chat = gemini_model.start_chat(history=history)
        
        # Send message
        start_time = time.time()
        response = chat.send_message(prompt)
        duration = time.time() - start_time
        print(f"\033[96m[LATENCY] LLM (Gemini): {duration:.2f}s\033[0m")
        
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
        print(f"\n\033[91m[GEMINI API ERROR] An error occurred while calling the Gemini API:\033[0m")
        print(f"\033[91mType:\033[0m {type(e).__name__}")
        print(f"\033[91mMessage:\033[0m {str(e)}")
        
        # Introspect for more details common in Google API clients
        if hasattr(e, 'metadata'):
            print(f"\033[93mMetadata:\033[0m {e.metadata}")
        if hasattr(e, 'details'):
            print(f"\033[93mDetails:\033[0m {e.details() if callable(e.details) else e.details}")
        if hasattr(e, 'reason'):
            print(f"\033[93mReason:\033[0m {e.reason}")
        if hasattr(e, 'headers'):
            print(f"\033[93mHeaders:\033[0m {e.headers}")
            
        print("-" * 40) # Visual separator
        return json.dumps({"response": "Signal interference. Repeat.", "action": "none"})

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
                existing_summary = combined[-config.SUMMARY_MAX_LENGTH:]
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
    effective_system = config.SYSTEM_PROMPT
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
        print(f"\033[96m[LATENCY] LLM (Ollama): {duration:.2f}s\033[0m")
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
    if config.USE_GEMINI:
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
            
            start_time = time.time()
            response = gemini_model.generate_content([prompt, image])
            duration = time.time() - start_time
            print(f"\033[96m[LATENCY] Vision (Gemini): {duration:.2f}s\033[0m")
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
        response = requests.post(config.OLLAMA_CHAT_URL, json=payload)
        data = response.json()
        return data['message']['content']
    except Exception as e:
        print(f"[VISION ERROR] {e}")
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
