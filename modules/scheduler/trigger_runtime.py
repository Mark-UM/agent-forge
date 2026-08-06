"""Production trigger runtime for cron, date, and interval Scheduler jobs.

The historical ``job_store.add_job`` remains unchanged for compatibility.  The
secure production daemon uses this module, which owns:

* the additive ``interval_seconds`` schema migration;
* timezone resolution and aware ``run_at`` normalization;
* persisted cron/date/interval job creation;
* APScheduler trigger construction and restoration;
* terminal status updates for one-shot date jobs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from modules.scheduler import job_store

SUPPORTED_TRIGGER_TYPES = frozenset({"cron", "date", "interval"})
VALID_JOB_STATUSES = frozenset(
    {"active", "paused", "completed", "error", "deleted"}
)
MIN_INTERVAL_SECONDS = 1
MAX_INTERVAL_SECONDS = 31_536_000


def resolve_timezone(
    requested: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve and validate the Scheduler timezone without a regional default."""

    environment = os.environ if env is None else env
    candidates = [
        requested,
        environment.get("AGENT_FORGE_USER_TZ"),
        environment.get("TZ"),
    ]
    for raw in candidates:
        value = (raw or "").strip()
        if not value:
            continue
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    local = datetime.now().astimezone().tzinfo
    local_key = getattr(local, "key", None)
    if local_key:
        try:
            ZoneInfo(local_key)
            return str(local_key)
        except ZoneInfoNotFoundError:
            pass
    return "UTC"


def _parse_datetime(value: str, timezone_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_at is required for trigger_type='date'")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("run_at must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed


def normalize_run_at(value: str, timezone_name: str) -> str:
    return _parse_datetime(value, timezone_name).astimezone(timezone.utc).isoformat()


def validate_interval_seconds(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("interval_seconds must be an integer")
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_seconds must be an integer") from exc
    if not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
        raise ValueError(
            f"interval_seconds must be between {MIN_INTERVAL_SECONDS} and "
            f"{MAX_INTERVAL_SECONDS}"
        )
    return seconds


def init_trigger_store() -> None:
    """Apply additive trigger schema migration and status compatibility."""

    job_store.init_job_store()
    with job_store._get_conn() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(scheduler_jobs)").fetchall()
        }
        if "interval_seconds" not in columns:
            conn.execute(
                "ALTER TABLE scheduler_jobs ADD COLUMN interval_seconds INTEGER"
            )
        conn.commit()
    # Existing job_store validation reads this module global dynamically.
    job_store.VALID_JOB_STATUSES = VALID_JOB_STATUSES


def add_job(
    *,
    job_type: str,
    params: Optional[dict] = None,
    trigger_type: str = "cron",
    cron_expr: str = "",
    run_at: str | None = None,
    interval_seconds: Any = None,
    timezone_str: str | None = None,
    job_id: str | None = None,
    status: str = "active",
    max_instances: int = 1,
    misfire_grace_time: int = 60,
    coalesce: bool = True,
) -> str:
    """Persist a validated production Scheduler job."""

    job_store._validate_job_type(job_type)
    if trigger_type not in SUPPORTED_TRIGGER_TYPES:
        raise ValueError(
            f"invalid trigger_type '{trigger_type}': must be one of "
            f"{sorted(SUPPORTED_TRIGGER_TYPES)}"
        )
    if status not in VALID_JOB_STATUSES:
        raise ValueError(
            f"invalid status '{status}': must be one of {sorted(VALID_JOB_STATUSES)}"
        )
    if not isinstance(params or {}, dict):
        raise ValueError("params must be an object")
    if not 1 <= int(max_instances) <= 100:
        raise ValueError("max_instances must be between 1 and 100")
    if not 0 <= int(misfire_grace_time) <= 86_400:
        raise ValueError("misfire_grace_time must be between 0 and 86400")

    timezone_name = resolve_timezone(timezone_str)
    normalized_run_at: str | None = None
    normalized_interval: int | None = None
    recurrence = ""
    if trigger_type == "cron":
        if not isinstance(cron_expr, str) or len(cron_expr.split()) != 5:
            raise ValueError("cron_expr must contain exactly 5 fields")
        recurrence = cron_expr
    elif trigger_type == "date":
        normalized_run_at = normalize_run_at(run_at or "", timezone_name)
        recurrence = normalized_run_at
    else:
        normalized_interval = validate_interval_seconds(interval_seconds)
        recurrence = f"PT{normalized_interval}S"

    init_trigger_store()
    identifier = job_id or f"job_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    next_run_at = normalized_run_at
    if trigger_type == "interval":
        next_run_at = (now + timedelta(seconds=normalized_interval or 0)).isoformat()
    payload_json = json.dumps(params or {}, ensure_ascii=False)

    with job_store._get_conn() as conn:
        conn.execute(
            """INSERT INTO scheduler_jobs
               (job_id, job_type, payload, trigger_type, cron_expr, run_at,
                interval_seconds, timezone, recurrence, status, created_at,
                updated_at, last_run_at, next_run_at, last_error, run_count,
                max_instances, misfire_grace_time, coalesce)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 0,
                       ?, ?, ?)""",
            (
                identifier,
                job_type,
                payload_json,
                trigger_type,
                cron_expr if trigger_type == "cron" else "",
                normalized_run_at,
                normalized_interval,
                timezone_name,
                recurrence,
                status,
                now.isoformat(),
                now.isoformat(),
                next_run_at,
                int(max_instances),
                int(misfire_grace_time),
                1 if coalesce else 0,
            ),
        )
        conn.commit()
    return identifier


def build_trigger(job: Mapping[str, Any]):
    """Build an APScheduler trigger from one persisted job."""

    try:
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.date import DateTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError as exc:
        raise RuntimeError("APScheduler is unavailable") from exc

    trigger_type = job.get("trigger_type", "cron")
    timezone_name = resolve_timezone(job.get("timezone"))
    tz = ZoneInfo(timezone_name)
    if trigger_type == "cron":
        expression = str(job.get("cron_expr") or "")
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(f"cron expression must contain 5 fields: {expression}")
        return CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=tz,
        )
    if trigger_type == "date":
        run_date = _parse_datetime(str(job.get("run_at") or ""), timezone_name)
        return DateTrigger(run_date=run_date, timezone=tz)
    if trigger_type == "interval":
        seconds = validate_interval_seconds(job.get("interval_seconds"))
        return IntervalTrigger(seconds=seconds, timezone=tz)
    raise ValueError(f"unsupported persisted trigger_type: {trigger_type}")


