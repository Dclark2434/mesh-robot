import sys
import os
import pytest

# Add src to path
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mesh_server import brain

def test_robust_json_parse_clean():
    raw = '{"response": "Hello", "action": "none", "param": "null"}'
    parsed = brain.robust_json_parse(raw)
    assert parsed["response"] == "Hello"
    assert parsed["action"] == "none"

def test_robust_json_parse_with_markdown():
    raw = 'Sure! Here is the JSON: ```json\n{"response": "Cynical reply", "action": "look", "param": "left"}\n```'
    parsed = brain.robust_json_parse(raw)
    assert parsed["response"] == "Cynical reply"
    assert parsed["action"] == "look"
    assert parsed["param"] == "left"

def test_robust_json_parse_fallback():
    raw = "This is not JSON at all."
    parsed = brain.robust_json_parse(raw)
    assert parsed["response"] == raw
    assert parsed["action"] == "none"
