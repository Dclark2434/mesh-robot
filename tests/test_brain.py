import sys
import os
import pytest

# Test imports

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