def add_to_scheduler(
    scheduler,
    *,
    job_id: str,
    execute_fn: Callable[..., dict],
) -> bool:
    """Restore or register one persisted job in APScheduler."""

    init_trigger_store()
    job = job_store.get_job(job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")
    trigger_type = job.get("trigger_type", "cron")
    scheduler.add_job(
        func=execute_fn,
        trigger=build_trigger(job),
        args=[job_id, trigger_type],
        id=job_id,
        replace_existing=True,
        max_instances=int(job.get("max_instances", 1)),
        misfire_grace_time=int(job.get("misfire_grace_time", 60)),
        coalesce=bool(job.get("coalesce", 1)),
    )
    scheduled = scheduler.get_job(job_id)
    next_run_at = None
    if scheduled is not None and scheduled.next_run_time is not None:
        next_run_at = scheduled.next_run_time.astimezone(timezone.utc).isoformat()
    job_store.update_job_run_info(job_id, next_run_at=next_run_at)
    return True


def finish_one_shot(job_id: str, *, success: bool, error: str | None = None) -> None:
    """Mark a date job terminal after its sole execution attempt."""

    init_trigger_store()
    status = "completed" if success else "error"
    now = datetime.now(timezone.utc).isoformat()
    with job_store._get_conn() as conn:
        conn.execute(
            """UPDATE scheduler_jobs
               SET status = ?, next_run_at = NULL, last_error = ?, updated_at = ?
               WHERE job_id = ?""",
            (status, error, now, job_id),
        )
        conn.commit()


def job_from_request(body: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an HTTP create-job payload for ``add_job``."""

    trigger_type = str(body.get("trigger_type", "cron"))
    return {
        "job_type": body.get("job_type", ""),
        "params": body.get("params", {}),
        "trigger_type": trigger_type,
        "cron_expr": body.get("cron_expr", ""),
        "run_at": body.get("run_at"),
        "interval_seconds": body.get("interval_seconds"),
        "timezone_str": body.get("timezone"),
        "max_instances": body.get("max_instances", 1),
        "misfire_grace_time": body.get("misfire_grace_time", 60),
        "coalesce": body.get("coalesce", True),
    }


__all__ = [
    "MAX_INTERVAL_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "SUPPORTED_TRIGGER_TYPES",
    "VALID_JOB_STATUSES",
    "add_job",
    "add_to_scheduler",
    "build_trigger",
    "finish_one_shot",
    "init_trigger_store",
    "job_from_request",
    "normalize_run_at",
    "resolve_timezone",
    "validate_interval_seconds",
]
