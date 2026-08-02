"""Tests for modules/orchestrator/action_extractor.py — v1.8 Phase C2."""
import json
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest


# ── _parse_extraction_response tests ───────────────────────────


def test_parse_empty_response():
    """Empty response should return empty list."""
    from modules.orchestrator.action_extractor import _parse_extraction_response
    assert _parse_extraction_response("") == []
    assert _parse_extraction_response("   ") == []


def test_parse_json_array():
    """Valid JSON array should parse correctly."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps([
        {"title": "Task 1", "due_at": "2026-08-01T23:59:00+08:00", "priority": "high"},
        {"title": "Task 2", "due_at": None, "priority": "low"},
    ])
    items = _parse_extraction_response(response)
    assert len(items) == 2
    assert items[0]["title"] == "Task 1"
    assert items[1]["due_at"] is None


def test_parse_items_wrapper():
    """{'items': [...]} format should parse correctly."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps({"items": [{"title": "Task 1"}]})
    items = _parse_extraction_response(response)
    assert len(items) == 1
    assert items[0]["title"] == "Task 1"


def test_parse_single_dict():
    """Single dict (not array) should be wrapped into list."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps({"title": "Single task", "priority": "medium"})
    items = _parse_extraction_response(response)
    assert len(items) == 1
    assert items[0]["title"] == "Single task"


def test_parse_markdown_fenced_json():
    """JSON wrapped in markdown code fences should parse."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = "```json\n[{\"title\": \"Task 1\"}]\n```"
    items = _parse_extraction_response(response)
    assert len(items) == 1
    assert items[0]["title"] == "Task 1"


def test_parse_invalid_json():
    """Invalid JSON should return empty list."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    assert _parse_extraction_response("not json at all") == []
    assert _parse_extraction_response("{invalid") == []


def test_parse_json_array_in_text():
    """JSON array embedded in text should be extracted."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = "Here are the items:\n[{\"title\": \"Task 1\"}]\nDone."
    items = _parse_extraction_response(response)
    assert len(items) == 1
    assert items[0]["title"] == "Task 1"


def test_parse_normalizes_priority():
    """Invalid priority should default to 'medium'."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps([{"title": "Task", "priority": "urgent"}])
    items = _parse_extraction_response(response)
    assert items[0]["priority"] == "medium"


def test_parse_valid_priorities_preserved():
    """Valid priorities should be preserved."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    for p in ["low", "medium", "high", "critical"]:
        response = json.dumps([{"title": "Task", "priority": p}])
        items = _parse_extraction_response(response)
        assert items[0]["priority"] == p


def test_parse_skips_items_without_title():
    """Items without title should be skipped."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps([
        {"title": "Valid", "priority": "high"},
        {"priority": "low"},  # No title
        {"title": "", "priority": "low"},  # Empty title
    ])
    items = _parse_extraction_response(response)
    assert len(items) == 1
    assert items[0]["title"] == "Valid"


def test_parse_truncates_long_title():
    """Titles over 200 chars should be truncated."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    long_title = "A" * 300
    response = json.dumps([{"title": long_title}])
    items = _parse_extraction_response(response)
    assert len(items[0]["title"]) == 200


# ── extract_action_items tests ─────────────────────────────────


def test_extract_empty_content():
    """Empty content should return empty list."""
    from modules.orchestrator.action_extractor import extract_action_items
    assert extract_action_items("") == []
    assert extract_action_items("   ") == []


def test_extract_no_api_key():
    """Missing API key should return error dict."""
    from modules.orchestrator.action_extractor import extract_action_items

    with patch.dict(os.environ, {}, clear=True):
        result = extract_action_items("some content")
        assert len(result) == 1
        assert "error" in result[0]


def test_extract_api_failure_returns_empty():
    """API failure should return empty list (don't block pipeline)."""
    from modules.orchestrator.action_extractor import extract_action_items

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_flash_api",
            side_effect=RuntimeError("API error")
        ):
            result = extract_action_items("some content")
            assert result == []


def test_extract_success_with_mock(temp_db, monkeypatch):
    """Successful extraction should parse and write to DB."""
    from modules.orchestrator.action_extractor import extract_action_items
    from modules.orchestrator import schedule_store

    # Patch schedule_store DB path
    temp_db_path = Path(temp_db)
    monkeypatch.setattr(schedule_store, "_DB_PATH", temp_db_path)
    schedule_store.init_db()

    # Mock API response
    mock_response = json.dumps([
        {"title": "完成作业", "due_at": "2026-08-01T23:59:00+08:00", "priority": "high"},
        {"title": "复习考试", "due_at": None, "priority": "medium"},
    ])

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_flash_api",
            return_value=mock_response
        ):
            items = extract_action_items(
                "# Weekly Report\n\n- 完成作业\n- 复习考试",
                source_ref="/reports/weekly.md"
            )

    assert len(items) == 2
    assert items[0]["title"] == "完成作业"
    assert "schedule_id" in items[0]

    # Verify written to DB
    all_schedules = schedule_store.list_schedules()
    assert len(all_schedules) == 2
    assert all_schedules[0]["source"] == "agent_extracted"
    assert all_schedules[0]["source_ref"] == "/reports/weekly.md"


