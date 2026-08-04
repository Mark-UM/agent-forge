#!/usr/bin/env python3
"""SC1 fix: Unified SQLite job store for the scheduler daemon.

Replaces the dual state source (schedule_store.py SQLite + daemon.py jobs.json)
with a single SQLite authority. The `schedules` table (action items) and
`scheduler_jobs` table (cron jobs) both live in `_runtime/mcp-sqlite.db`.

Schema (SC1):
    CREATE TABLE IF NOT EXISTS scheduler_jobs (
        job_id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        payload TEXT,                  -- JSON-encoded params
        trigger_type TEXT NOT NULL,    -- 'cron' | 'date' | 'interval'
        cron_expr TEXT,                -- for cron trigger
        run_at TEXT,                   -- for date trigger (UTC ISO)
        timezone TEXT,                 -- original timezone (IANA name)
        recurrence TEXT,               -- cron expr or empty
        status TEXT DEFAULT 'active',  -- active | paused | error | deleted
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_run_at TEXT,
        next_run_at TEXT,
        last_error TEXT,
        run_count INTEGER DEFAULT 0,
        max_instances INTEGER DEFAULT 1,
        misfire_grace_time INTEGER DEFAULT 60,
        coalesce INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS scheduler_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

Migration:
    - One-time, idempotent migration from `_runtime/scheduler/jobs.json`.
    - Backs up the old file to `jobs.json.migrated-{timestamp}`.
    - Records migration version in `scheduler_meta`.
    - Does not re-migrate if already done (checks `scheduler_meta.jobs_migration_version`).

Design:
    - Direct sqlite3 (stdlib), same DB file as schedule_store.
    - Atomic transactions (commit on success, rollback on error).
    - All times stored as UTC ISO 8601.
    - job_id generated as uuid4 hex prefix (preserves legacy format).

Usage:
    from modules.scheduler.job_store import (
        init_job_store, add_job, list_jobs, get_job,
        update_job_status, delete_job, migrate_from_json
    )

    init_job_store()  # idempotent
    job_id = add_job(job_type="file_reindex", cron_expr="0 9 * * *",
                     params={"root": "/tmp"})
    jobs = list_jobs(status="active")
"""
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "_runtime" / "mcp-sqlite.db"
_JOBS_JSON_PATH = _PROJECT_ROOT / "_runtime" / "scheduler" / "jobs.json"

# ── Migration version ──────────────────────────────────────────
# Bump this when the migration logic changes. Idempotent re-runs with the
# same version are no-ops; a higher version triggers re-migration.
_MIGRATION_VERSION = "1"

# ── Valid values ───────────────────────────────────────────────
# R2-4.2: Only 'cron' is currently supported. 'date' and 'interval' are
# recognized as valid trigger types (so the table column accepts them) but
# are NOT in SUPPORTED_TRIGGER_TYPES. Attempting to add a job with an
# unsupported trigger raises UnsupportedTriggerError.
VALID_TRIGGER_TYPES = frozenset({"cron", "date", "interval"})
SUPPORTED_TRIGGER_TYPES = frozenset({"cron"})
VALID_JOB_STATUSES = frozenset({"active", "paused", "error", "deleted"})
VALID_JOB_TYPES = frozenset({
    "file_reindex", "report_collect", "memory_review",
    "action_extract", "custom",
})


class UnsupportedTriggerError(ValueError):
    """Raised when a trigger type is valid but not yet wired into APScheduler.

    R2-4.2: Only 'cron' is supported. 'date' and 'interval' triggers raise
    this until APScheduler wiring lands for them.
    """

# ── Schema ─────────────────────────────────────────────────────
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scheduler_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    payload TEXT,
    trigger_type TEXT NOT NULL,
    cron_expr TEXT,
    run_at TEXT,
    timezone TEXT,
    recurrence TEXT,
    status TEXT DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT,
    next_run_at TEXT,
    last_error TEXT,
    run_count INTEGER DEFAULT 0,
    max_instances INTEGER DEFAULT 1,
    misfire_grace_time INTEGER DEFAULT 60,
    coalesce INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_status ON scheduler_jobs(status);
