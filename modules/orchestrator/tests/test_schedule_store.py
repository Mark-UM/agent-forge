"""Tests for modules/orchestrator/schedule_store.py — v1.8 Phase C1."""
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Patch _DB_PATH to a temporary file."""
    from modules.orchestrator import schedule_store

    temp_db_path = tmp_path / "test-schedules.db"
    monkeypatch.setattr(schedule_store, "_DB_PATH", temp_db_path)
    schedule_store.init_db()
    yield temp_db_path


@pytest.fixture
def sample_due_at():
    """Provide a due_at 7 days in the future."""
    return (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()


# ── init_db tests ──────────────────────────────────────────────


def test_init_db_creates_table(temp_db):
    """init_db should create the schedules table."""
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'"
    ).fetchall()
    conn.close()
    assert len(tables) == 1


def test_init_db_idempotent(temp_db):
    """init_db should be idempotent (calling twice should not error)."""
    from modules.orchestrator.schedule_store import init_db
    init_db()  # Should not raise
    init_db()  # Second call should also not raise


def test_init_db_creates_indexes(temp_db):
    """init_db should create indexes on due_at and status."""
    import sqlite3
    conn = sqlite3.connect(str(temp_db))
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_schedules%'"
    ).fetchall()
    conn.close()
    index_names = [r[0] for r in indexes]
    assert "idx_schedules_due_at" in index_names
    assert "idx_schedules_status" in index_names


# ── add_schedule tests ─────────────────────────────────────────


def test_add_schedule_basic(temp_db, sample_due_at):
    """add_schedule should insert a row and return the ID."""
    from modules.orchestrator.schedule_store import add_schedule, get_schedule

    sid = add_schedule(
        title="完成作业",
        due_at=sample_due_at,
        source="user_manual",
    )
    assert sid  # Non-empty string

    item = get_schedule(sid)
    assert item is not None
    assert item["title"] == "完成作业"
    assert item["status"] == "pending"
    assert item["priority"] == "medium"
    assert item["source"] == "user_manual"


def test_add_schedule_with_all_fields(temp_db, sample_due_at):
    """add_schedule should accept all optional fields."""
    from modules.orchestrator.schedule_store import add_schedule, get_schedule

    sid = add_schedule(
        title="重要会议",
        due_at=sample_due_at,
        source="agent_extracted",
        description="项目评审会议",
        priority="high",
        source_ref="/reports/weekly.md",
        recurrence="0 9 * * 1",
    )
    item = get_schedule(sid)
    assert item["description"] == "项目评审会议"
    assert item["priority"] == "high"
    assert item["source_ref"] == "/reports/weekly.md"
    assert item["recurrence"] == "0 9 * * 1"


def test_add_schedule_invalid_priority(temp_db, sample_due_at):
    """add_schedule should reject invalid priority."""
    from modules.orchestrator.schedule_store import add_schedule

    with pytest.raises(ValueError, match="priority"):
        add_schedule(title="test", due_at=sample_due_at, source="user_manual", priority="urgent")


def test_add_schedule_invalid_source(temp_db, sample_due_at):
    """add_schedule should reject invalid source."""
    from modules.orchestrator.schedule_store import add_schedule

    with pytest.raises(ValueError, match="source"):
        add_schedule(title="test", due_at=sample_due_at, source="invalid_source")


def test_add_schedule_empty_title(temp_db, sample_due_at):
    """add_schedule should reject empty title."""
    from modules.orchestrator.schedule_store import add_schedule

    with pytest.raises(ValueError, match="title"):
        add_schedule(title="", due_at=sample_due_at, source="user_manual")


def test_add_schedule_empty_due_at(temp_db):
    """add_schedule should reject empty due_at."""
    from modules.orchestrator.schedule_store import add_schedule

    with pytest.raises(ValueError, match="due_at"):
        add_schedule(title="test", due_at="", source="user_manual")


def test_add_schedule_generates_uuid(temp_db, sample_due_at):
    """add_schedule should generate a unique UUID for each entry."""
    from modules.orchestrator.schedule_store import add_schedule
    import uuid

    sid1 = add_schedule(title="task1", due_at=sample_due_at, source="user_manual")
    sid2 = add_schedule(title="task2", due_at=sample_due_at, source="user_manual")

    # Should be valid UUIDs
    uuid.UUID(sid1)
    uuid.UUID(sid2)
    assert sid1 != sid2


# ── get_schedule tests ─────────────────────────────────────────


def test_get_schedule_not_found(temp_db):
    """get_schedule should return None for non-existent ID."""
    from modules.orchestrator.schedule_store import get_schedule

    result = get_schedule("nonexistent-id")
    assert result is None


# ── list_schedules tests ───────────────────────────────────────


def test_list_schedules_empty(temp_db):
    """list_schedules should return empty list when no schedules."""
    from modules.orchestrator.schedule_store import list_schedules

    assert list_schedules() == []


def test_list_schedules_returns_all(temp_db, sample_due_at):
    """list_schedules should return all schedules sorted by due_at."""
    from modules.orchestrator.schedule_store import add_schedule, list_schedules

    add_schedule(title="task1", due_at=sample_due_at, source="user_manual")
    add_schedule(title="task2", due_at=sample_due_at, source="user_manual")

    items = list_schedules()
    assert len(items) == 2


def test_list_schedules_filter_by_status(temp_db, sample_due_at):
    """list_schedules should filter by status."""
    from modules.orchestrator.schedule_store import add_schedule, list_schedules, update_schedule_status

    sid1 = add_schedule(title="task1", due_at=sample_due_at, source="user_manual")
    add_schedule(title="task2", due_at=sample_due_at, source="user_manual")
    update_schedule_status(sid1, "done")

    pending = list_schedules(status="pending")
    done = list_schedules(status="done")
    assert len(pending) == 1
    assert len(done) == 1
    assert pending[0]["title"] == "task2"


def test_list_schedules_filter_by_priority(temp_db, sample_due_at):
    """list_schedules should filter by priority."""
    from modules.orchestrator.schedule_store import add_schedule, list_schedules

    add_schedule(title="low task", due_at=sample_due_at, source="user_manual", priority="low")
    add_schedule(title="high task", due_at=sample_due_at, source="user_manual", priority="high")

    high = list_schedules(priority="high")
    assert len(high) == 1
    assert high[0]["title"] == "high task"


def test_list_schedules_filter_by_source(temp_db, sample_due_at):
    """list_schedules should filter by source."""
    from modules.orchestrator.schedule_store import add_schedule, list_schedules

    add_schedule(title="manual", due_at=sample_due_at, source="user_manual")
    add_schedule(title="extracted", due_at=sample_due_at, source="agent_extracted")

    extracted = list_schedules(source="agent_extracted")
    assert len(extracted) == 1
    assert extracted[0]["title"] == "extracted"


def test_list_schedules_filter_by_days(temp_db):
    """list_schedules should filter by days (due within N days)."""
    from modules.orchestrator.schedule_store import add_schedule, list_schedules

    now = datetime.now(timezone.utc)
    soon = (now + timedelta(days=2)).isoformat()
    far = (now + timedelta(days=30)).isoformat()

    add_schedule(title="soon task", due_at=soon, source="user_manual")
    add_schedule(title="far task", due_at=far, source="user_manual")

    within_week = list_schedules(days=7)
    assert len(within_week) == 1
    assert within_week[0]["title"] == "soon task"


# ── update_schedule_status tests ───────────────────────────────


def test_update_schedule_status_success(temp_db, sample_due_at):
    """update_schedule_status should update status."""
    from modules.orchestrator.schedule_store import add_schedule, update_schedule_status, get_schedule

    sid = add_schedule(title="test", due_at=sample_due_at, source="user_manual")
    result = update_schedule_status(sid, "done")
    assert result is True

    item = get_schedule(sid)
    assert item["status"] == "done"


def test_update_schedule_status_not_found(temp_db):
    """update_schedule_status should return False for non-existent ID."""
    from modules.orchestrator.schedule_store import update_schedule_status

    result = update_schedule_status("nonexistent", "done")
    assert result is False


def test_update_schedule_status_invalid(temp_db, sample_due_at):
    """update_schedule_status should reject invalid status."""
    from modules.orchestrator.schedule_store import add_schedule, update_schedule_status

    sid = add_schedule(title="test", due_at=sample_due_at, source="user_manual")
    with pytest.raises(ValueError, match="status"):
        update_schedule_status(sid, "invalid_status")


# ── update_schedule_notified tests ─────────────────────────────


def test_update_schedule_notified(temp_db, sample_due_at):
    """update_schedule_notified should increment notify_count."""
    from modules.orchestrator.schedule_store import add_schedule, update_schedule_notified, get_schedule

    sid = add_schedule(title="test", due_at=sample_due_at, source="user_manual")
    update_schedule_notified(sid)
    update_schedule_notified(sid)

    item = get_schedule(sid)
    assert item["notify_count"] == 2
    assert item["notified_at"] is not None


# ── delete_schedule tests ──────────────────────────────────────


def test_delete_schedule_success(temp_db, sample_due_at):
    """delete_schedule should remove the schedule."""
    from modules.orchestrator.schedule_store import add_schedule, delete_schedule, get_schedule

    sid = add_schedule(title="test", due_at=sample_due_at, source="user_manual")
    result = delete_schedule(sid)
    assert result is True
    assert get_schedule(sid) is None


def test_delete_schedule_not_found(temp_db):
    """delete_schedule should return False for non-existent ID."""
    from modules.orchestrator.schedule_store import delete_schedule

    result = delete_schedule("nonexistent")
    assert result is False


# ── get_pending_reminders tests ────────────────────────────────


def test_get_pending_reminders_returns_due_items(temp_db):
    """get_pending_reminders should return items where due_at <= now."""
    from modules.orchestrator.schedule_store import add_schedule, get_pending_reminders

    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=7)).isoformat()

    add_schedule(title="past due", due_at=past, source="user_manual")
    add_schedule(title="future", due_at=future, source="user_manual")

    due = get_pending_reminders(now=now.isoformat())
    assert len(due) == 1
    assert due[0]["title"] == "past due"


def test_get_pending_reminders_excludes_done(temp_db):
    """get_pending_reminders should exclude done items."""
    from modules.orchestrator.schedule_store import add_schedule, get_pending_reminders, update_schedule_status

    now = datetime.now(timezone.utc)
    past = (now - timedelta(days=1)).isoformat()

    sid = add_schedule(title="done task", due_at=past, source="user_manual")
    update_schedule_status(sid, "done")

    due = get_pending_reminders(now=now.isoformat())
    assert len(due) == 0
