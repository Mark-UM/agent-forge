#!/usr/bin/env python3
"""C1: Schedule Store — SQLite schedules 表 CRUD 接口。

Schema (v1.8 Phase C1):
    CREATE TABLE schedules (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        due_at TEXT NOT NULL,
        priority TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'pending',
        source TEXT NOT NULL,
        source_ref TEXT,
        recurrence TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        notified_at TEXT,
        notify_count INTEGER DEFAULT 0
    );

Design:
    - Direct sqlite3 (stdlib), 不依赖 SQLiteClient（schedule_store 需要写入）
    - 自动迁移（CREATE TABLE IF NOT EXISTS）
    - 原子事务（commit on success, rollback on error）
    - ISO 8601 with timezone for due_at
    - uuid4 for id

Usage:
    from modules.orchestrator.schedule_store import (
        init_db, add_schedule, list_schedules,
        update_schedule_status, delete_schedule, get_schedule
    )

    init_db()  # 幂等，自动创建表
    sid = add_schedule(title="完成作业", due_at="2026-08-01T23:59:00+08:00",
                       source="user_manual")
    items = list_schedules(status="pending", days=7)
"""
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "_runtime" / "mcp-sqlite.db"

# ── Valid values ───────────────────────────────────────────────
VALID_PRIORITIES = frozenset({"low", "medium", "high", "critical"})
VALID_STATUSES = frozenset({"pending", "done", "skipped", "cancelled"})
VALID_SOURCES = frozenset({"agent_extracted", "user_manual", "recurring"})

# ── Schema ─────────────────────────────────────────────────────
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    due_at TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'pending',
    source TEXT NOT NULL,
    source_ref TEXT,
    recurrence TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notified_at TEXT,
    notify_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_schedules_due_at ON schedules(due_at);