CREATE INDEX IF NOT EXISTS idx_scheduler_jobs_next_run ON scheduler_jobs(next_run_at);

CREATE TABLE IF NOT EXISTS scheduler_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- R2-4.1: job_runs table — one row per execution attempt.
-- Separated from scheduler_jobs (the schedule config) per the Round 2
-- scheduler domain contract. Created BEFORE the job runs, marked RUNNING,
-- then SUCCEEDED / FAILED / TIMED_OUT after.
CREATE TABLE IF NOT EXISTS job_runs (
    run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued|running|succeeded|failed|timed_out|abandoned
    triggered_by TEXT NOT NULL DEFAULT 'cron', -- cron|manual|test
    started_at TEXT,
    ended_at TEXT,
    duration_ms REAL DEFAULT 0.0,
    error TEXT,
    run_count_at_start INTEGER DEFAULT 0,
    next_run_at_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES scheduler_jobs(job_id)
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job_id ON job_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_job_runs_status ON job_runs(status);
CREATE INDEX IF NOT EXISTS idx_job_runs_created_at ON job_runs(created_at);

-- R2-4.6: quarantined_jobs table — legacy jobs that could not be migrated
-- cleanly. NEVER auto-converted to an invalid Custom Job. Stored for
-- manual review.
CREATE TABLE IF NOT EXISTS quarantined_jobs (
    id TEXT PRIMARY KEY,
    raw_legacy TEXT NOT NULL,               -- JSON-encoded legacy job dict
    reason TEXT NOT NULL,                   -- why migration failed
    migrated_at TEXT NOT NULL
);
"""


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection. Creates db file if not exists."""
    os.makedirs(str(_DB_PATH.parent), exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_job_store() -> None:
    """Initialize the scheduler_jobs and scheduler_meta tables. Idempotent."""
    with _get_conn() as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 with timezone."""
    return datetime.now(timezone.utc).isoformat()


def _validate_job_type(job_type: str) -> str:
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(
            f"invalid job_type '{job_type}': must be one of {sorted(VALID_JOB_TYPES)}"
        )
    return job_type


def _validate_trigger_type(trigger_type: str) -> str:
    if trigger_type not in VALID_TRIGGER_TYPES:
        raise ValueError(
            f"invalid trigger_type '{trigger_type}': must be one of {sorted(VALID_TRIGGER_TYPES)}"
        )
    return trigger_type


def _validate_job_status(status: str) -> str:
    if status not in VALID_JOB_STATUSES:
        raise ValueError(
            f"invalid status '{status}': must be one of {sorted(VALID_JOB_STATUSES)}"
        )
    return status


def add_job(
    job_type: str,
    cron_expr: str = "",
    params: Optional[dict] = None,
    trigger_type: str = "cron",
    run_at: Optional[str] = None,
    timezone_str: Optional[str] = None,
    job_id: Optional[str] = None,
    status: str = "active",
    max_instances: int = 1,
    misfire_grace_time: int = 60,
    coalesce: bool = True,
) -> str:
    """Insert a new scheduler job.

    Args:
        job_type: One of VALID_JOB_TYPES (required)
        cron_expr: Cron expression for recurring jobs (5-field unix cron)
        params: Job parameters dict (will be JSON-encoded as payload)
        trigger_type: 'cron' | 'date' | 'interval' (default: cron)
        run_at: For 'date' trigger, UTC ISO 8601 datetime
        timezone_str: Original timezone (IANA name, e.g., "Asia/Shanghai")
        job_id: Optional job ID (auto-generated if not provided)
        status: Initial status (default: active)
        max_instances: Max concurrent instances (default: 1)
        misfire_grace_time: Grace period in seconds (default: 60)
        coalesce: Coalesce misfired instances (default: True)

    Returns:
        str: The job ID
    """
    _validate_job_type(job_type)
    _validate_trigger_type(trigger_type)
    _validate_job_status(status)

    # R2-4.2: Reject unsupported triggers (date / interval) at the store layer.
    # Only 'cron' is wired into APScheduler in Round 2.
    if trigger_type not in SUPPORTED_TRIGGER_TYPES:
        raise UnsupportedTriggerError(
            f"trigger_type '{trigger_type}' is not supported. "
            f"Supported: {sorted(SUPPORTED_TRIGGER_TYPES)}. "
            f"'date' and 'interval' will be enabled after APScheduler wiring."
        )

    if trigger_type == "cron" and not cron_expr:
        raise ValueError("cron_expr is required for trigger_type='cron'")

    init_job_store()

    if job_id is None:
        job_id = f"job_{uuid.uuid4().hex[:12]}"

    now = _now_iso()
    payload_json = json.dumps(params, ensure_ascii=False) if params else "{}"
    recurrence = cron_expr if trigger_type == "cron" else ""

    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO scheduler_jobs
               (job_id, job_type, payload, trigger_type, cron_expr, run_at,
                timezone, recurrence, status, created_at, updated_at,
                last_run_at, next_run_at, last_error, run_count,
                max_instances, misfire_grace_time, coalesce)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, ?, ?, ?)""",
            (
                job_id, job_type, payload_json, trigger_type, cron_expr, run_at,
                timezone_str, recurrence, status, now, now,
                max_instances, misfire_grace_time, 1 if coalesce else 0,
            ),
        )
        conn.commit()

    return job_id


def get_job(job_id: str) -> Optional[dict]:
    """Get a single job by ID.

    Returns:
        dict (with payload parsed) or None if not found.
    """
    init_job_store()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduler_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()

    if row is None:
        return None

    result = dict(row)
    # Parse payload JSON
    try:
        result["payload"] = json.loads(result.get("payload") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["payload"] = {}
    result["coalesce"] = bool(result.get("coalesce", 1))
    return result


def list_jobs(status: Optional[str] = None, limit: int = 100) -> list:
    """List scheduler jobs with optional status filter.

    Args:
        status: Filter by status (e.g., "active")
        limit: Max results (default 100)

    Returns:
        list[dict]: Matching jobs, sorted by created_at ascending.
    """
    init_job_store()

    if status is not None:
        _validate_job_status(status)

    query = "SELECT * FROM scheduler_jobs WHERE status != 'deleted'"
    params = []

    if status is not None:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at ASC LIMIT ?"
    params.append(limit)

    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    results = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["payload"] = {}
        item["coalesce"] = bool(item.get("coalesce", 1))
        results.append(item)

    return results


def update_job_status(job_id: str, status: str) -> bool:
    """Update a job's status.

    Returns:
        bool: True if updated, False if job not found.
    """
    _validate_job_status(status)
    init_job_store()

    now = _now_iso()
    with _get_conn() as conn:
        cursor = conn.execute(
            "UPDATE scheduler_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, now, job_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_job_run_info(
    job_id: str,
    last_run_at: Optional[str] = None,
    next_run_at: Optional[str] = None,
    last_error: Optional[str] = None,
    increment_run_count: bool = False,
) -> bool:
    """Update job run metadata after execution.

    Args:
        job_id: Job ID
        last_run_at: When the job last ran (UTC ISO)
        next_run_at: Next scheduled run (UTC ISO)
        last_error: Error message if failed (None to clear)
        increment_run_count: If True, increment run_count by 1

    Returns:
        bool: True if updated, False if not found.
    """
    init_job_store()
    now = _now_iso()

    sets = ["updated_at = ?"]
    params = [now]

    if last_run_at is not None:
        sets.append("last_run_at = ?")
        params.append(last_run_at)
    if next_run_at is not None:
        sets.append("next_run_at = ?")
        params.append(next_run_at)
    if last_error is not None:
        sets.append("last_error = ?")
        params.append(last_error)
    if increment_run_count:
        sets.append("run_count = run_count + 1")

    params.append(job_id)

    with _get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE scheduler_jobs SET {', '.join(sets)} WHERE job_id = ?",
            params,
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_job(job_id: str) -> bool:
    """Soft-delete a job (sets status='deleted').

    Returns:
        bool: True if deleted, False if not found.
    """
    return update_job_status(job_id, "deleted")


def hard_delete_job(job_id: str) -> bool:
    """Hard-delete a job row from the database.

    Returns:
        bool: True if deleted, False if not found.
    """
    init_job_store()
    with _get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM scheduler_jobs WHERE job_id = ?", (job_id,)
        )
        conn.commit()
        return cursor.rowcount > 0


# ── Job Runs (R2-4.1 / R2-4.4) ─────────────────────────────────

# Valid statuses for job_runs rows. Mirrors modules.scheduler.contracts.JobRunStatus.
VALID_RUN_STATUSES = frozenset({
    "queued", "running", "succeeded", "failed", "timed_out", "abandoned",
})
VALID_TRIGGERED_BY = frozenset({"cron", "manual", "test"})


def create_job_run(
    job_id: str,
    triggered_by: str = "cron",
    run_id: Optional[str] = None,
) -> str:
    """Create a job_runs row BEFORE the job executes.

    R2-4.4: The execution wrapper calls this to create a JobRun row marked
    QUEUED, then transitions it to RUNNING before execution and
    SUCCEEDED / FAILED after.

    Args:
        job_id: The scheduler job that is about to run.
        triggered_by: 'cron' | 'manual' | 'test'.
        run_id: Optional run ID (auto-generated if not provided).

    Returns:
        str: The run_id.
    """
    if triggered_by not in VALID_TRIGGERED_BY:
        raise ValueError(
            f"invalid triggered_by '{triggered_by}': must be one of "
            f"{sorted(VALID_TRIGGERED_BY)}"
        )
    init_job_store()
    if run_id is None:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO job_runs
               (run_id, job_id, status, triggered_by, started_at, ended_at,
                duration_ms, error, run_count_at_start, next_run_at_after,
                created_at, updated_at)
               VALUES (?, ?, 'queued', ?, NULL, NULL, 0.0, NULL, 0, NULL, ?, ?)""",
            (run_id, job_id, triggered_by, now, now),
        )
        conn.commit()
    return run_id


def update_job_run(
    run_id: str,
    status: Optional[str] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
    run_count_at_start: Optional[int] = None,
    next_run_at_after: Optional[str] = None,
) -> bool:
    """Update a job_runs row.

    Only provided fields are updated. Returns True if the row was found.
    """
    if status is not None and status not in VALID_RUN_STATUSES:
        raise ValueError(
            f"invalid run status '{status}': must be one of "
            f"{sorted(VALID_RUN_STATUSES)}"
        )
    init_job_store()
    now = _now_iso()
    sets = ["updated_at = ?"]
    params: list = [now]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if started_at is not None:
        sets.append("started_at = ?")
        params.append(started_at)
    if ended_at is not None:
        sets.append("ended_at = ?")
        params.append(ended_at)
    if duration_ms is not None:
        sets.append("duration_ms = ?")
        params.append(duration_ms)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if run_count_at_start is not None:
        sets.append("run_count_at_start = ?")
        params.append(run_count_at_start)
    if next_run_at_after is not None:
        sets.append("next_run_at_after = ?")
        params.append(next_run_at_after)
    params.append(run_id)
    with _get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE job_runs SET {', '.join(sets)} WHERE run_id = ?",
            params,
        )
        conn.commit()
        return cursor.rowcount > 0


def get_job_run(run_id: str) -> Optional[dict]:
    """Get a single job run by ID."""
    init_job_store()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM job_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return dict(row) if row is not None else None


def list_job_runs(
    job_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list:
    """List job runs with optional filters."""
    init_job_store()
    query = "SELECT * FROM job_runs"
    params: list = []
    clauses: list = []
    if job_id is not None:
        clauses.append("job_id = ?")
        params.append(job_id)
    if status is not None:
        if status not in VALID_RUN_STATUSES:
            raise ValueError(
                f"invalid run status '{status}': must be one of "
                f"{sorted(VALID_RUN_STATUSES)}"
            )
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Quarantine (R2-4.6) ────────────────────────────────────────


def add_quarantined_job(
    job_id: str,
    raw_legacy: dict,
    reason: str,
) -> str:
    """Add a legacy job to the quarantine table for manual review.

    R2-4.6: Unmappable legacy jobs must NOT be auto-converted to invalid
    Custom Jobs. They land here instead.
    """
    init_job_store()
    now = _now_iso()
    raw_json = json.dumps(raw_legacy, ensure_ascii=False)
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO quarantined_jobs (id, raw_legacy, reason, migrated_at)
               VALUES (?, ?, ?, ?)""",
            (job_id, raw_json, reason, now),
        )
        conn.commit()
    return job_id