def test_extract_no_write_to_db(temp_db, monkeypatch):
    """write_to_db=False should skip DB write."""
    from modules.orchestrator.action_extractor import extract_action_items
    from modules.orchestrator import schedule_store

    monkeypatch.setattr(schedule_store, "_DB_PATH", Path(temp_db))
    schedule_store.init_db()

    mock_response = json.dumps([{"title": "Task 1", "priority": "high"}])

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_flash_api",
            return_value=mock_response
        ):
            items = extract_action_items("content", write_to_db=False)

    assert len(items) == 1
    assert "schedule_id" not in items[0]  # Not written to DB

    # DB should be empty
    all_schedules = schedule_store.list_schedules()
    assert len(all_schedules) == 0


def test_extract_default_due_at(temp_db, monkeypatch):
    """Items without due_at should get default 7 days from now."""
    from modules.orchestrator.action_extractor import extract_action_items
    from modules.orchestrator import schedule_store
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(schedule_store, "_DB_PATH", Path(temp_db))
    schedule_store.init_db()

    mock_response = json.dumps([{"title": "No deadline task"}])

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_flash_api",
            return_value=mock_response
        ):
            items = extract_action_items("content")

    all_schedules = schedule_store.list_schedules()
    assert len(all_schedules) == 1

    # Due_at should be ~7 days from now
    due_at = all_schedules[0]["due_at"]
    parsed_due = datetime.fromisoformat(due_at)
    expected_min = datetime.now(timezone.utc) + timedelta(days=6, hours=23)
    expected_max = datetime.now(timezone.utc) + timedelta(days=7, minutes=1)
    assert expected_min < parsed_due < expected_max


# ── extract_from_file tests ────────────────────────────────────


def test_extract_from_file_success(tmp_path, monkeypatch):
    """extract_from_file should read file and extract."""
    from modules.orchestrator.action_extractor import extract_from_file
    from modules.orchestrator import schedule_store

    # Create test file
    test_file = tmp_path / "report.md"
    test_file.write_text("# Report\n\n- Task 1", encoding="utf-8")

    monkeypatch.setattr(schedule_store, "_DB_PATH", tmp_path / "test.db")
    schedule_store.init_db()

    mock_response = json.dumps([{"title": "Task 1", "priority": "high"}])

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_flash_api",
            return_value=mock_response
        ):
            items = extract_from_file(str(test_file))

    assert len(items) == 1
    assert items[0]["title"] == "Task 1"


def test_extract_from_file_not_found():
    """extract_from_file should return empty for non-existent file."""
    from modules.orchestrator.action_extractor import extract_from_file
    assert extract_from_file("/nonexistent/file.md") == []


# ── PII redaction tests ────────────────────────────────────────


def test_redact_pii_with_privacy_module():
    """_redact_pii should call privacy.redact_outbound when available."""
    from modules.orchestrator.action_extractor import _redact_pii

    with patch("modules.search.privacy.redact_outbound") as mock_redact:
        mock_redact.return_value = ("redacted content", [])
        result = _redact_pii("content with PII")
        assert result == "redacted content"
        mock_redact.assert_called_once_with("content with PII")


def test_redact_pii_fallback():
    """_redact_pii should return content unchanged if privacy module unavailable."""
    from modules.orchestrator.action_extractor import _redact_pii

    with patch.dict("sys.modules", {"modules.search.privacy": None}):
        result = _redact_pii("content")
        assert result == "content"


# ── Fixture ────────────────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Provide a temp DB path for schedule_store."""
    yield tmp_path / "test-schedules.db"