CREATE INDEX IF NOT EXISTS idx_schedules_status ON schedules(status);
"""


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection. Creates db file if not exists."""
    os.makedirs(str(_DB_PATH.parent), exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initialize the schedules table. Idempotent."""
    with _get_conn() as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()


def _validate_priority(priority: str) -> str:
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority '{priority}': must be one of {VALID_PRIORITIES}")
    return priority


def _validate_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status '{status}': must be one of {VALID_STATUSES}")
    return status


def _validate_source(source: str) -> str:
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source '{source}': must be one of {VALID_SOURCES}")
    return source


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 with timezone."""
    return datetime.now(timezone.utc).isoformat()


def add_schedule(
    title: str,
    due_at: str,
    source: str,
    description: str = "",
    priority: str = "medium",
    source_ref: str = "",
    recurrence: str = "",
) -> str:
    """Insert a new schedule entry.

    Args:
        title: Schedule title (required)
        due_at: Due datetime in ISO 8601 with timezone (required)
        source: One of VALID_SOURCES (required)
        description: Optional description
        priority: One of VALID_PRIORITIES (default: medium)
        source_ref: Reference to source (e.g., report path)
        recurrence: SC2 fix: UNSUPPORTED for action items. Non-empty values
            are rejected. Recurring jobs should use the scheduler_jobs table
            via job_store.add_job() instead. The parameter is kept for
            backward compatibility but must be empty.

    Returns:
        str: The generated schedule ID (uuid4)

    Raises:
        ValueError: If recurrence is non-empty (SC2: unsupported for action items)
    """
    if not title or not title.strip():
        raise ValueError("title is required")
    if not due_at or not due_at.strip():
        raise ValueError("due_at is required")

    # SC2 fix: recurrence is not supported for action items (schedules table).
    # Recurring jobs should use scheduler_jobs table via job_store.add_job().
    if recurrence and recurrence.strip():
        raise ValueError(
            "recurrence is not supported for action items (SC2 fix). "
            "Use job_store.add_job() for recurring cron jobs instead."
        )

    _validate_priority(priority)
    _validate_source(source)

    init_db()  # Ensure table exists

    schedule_id = str(uuid.uuid4())
    now = _now_iso()

    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO schedules
               (id, title, description, due_at, priority, status, source,
                source_ref, recurrence, created_at, updated_at, notified_at, notify_count)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL, 0)""",
            (schedule_id, title.strip(), description, due_at, priority,
             source, source_ref, recurrence, now, now),
        )
        conn.commit()

    return schedule_id


def get_schedule(schedule_id: str) -> Optional[dict]:
    """Get a single schedule by ID.

    Returns:
        dict or None if not found
    """
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
    return dict(row) if row else None


def list_schedules(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    days: Optional[int] = None,
    source: Optional[str] = None,
    limit: int = 100,
) -> list:
    """List schedules with optional filters.

    Args:
        status: Filter by status (e.g., "pending")
        priority: Filter by priority (e.g., "high")
        days: Only show items due within N days from now
        source: Filter by source
        limit: Max results (default 100)

    Returns:
        list[dict]: Matching schedules, sorted by due_at ascending
    """
    init_db()

    if status is not None:
        _validate_status(status)
    if priority is not None:
        _validate_priority(priority)
    if source is not None:
        _validate_source(source)

    query = "SELECT * FROM schedules WHERE 1=1"
    params = []

    if status is not None:
        query += " AND status = ?"
        params.append(status)
    if priority is not None:
        query += " AND priority = ?"
        params.append(priority)
    if source is not None:
        query += " AND source = ?"
        params.append(source)
    if days is not None:
        # Calculate cutoff datetime
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        query += " AND due_at <= ?"
        params.append(cutoff)

    query += " ORDER BY due_at ASC LIMIT ?"
    params.append(limit)

    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


def update_schedule_status(schedule_id: str, status: str) -> bool:
    """Update a schedule's status.

    Returns:
        bool: True if updated, False if schedule not found
    """
    _validate_status(status)
    init_db()

    now = _now_iso()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE schedules SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, schedule_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_schedule_notified(schedule_id: str) -> bool:
    """Mark a schedule as notified (increment notify_count, set notified_at).

    Returns:
        bool: True if updated, False if schedule not found
    """
    init_db()
    now = _now_iso()
    with _get_conn() as conn:
        cursor = conn.execute(
            """UPDATE schedules
               SET notified_at = ?, notify_count = notify_count + 1, updated_at = ?
               WHERE id = ?""",
            (now, now, schedule_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_schedule(schedule_id: str) -> bool:
    """Delete a schedule by ID.

    Returns:
        bool: True if deleted, False if not found
    """
    init_db()
    with _get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM schedules WHERE id = ?", (schedule_id,)
        )
        conn.commit()
        return cursor.rowcount > 0


def get_pending_reminders(now: Optional[str] = None) -> list:
    """Get all pending schedules that are due (for reminder daemon).

    Args:
        now: Current time in ISO 8601 (default: utcnow)

    Returns:
        list[dict]: Pending schedules where due_at <= now
    """
    init_db()
    if now is None:
        now = _now_iso()

    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM schedules
               WHERE status = 'pending' AND due_at <= ?
               ORDER BY due_at ASC""",
            (now,),
        ).fetchall()

    return [dict(r) for r in rows]


def get_db_path() -> str:
    """Return the database file path."""
    return str(_DB_PATH)


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m modules.orchestrator.schedule_store [init|list|stats]")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "init":
        init_db()
        print(f"Database initialized at {get_db_path()}")

    elif cmd == "list":
        items = list_schedules()
        print(json.dumps(items, indent=2, ensure_ascii=False))

    elif cmd == "stats":
        init_db()
        with _get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM schedules WHERE status = 'pending'"
            ).fetchone()[0]
            done = conn.execute(
                "SELECT COUNT(*) FROM schedules WHERE status = 'done'"
            ).fetchone()[0]
        print(json.dumps({"total": total, "pending": pending, "done": done}, indent=2))

    else:
        print(f"Unknown command: {cmd}")
