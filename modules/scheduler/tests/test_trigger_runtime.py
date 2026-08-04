from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.scheduler import job_store, trigger_runtime


@pytest.fixture()
def isolated_store(tmp_path: Path, monkeypatch):
    database = tmp_path / "scheduler.db"
    legacy_json = tmp_path / "jobs.json"
    monkeypatch.setattr(job_store, "_DB_PATH", database)
    monkeypatch.setattr(job_store, "_JOBS_JSON_PATH", legacy_json)
    trigger_runtime.init_trigger_store()
    return database


def test_trigger_schema_adds_interval_column(isolated_store: Path) -> None:
    with job_store._get_conn() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(scheduler_jobs)").fetchall()
        }
    assert "interval_seconds" in columns
    assert "completed" in job_store.VALID_JOB_STATUSES


def test_timezone_resolution_priority() -> None:
    assert trigger_runtime.resolve_timezone(
        "Europe/London",
        env={"AGENT_FORGE_USER_TZ": "Asia/Kuala_Lumpur", "TZ": "UTC"},
    ) == "Europe/London"
    assert trigger_runtime.resolve_timezone(
        None,
        env={"AGENT_FORGE_USER_TZ": "Asia/Kuala_Lumpur", "TZ": "UTC"},
    ) == "Asia/Kuala_Lumpur"
    assert trigger_runtime.resolve_timezone(None, env={"TZ": "UTC"}) == "UTC"


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown IANA timezone"):
        trigger_runtime.resolve_timezone("Mars/Olympus_Mons")


def test_naive_date_is_interpreted_in_requested_timezone() -> None:
    normalized = trigger_runtime.normalize_run_at(
        "2026-08-05T09:00:00", "Asia/Kuala_Lumpur"
    )
    assert datetime.fromisoformat(normalized) == datetime(
        2026, 8, 5, 1, 0, tzinfo=timezone.utc
    )


def test_add_date_job_persists_utc_run_at_and_timezone(
    isolated_store: Path,
) -> None:
    job_id = trigger_runtime.add_job(
        job_type="memory_review",
        trigger_type="date",
        run_at="2026-08-05T09:00:00",
        timezone_str="Asia/Kuala_Lumpur",
        params={"output_path": "_runtime/reports/review.md"},
    )
    job = job_store.get_job(job_id)

    assert job["trigger_type"] == "date"
    assert job["timezone"] == "Asia/Kuala_Lumpur"
    assert job["run_at"] == "2026-08-05T01:00:00+00:00"
    assert job["next_run_at"] == job["run_at"]
    assert job["payload"]["output_path"].endswith("review.md")


def test_add_interval_job_persists_bounded_interval(
    isolated_store: Path,
) -> None:
    job_id = trigger_runtime.add_job(
        job_type="file_reindex",
        trigger_type="interval",
        interval_seconds=900,
        timezone_str="UTC",
        params={"root": "_data/memory"},
    )
    job = job_store.get_job(job_id)

    assert job["trigger_type"] == "interval"
    assert job["interval_seconds"] == 900
    assert job["recurrence"] == "PT900S"
    assert job["next_run_at"] is not None


@pytest.mark.parametrize("value", [None, True, 0, -1, 31_536_001, "abc"])
def test_invalid_interval_is_rejected(value) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="interval_seconds"):
        trigger_runtime.validate_interval_seconds(value)


def test_add_cron_job_uses_same_production_store(isolated_store: Path) -> None:
    job_id = trigger_runtime.add_job(
        job_type="file_reindex",
        trigger_type="cron",
        cron_expr="0 9 * * 1-5",
        timezone_str="Asia/Kuala_Lumpur",
    )
    job = job_store.get_job(job_id)
    assert job["trigger_type"] == "cron"
    assert job["cron_expr"] == "0 9 * * 1-5"
    assert job["timezone"] == "Asia/Kuala_Lumpur"


def test_date_job_is_marked_completed_or_error(isolated_store: Path) -> None:
    success_id = trigger_runtime.add_job(
        job_type="memory_review",
        trigger_type="date",
        run_at="2026-08-05T09:00:00+08:00",
        timezone_str="Asia/Kuala_Lumpur",
    )
    failed_id = trigger_runtime.add_job(
        job_type="memory_review",
        trigger_type="date",
        run_at="2026-08-05T10:00:00+08:00",
        timezone_str="Asia/Kuala_Lumpur",
    )

    trigger_runtime.finish_one_shot(success_id, success=True)
    trigger_runtime.finish_one_shot(
        failed_id, success=False, error="execution failed"
    )

    completed = job_store.get_job(success_id)
    failed = job_store.get_job(failed_id)
    assert completed["status"] == "completed"
    assert completed["next_run_at"] is None
    assert failed["status"] == "error"
    assert failed["last_error"] == "execution failed"


def test_request_normalization_preserves_trigger_fields() -> None:
    normalized = trigger_runtime.job_from_request(
        {
            "job_type": "memory_review",
            "trigger_type": "interval",
            "interval_seconds": 3600,
            "timezone": "Asia/Kuala_Lumpur",
            "params": {"output_path": "report.md"},
        }
    )
    assert normalized["trigger_type"] == "interval"
    assert normalized["interval_seconds"] == 3600
    assert normalized["timezone_str"] == "Asia/Kuala_Lumpur"


def test_builds_real_apscheduler_triggers_when_dependency_available(
    isolated_store: Path,
) -> None:
    pytest.importorskip("apscheduler")

    cron_id = trigger_runtime.add_job(
        job_type="file_reindex",
        trigger_type="cron",
        cron_expr="0 9 * * *",
        timezone_str="UTC",
    )
    date_id = trigger_runtime.add_job(
        job_type="memory_review",
        trigger_type="date",
        run_at="2026-08-05T09:00:00+00:00",
        timezone_str="UTC",
    )
    interval_id = trigger_runtime.add_job(
        job_type="memory_review",
        trigger_type="interval",
        interval_seconds=60,
        timezone_str="UTC",
    )

    assert trigger_runtime.build_trigger(job_store.get_job(cron_id)).__class__.__name__ == "CronTrigger"
    assert trigger_runtime.build_trigger(job_store.get_job(date_id)).__class__.__name__ == "DateTrigger"
    assert trigger_runtime.build_trigger(job_store.get_job(interval_id)).__class__.__name__ == "IntervalTrigger"
