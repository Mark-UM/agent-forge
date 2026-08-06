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
    """Valid JSON array should parse correctly.

    SC5 fix: due_at=None now resolves to default 7 days from now (not None).
    """
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps([
        {"title": "Task 1", "due_at": "2026-08-01T23:59:00+08:00", "priority": "high"},
        {"title": "Task 2", "due_at": None, "priority": "low"},
    ])
    items = _parse_extraction_response(response)
    assert len(items) == 2
    assert items[0]["title"] == "Task 1"
    assert items[1]["due_at"] is not None
    assert "T" in items[1]["due_at"]


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
        {"priority": "low"},
        {"title": "", "priority": "low"},
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
    """R2-5.2: Empty content should return no_actions status."""
    from modules.orchestrator.action_extractor import extract_action_items
    result = extract_action_items("")
    assert result["status"] == "no_actions"
    assert result["items"] == []
    result = extract_action_items("   ")
    assert result["status"] == "no_actions"


def test_extract_no_api_key():
    """R2-5.2: Missing API key should return model_error status."""
    from modules.orchestrator.action_extractor import extract_action_items

    with patch.dict(os.environ, {}, clear=True):
        result = extract_action_items("some content")
        assert result["status"] == "model_error"
        assert "DEEPSEEK_API_KEY" in result["error"]


def test_extract_api_failure_returns_model_error():
    """R2-5.2: API failure should return model_error (not empty list)."""
    from modules.orchestrator.action_extractor import extract_action_items

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_api",
            side_effect=RuntimeError("API error")
        ):
            result = extract_action_items("some content")
            assert result["status"] == "model_error"
            assert "API error" in result["error"]


def test_extract_success_with_mock(temp_db, monkeypatch):
    """Successful extraction should parse and write to DB."""
    from modules.orchestrator.action_extractor import extract_action_items
    from modules.orchestrator import schedule_store

    temp_db_path = Path(temp_db)
    monkeypatch.setattr(schedule_store, "_DB_PATH", temp_db_path)
    schedule_store.init_db()

    mock_response = json.dumps({
        "actions": [
            {"title": "完成作业", "due_at": "2026-08-01T23:59:00+08:00", "priority": "high"},
            {"title": "复习考试", "due_at": None, "priority": "medium"},
        ]
    })

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_api",
            return_value=mock_response
        ):
            result = extract_action_items(
                "# Weekly Report\n\n- 完成作业\n- 复习考试",
                source_ref="/reports/weekly.md"
            )

    assert result["status"] == "ok"
    items = result["items"]
    assert len(items) == 2
    assert items[0]["title"] == "完成作业"
    assert "schedule_id" in items[0]

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

    mock_response = json.dumps({"actions": [{"title": "Task 1", "priority": "high"}]})

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_api",
            return_value=mock_response
        ):
            result = extract_action_items("content", write_to_db=False)

    items = result["items"]
    assert len(items) == 1
    assert "schedule_id" not in items[0]

    all_schedules = schedule_store.list_schedules()
    assert len(all_schedules) == 0


def test_extract_default_due_at(temp_db, monkeypatch):
    """Items without due_at should get default 7 days from now."""
    from modules.orchestrator.action_extractor import extract_action_items
    from modules.orchestrator import schedule_store
    from datetime import datetime, timezone, timedelta

    monkeypatch.setattr(schedule_store, "_DB_PATH", Path(temp_db))
    schedule_store.init_db()

    mock_response = json.dumps({"actions": [{"title": "No deadline task"}]})

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_api",
            return_value=mock_response
        ):
            result = extract_action_items("content")

    all_schedules = schedule_store.list_schedules()
    assert len(all_schedules) == 1

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

    test_file = tmp_path / "report.md"
    test_file.write_text("# Report\n\n- Task 1", encoding="utf-8")

    monkeypatch.setattr(schedule_store, "_DB_PATH", tmp_path / "test.db")
    schedule_store.init_db()

    mock_response = json.dumps({"actions": [{"title": "Task 1", "priority": "high"}]})

    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-key"}):
        with patch(
            "modules.orchestrator.action_extractor._call_api",
            return_value=mock_response
        ):
            result = extract_from_file(str(test_file))

    assert result["status"] == "ok"
    items = result["items"]
    assert len(items) == 1
    assert items[0]["title"] == "Task 1"


