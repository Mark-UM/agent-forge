"""Tests for modules.prompt.log (v1.5 P0)"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import log


@pytest.fixture
def temp_runtime(tmp_path, monkeypatch):
    """Redirect RUNTIME_DIR to a temp dir for testing."""
    monkeypatch.setattr(log, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(log, "LOG_PATH", tmp_path / "composition_log.jsonl")
    monkeypatch.setattr(log, "VERSION_PATH", tmp_path / "version.json")
    return tmp_path


# ---------- log_composition ----------

def test_log_composition_creates_file(temp_runtime):
    metadata = {"version": "1.5.0", "profile": "default"}
    log.log_composition(metadata)
    assert log.LOG_PATH.exists()


def test_log_composition_appends_entries(temp_runtime):
    log.log_composition({"version": "1.5.0", "profile": "default"})
    log.log_composition({"version": "1.5.0", "profile": "terse"})
    entries = log.LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
    assert len(entries) == 2


def test_log_composition_includes_timestamp(temp_runtime):
    log.log_composition({"version": "1.5.0"})
    entry = json.loads(log.LOG_PATH.read_text(encoding="utf-8").strip())
    assert "timestamp" in entry


def test_log_composition_preserves_metadata(temp_runtime):
    metadata = {"version": "1.5.0", "profile": "terse", "task": "coding"}
    log.log_composition(metadata)
    entry = json.loads(log.LOG_PATH.read_text(encoding="utf-8").strip())
    assert entry["version"] == "1.5.0"
    assert entry["profile"] == "terse"
    assert entry["task"] == "coding"


# ---------- write_version_snapshot ----------

def test_write_version_snapshot_creates_file(temp_runtime):
    log.write_version_snapshot(["base.md", "profiles/default.md"], "default")
    assert log.VERSION_PATH.exists()


def test_write_version_snapshot_content(temp_runtime):
    log.write_version_snapshot(["base.md"], "terse", task="coding")
    snapshot = json.loads(log.VERSION_PATH.read_text(encoding="utf-8"))
    assert snapshot["version"] == "1.5.0"
    assert snapshot["profile"] == "terse"
    assert snapshot["task"] == "coding"
    assert snapshot["sources"] == ["base.md"]
    assert "snapshot_at" in snapshot


# ---------- read_version_snapshot ----------

def test_read_version_snapshot_returns_empty_when_missing(temp_runtime):
    assert log.read_version_snapshot() == {}


def test_read_version_snapshot_returns_dict(temp_runtime):
    log.write_version_snapshot(["base.md"], "default")
    snapshot = log.read_version_snapshot()
    assert snapshot["version"] == "1.5.0"
    assert snapshot["profile"] == "default"


# ---------- read_recent ----------

def test_read_recent_returns_empty_when_missing(temp_runtime):
    assert log.read_recent() == []


def test_read_recent_returns_entries(temp_runtime):
    for i in range(5):
        log.log_composition({"version": "1.5.0", "index": i})
    entries = log.read_recent(limit=3)
    assert len(entries) == 3
    # Last 3 entries (indices 2, 3, 4)
    assert entries[-1]["index"] == 4


def test_read_recent_limit_zero_returns_empty(temp_runtime):
    """Regression: read_recent(limit=0) must return empty list, not all entries.
    Previously, `entries[-0:]` == `entries[0:]` == all entries (BUG)."""
    for i in range(5):
        log.log_composition({"version": "1.5.0", "index": i})
    assert log.read_recent(limit=0) == []


def test_read_recent_limit_negative_returns_empty(temp_runtime):
    """limit < 0 should also return empty list (defensive)."""
    for i in range(3):
        log.log_composition({"version": "1.5.0", "index": i})
    assert log.read_recent(limit=-1) == []


def test_read_recent_limit_none_returns_all(temp_runtime):
    """limit=None returns all entries (no slicing)."""
    for i in range(3):
        log.log_composition({"version": "1.5.0", "index": i})
    entries = log.read_recent(limit=None)
    assert len(entries) == 3


def test_read_recent_skips_invalid_json(temp_runtime):
    log.LOG_PATH.write_text(
        '{"valid": true}\ninvalid json line\n{"also_valid": true}\n',
        encoding="utf-8",
    )
    entries = log.read_recent()
    assert len(entries) == 2
    assert entries[0]["valid"] is True
    assert entries[1]["also_valid"] is True


def test_read_recent_skips_empty_lines(temp_runtime):
    log.LOG_PATH.write_text('{"valid": true}\n\n\n', encoding="utf-8")
    entries = log.read_recent()
    assert len(entries) == 1
