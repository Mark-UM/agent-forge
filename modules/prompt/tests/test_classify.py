"""Tests for modules.prompt.classify (v1.5 P0)"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import classify


# ---------- classify_heuristic ----------

def test_classify_heuristic_coding():
    assert classify.classify_heuristic("implement a new function") == "coding"


def test_classify_heuristic_coding_chinese():
    # 修复 → coding (matches "修复" keyword)
    assert classify.classify_heuristic("修复这个 bug") == "coding"


def test_classify_heuristic_debugging():
    assert classify.classify_heuristic("this crashes on startup") == "debugging"
    assert classify.classify_heuristic("error in module") == "debugging"


def test_classify_heuristic_review():
    assert classify.classify_heuristic("review my PR") == "review"
    assert classify.classify_heuristic("审查代码") == "review"


def test_classify_heuristic_research():
    assert classify.classify_heuristic("research this topic") == "research"


def test_classify_heuristic_planning():
    assert classify.classify_heuristic("plan a new architecture") == "planning"


def test_classify_heuristic_writing():
    assert classify.classify_heuristic("write documentation") == "writing"


def test_classify_heuristic_automation():
    assert classify.classify_heuristic("automate the browser") == "automation"


def test_classify_heuristic_default_coding():
    # No keyword match → default to "coding"
    assert classify.classify_heuristic("hello world") == "coding"


# ---------- classify_flash ----------

def test_classify_flash_no_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert classify.classify_flash("test") is None


def test_classify_flash_network_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    def fake_urlopen(*args, **kwargs):
        raise Exception("network error")

    with patch("modules.prompt.classify.urllib.request.urlopen", side_effect=fake_urlopen):
        assert classify.classify_flash("test") is None


def test_classify_flash_invalid_json_response(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not valid json"
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        assert classify.classify_flash("test") is None


def test_classify_flash_invalid_task_type(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    response_data = {
        "choices": [{
            "message": {
                "content": '{"task_type": "invalid_type", "confidence": 0.5}'
            }
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        assert classify.classify_flash("test") is None


def test_classify_flash_valid_response(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    response_data = {
        "choices": [{
            "message": {
                "content": '{"task_type": "debugging", "confidence": 0.9, "rationale": "crash reported"}'
            }
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        assert classify.classify_flash("it crashes") == "debugging"


def test_classify_flash_strips_markdown_fences(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    fenced_content = '```json\n{"task_type": "coding", "confidence": 0.95}\n```'
    response_data = {
        "choices": [{
            "message": {"content": fenced_content}
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        assert classify.classify_flash("implement feature") == "coding"


def test_classify_flash_strips_single_line_fences(monkeypatch):
    """Regression: single-line fenced content (no newline) must not crash.
    Previously, `content.split("\n", 1)[1]` raised IndexError on single-line content."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    # Single-line fenced: ```{"task_type": "coding"}```
    fenced_content = '```{"task_type": "coding", "confidence": 0.9}```'
    response_data = {
        "choices": [{
            "message": {"content": fenced_content}
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        assert classify.classify_flash("implement feature") == "coding"


def test_classify_flash_handles_malformed_fence(monkeypatch):
    """Regression: malformed fence (no closing) should not crash."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    # Malformed: opening fence only, no closing
    fenced_content = '```{"task_type": "coding"}'
    response_data = {
        "choices": [{
            "message": {"content": fenced_content}
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        # Should parse the JSON after stripping the opening fence
        assert classify.classify_flash("implement feature") == "coding"


# ---------- classify (unified) ----------

def test_classify_heuristic_mode():
    assert classify.classify("implement a feature", mode="heuristic") == "coding"


def test_classify_flash_falls_back_to_heuristic(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    # flash returns None → falls back to heuristic
    assert classify.classify("implement a feature", mode="flash") == "coding"


def test_classify_flash_uses_flash_result(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")

    response_data = {
        "choices": [{
            "message": {"content": '{"task_type": "review", "confidence": 0.9}'}
        }]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = lambda self: mock_resp
    mock_resp.__exit__ = lambda self, *args: None

    with patch("modules.prompt.classify.urllib.request.urlopen", return_value=mock_resp):
        assert classify.classify("review my code", mode="flash") == "review"


def test_classify_default_mode_is_heuristic():
    # Default mode is heuristic
    assert classify.classify("implement a feature") == "coding"