def test_extract_from_file_not_found():
    """R2-5.2: extract_from_file should return parse_error for non-existent file."""
    from modules.orchestrator.action_extractor import extract_from_file
    result = extract_from_file("/nonexistent/file.md")
    assert result["status"] == "parse_error"
    assert result["items"] == []


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


# ── SC4: JSON object schema tests ─────────────────────────────


def test_sc4_prompt_requires_json_object():
    """SC4: Extraction prompt must require JSON object output, not raw array."""
    from modules.orchestrator.action_extractor import _EXTRACTION_PROMPT

    assert "JSON 对象" in _EXTRACTION_PROMPT or "JSON object" in _EXTRACTION_PROMPT
    assert '"actions"' in _EXTRACTION_PROMPT
    assert "顶层必须是对象" in _EXTRACTION_PROMPT or "top-level" in _EXTRACTION_PROMPT.lower()


def test_sc4_parse_actions_object_canonical():
    """SC4: {"actions": [...]} is the canonical format and parses cleanly."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps({
        "actions": [
            {"title": "Task A", "priority": "high"},
            {"title": "Task B", "priority": "low"},
        ]
    })
    items = _parse_extraction_response(response)
    assert len(items) == 2
    assert items[0]["title"] == "Task A"
    assert items[1]["title"] == "Task B"


def test_sc4_parse_empty_actions_object():
    """SC4: {"actions": []} returns empty list without warning."""
    from modules.orchestrator.action_extractor import _parse_extraction_response
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        items = _parse_extraction_response(json.dumps({"actions": []}))
        assert items == []


def test_sc4_parse_legacy_array_emits_deprecation():
    """SC4: Raw array format emits DeprecationWarning (backward compat)."""
    from modules.orchestrator.action_extractor import _parse_extraction_response
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _parse_extraction_response(json.dumps([{"title": "Task"}]))
        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1


# ── SC5: due_at validation tests ──────────────────────────────


def test_sc5_validate_due_at_valid_iso_with_tz():
    """R2-5.1: Valid ISO 8601 with timezone passes validation and converts to UTC."""
    from modules.orchestrator.action_extractor import _validate_due_at

    utc_iso, source_tz, original, error = _validate_due_at("2026-08-01T23:59:00+08:00")
    assert error is None
    assert "+00:00" in utc_iso
    assert original == "2026-08-01T23:59:00+08:00"


def test_sc5_validate_due_at_utc():
    """R2-5.1: UTC ISO 8601 passes validation."""
    from modules.orchestrator.action_extractor import _validate_due_at

    utc_iso, source_tz, original, error = _validate_due_at("2026-08-01T15:59:00+00:00")
    assert error is None
    assert utc_iso == "2026-08-01T15:59:00+00:00"


def test_sc5_validate_due_at_none_returns_default():
    """R2-5.1: None due_at returns default 7 days from now in UTC."""
    from modules.orchestrator.action_extractor import _validate_due_at
    from datetime import datetime, timezone, timedelta

    utc_iso, source_tz, original, error = _validate_due_at(None)
    assert error is None
    parsed = datetime.fromisoformat(utc_iso)
    expected_min = datetime.now(timezone.utc) + timedelta(days=6, hours=23)
    expected_max = datetime.now(timezone.utc) + timedelta(days=7, minutes=1)
    assert expected_min < parsed < expected_max


def test_sc5_validate_due_at_empty_string_returns_default():
    """R2-5.1: Empty string due_at returns default 7 days from now."""
    from modules.orchestrator.action_extractor import _validate_due_at

    utc_iso, source_tz, original, error = _validate_due_at("")
    assert error is None
    assert utc_iso is not None
    assert "T" in utc_iso


def test_sc5_validate_due_at_naive_attaches_timezone():
    """R2-5.1: Naive datetime gets default timezone attached and converted to UTC."""
    from modules.orchestrator.action_extractor import _validate_due_at
    from datetime import datetime

    utc_iso, source_tz, original, error = _validate_due_at("2026-08-01T23:59:00")
    assert error is None
    parsed = datetime.fromisoformat(utc_iso)
    assert parsed.tzinfo is not None
    assert source_tz is not None


def test_sc5_validate_due_at_invalid_format_rejected():
    """R2-5.1: Invalid due_at format is rejected with error."""
    from modules.orchestrator.action_extractor import _validate_due_at

    utc_iso, source_tz, original, error = _validate_due_at("not-a-date")
    assert utc_iso is None
    assert error is not None
    assert "ISO 8601" in error or "not valid" in error


def test_sc5_validate_due_at_non_string_rejected():
    """R2-5.1: Non-string due_at is rejected with error."""
    from modules.orchestrator.action_extractor import _validate_due_at

    utc_iso, source_tz, original, error = _validate_due_at(12345)
    assert utc_iso is None
    assert error is not None
    assert "string" in error


def test_sc5_invalid_due_at_item_marked_with_error():
    """SC5: Items with invalid due_at are kept but flagged with validation_error."""
    from modules.orchestrator.action_extractor import _parse_extraction_response

    response = json.dumps({
        "actions": [
            {"title": "Bad task", "due_at": "not-a-date"},
            {"title": "Good task", "due_at": "2026-08-01T23:59:00+08:00"},
        ]
    })
    items = _parse_extraction_response(response)
    assert len(items) == 2
    bad_item = next(i for i in items if i["title"] == "Bad task")
    assert "validation_error" in bad_item
    assert bad_item["due_at"] is None
    good_item = next(i for i in items if i["title"] == "Good task")
    assert "validation_error" not in good_item


# ── SC6: Dispatch Guard integration tests ─────────────────────


def test_sc6_resolve_model_uses_dispatch_guard():
    """SC6: _resolve_model should call guard.resolve_model('action_extraction')."""
    from modules.orchestrator import action_extractor

    with patch("modules.dispatch.guard.resolve_model") as mock_resolve:
        mock_resolve.return_value = "deepseek-chat"
        model = action_extractor._resolve_model()
        mock_resolve.assert_called_once_with(
            "action_extraction",
            caller="action_extractor._call_api",
        )
        assert model == "deepseek-chat"


def test_sc6_resolve_model_fallback_on_import_error():
    """SC6: Falls back to deepseek-chat if dispatch guard unavailable."""
    from modules.orchestrator import action_extractor

    with patch.dict("sys.modules", {"modules.dispatch.guard": None, "modules.dispatch": None}):
        with patch("builtins.__import__", side_effect=ImportError("no guard")):
            model = action_extractor._resolve_model()
            assert model == "deepseek-chat"


def test_sc6_action_extraction_in_flash_allowlist():
    """SC6: 'action_extraction' must be in FLASH_ALLOWED_TASKS."""
    from modules.dispatch.guard import FLASH_ALLOWED_TASKS, check_task_allowed

    assert "action_extraction" in FLASH_ALLOWED_TASKS
    assert check_task_allowed("action_extraction") is True


def test_sc6_call_api_uses_resolved_model(temp_db, monkeypatch):
    """SC6: _call_api sends the supported model selected by _resolve_model()."""
    from modules.orchestrator import action_extractor
    from modules.orchestrator import schedule_store

    monkeypatch.setattr(schedule_store, "_DB_PATH", Path(temp_db))
    schedule_store.init_db()

    captured_model = {"value": None}

    def fake_resolve():
        captured_model["value"] = "deepseek-chat"
        return "deepseek-chat"

    with patch.object(action_extractor, "_resolve_model", side_effect=fake_resolve):
        with patch.object(action_extractor, "_redact_pii", return_value="content"):
            captured_payload = {"value": None}

            class FakeRequest:
                def __init__(self, url, data=None, headers=None, method=None):
                    captured_payload["value"] = json.loads(data.decode("utf-8"))

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def read(self):
                    return json.dumps({
                        "choices": [{"message": {"content": '{"actions": []}'}}]
                    }).encode("utf-8")

            with patch("urllib.request.Request", FakeRequest):
                with patch("urllib.request.urlopen", return_value=FakeResponse()):
                    action_extractor._call_api("content", "fake-key")

    assert captured_model["value"] == "deepseek-chat"
    assert captured_payload["value"]["model"] == "deepseek-chat"


# ── Fixture ────────────────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Provide a temp DB path for schedule_store."""
    yield tmp_path / "test-schedules.db"
