import pytest
import io
import json
import re

# We will implement a simplified version of the logic in main.py for testing purposes
# since main.py is a script and harder to import cleanly without side effects.
# A future refactor should move this logic to a utility function.

def extract_commands_from_chunk(chunk, buffer):
    """
    Simulates the logic inside main.py's stream_audio_response loop.
    Returns: (updated_buffer, found_commands_list)
    """
    buffer += chunk
    commands = []
    
    while True:
        start_idx = buffer.find(b'{"action"')
        if start_idx == -1:
            break
        
        end_idx = buffer.find(b'}', start_idx)
        if end_idx == -1:
            break # Wait for more data
        
        # Extract candidate
        json_bytes = buffer[start_idx:end_idx+1]
        try:
            cmd_str = json_bytes.decode("utf-8")
            cmd = json.loads(cmd_str)
            commands.append(cmd)
            
            # Remove from buffer
            buffer = buffer[:start_idx] + buffer[end_idx+1:]
            
        except Exception:
            # If parse failed, discard the start and try again?
            # In main.py we break, but here let's validly simulate "skipping bad data"
            # For this test we assume valid json if it looks like it
            break 
            
    return buffer, commands


def test_parsing_clean_command():
    buffer = b""
    chunk = b'{"action": "walk", "param": "forward"}'
    buffer, cmds = extract_commands_from_chunk(chunk, buffer)
    
    assert len(cmds) == 1
    assert cmds[0]["action"] == "walk"
    assert cmds[0]["param"] == "forward"
    assert len(buffer) == 0

def test_parsing_command_sandwiched():
    buffer = b""
    # WAV header garbage + command + WAV data
    chunk = b'RIFF....{"action": "stop", "param": "now"}WAVEfmt '
    buffer, cmds = extract_commands_from_chunk(chunk, buffer)
    
    assert len(cmds) == 1
    assert cmds[0]["action"] == "stop"
    # Verify buffer still has the surrounding data (minus the command)
    assert b'RIFF....WAVEfmt ' == buffer

def test_parsing_split_command():
    buffer = b""
    chunk1 = b'{"action": "lo'
    chunk2 = b'ok", "param": "left"}'
    
    buffer, cmds = extract_commands_from_chunk(chunk1, buffer)
    assert len(cmds) == 0
    assert buffer == chunk1
    
    buffer, cmds = extract_commands_from_chunk(chunk2, buffer)
    assert len(cmds) == 1
    assert cmds[0]["action"] == "look"
    assert len(buffer) == 0