def list_quarantined_jobs() -> list:
    """List all quarantined jobs."""
    init_job_store()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM quarantined_jobs ORDER BY migrated_at ASC"
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["raw_legacy"] = json.loads(item.get("raw_legacy") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["raw_legacy"] = {}
        results.append(item)
    return results


# ── Migration from jobs.json ───────────────────────────────────


def _get_meta(key: str) -> Optional[str]:
    """Read a value from scheduler_meta."""
    init_job_store()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM scheduler_meta WHERE key = ?", (key,)
        ).fetchone()
    return row[0] if row else None


def _set_meta(key: str, value: str) -> None:
    """Write a value to scheduler_meta (upsert)."""
    now = _now_iso()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO scheduler_meta (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, value, now),
        )
        conn.commit()


def get_migration_version() -> Optional[str]:
    """Return the recorded migration version, or None if not migrated yet."""
    return _get_meta("jobs_migration_version")


def migrate_from_json(json_path: Optional[str] = None, backup: bool = True) -> dict:
    """One-time idempotent migration from jobs.json to SQLite.

    Args:
        json_path: Path to jobs.json (default: _JOBS_JSON_PATH)
        backup: If True, copy jobs.json to jobs.json.migrated-{timestamp}

    Returns:
        dict: Migration report with keys:
            - migrated_count: number of jobs migrated
            - skipped_count: number of jobs already present (idempotent)
            - quarantined_count: number of jobs sent to quarantine
            - backed_up: bool
            - backup_path: str or None
            - migration_version: str
            - already_migrated: bool (True if this version already ran)

    Behavior (R2-4.6 fixes):
        - If migration_version matches _MIGRATION_VERSION, returns early
          with already_migrated=True (idempotent).
        - Corrupt / unparseable jobs.json does NOT record the migration
          version (allows future retries). The error is recorded in
          scheduler_meta.jobs_migration_error.
        - Unmappable job_type values are quarantined (NOT auto-converted
          to 'custom'). Only job_types in VALID_JOB_TYPES are migrated.
        - After successful migration, backs up the JSON file (copy) and
          records the migration version.
    """
    if json_path is None:
        json_path = str(_JOBS_JSON_PATH)

    init_job_store()

    # Check if already migrated with this version
    existing_version = get_migration_version()
    report: dict = {
        "migrated_count": 0,
        "skipped_count": 0,
        "quarantined_count": 0,
        "backed_up": False,
        "backup_path": None,
        "migration_version": _MIGRATION_VERSION,
        "already_migrated": False,
    }

    if existing_version == _MIGRATION_VERSION:
        report["already_migrated"] = True
        return report

    # Load jobs.json
    path = Path(json_path)
    if not path.exists():
        # No jobs.json to migrate — record version and return (nothing to do)
        _set_meta("jobs_migration_version", _MIGRATION_VERSION)
        report["already_migrated"] = False
        return report

    try:
        raw = path.read_text(encoding="utf-8")
        jobs_data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        # R2-4.6: Corrupt jobs.json — do NOT record version (allow retries).
        # Record the error so it can be diagnosed.
        _set_meta("jobs_migration_error", str(exc))
        report["already_migrated"] = False
        report["error"] = str(exc)
        return report

    if not isinstance(jobs_data, dict):
        # Invalid structure — do NOT record version (allow retries)
        _set_meta("jobs_migration_error", "jobs.json root is not a dict")
        report["already_migrated"] = False
        report["error"] = "jobs.json root is not a dict"
        return report

    # Migrate each job
    now = _now_iso()
    for job_id, job_data in jobs_data.items():
        if not isinstance(job_data, dict):
            continue

        # Check if job_id already exists (idempotent)
        existing = get_job(job_id)
        if existing is not None:
            report["skipped_count"] += 1
            continue

        job_type = job_data.get("job_type", "custom")
        cron_expr = job_data.get("cron_expr", "")
        params = job_data.get("params", {})
        status = job_data.get("status", "active")
        created_at = job_data.get("created_at", now)

        # Map legacy status values
        if status not in VALID_JOB_STATUSES:
            status = "active"

        # R2-4.6: Unmappable job_type → quarantine, NOT auto-convert to custom.
        if job_type not in VALID_JOB_TYPES:
            add_quarantined_job(
                job_id,
                raw_legacy=job_data,
                reason=f"unmappable job_type '{job_type}'",
            )
            report["quarantined_count"] += 1
            continue

        # cron trigger requires cron_expr
        if not cron_expr:
            add_quarantined_job(
                job_id,
                raw_legacy=job_data,
                reason="cron trigger missing cron_expr",
            )
            report["quarantined_count"] += 1
            continue

        # Insert directly (bypass add_job to preserve job_id and created_at)
        payload_json = json.dumps(params, ensure_ascii=False) if params else "{}"
        recurrence = cron_expr if cron_expr else ""

        with _get_conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO scheduler_jobs
                       (job_id, job_type, payload, trigger_type, cron_expr, run_at,
                        timezone, recurrence, status, created_at, updated_at,
                        last_run_at, next_run_at, last_error, run_count,
                        max_instances, misfire_grace_time, coalesce)
                       VALUES (?, ?, ?, 'cron', ?, NULL, NULL, ?, ?, ?, ?, NULL, NULL, NULL, 0, 1, 60, 1)""",
                    (job_id, job_type, payload_json, cron_expr, recurrence,
                     status, created_at, created_at),
                )
                conn.commit()
                report["migrated_count"] += 1
            except sqlite3.IntegrityError:
                # job_id already exists (race condition or duplicate)
                report["skipped_count"] += 1

    # Back up the JSON file
    if backup and report["migrated_count"] > 0:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.migrated-{timestamp}")
        try:
            shutil.copy2(str(path), str(backup_path))
            report["backed_up"] = True
            report["backup_path"] = str(backup_path)
        except OSError:
            # Backup failure is non-fatal
            report["backed_up"] = False

    # Record migration version
    _set_meta("jobs_migration_version", _MIGRATION_VERSION)

    return report


def get_db_path() -> str:
    """Return the database file path (same as schedule_store)."""
    return str(_DB_PATH)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m modules.scheduler.job_store [init|migrate|list|stats]")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "init":
        init_job_store()
        print(f"Job store initialized at {get_db_path()}")

    elif cmd == "migrate":
        report = migrate_from_json()
        print(json.dumps(report, indent=2, ensure_ascii=False))

    elif cmd == "list":
        jobs = list_jobs()
        print(json.dumps(jobs, indent=2, ensure_ascii=False))

    elif cmd == "stats":
        init_job_store()
        with _get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM scheduler_jobs").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM scheduler_jobs WHERE status = 'active'"
            ).fetchone()[0]
        version = get_migration_version()
        print(json.dumps({
            "total": total,
            "active": active,
            "migration_version": version,
        }, indent=2))

    else:
        print(f"Unknown command: {cmd}")
