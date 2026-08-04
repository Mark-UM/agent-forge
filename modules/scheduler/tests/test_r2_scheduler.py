"""Round 2 Phase 4 scheduler tests.

Covers:
    R2-4.1: Three separate tables (scheduler_jobs, job_runs, quarantined_jobs)
    R2-4.2: Trigger whitelist (only 'cron' supported; date/interval rejected)
    R2-4.3: Job config (timezone, max_instances, misfire_grace_time, coalesce)
            passed through to APScheduler
    R2-4.4: execute_job_with_tracking creates JobRun rows
    R2-4.5: Async manual trigger (POST /jobs/{job_id}/runs → run_id + QUEUED)
    R2-4.6: Migration quarantine (unmappable jobs → quarantined_jobs, not custom)
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Patch job_store _DB_PATH to a temp SQLite file."""
    from modules.scheduler import job_store

    temp_db_path = tmp_path / "test-r2-scheduler.db"
    monkeypatch.setattr(job_store, "_DB_PATH", temp_db_path)
    monkeypatch.setattr(job_store, "_JOBS_JSON_PATH", tmp_path / "jobs.json")
    job_store.init_job_store()
    yield temp_db_path


# ── R2-4.1: job_runs table exists ─────────────────────────────


class TestJobRunsTable:
    def test_job_runs_table_exists(self, temp_db):
        """R2-4.1: job_runs table must exist after init_job_store()."""
        from modules.scheduler import job_store

        with job_store._get_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        names = {t[0] for t in tables}
        assert "job_runs" in names
        assert "quarantined_jobs" in names
        assert "scheduler_jobs" in names

    def test_create_job_run_returns_run_id(self, temp_db):
        """create_job_run should return a run_id and insert a QUEUED row."""
        from modules.scheduler import job_store

        # Need a job first
        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        run_id = job_store.create_job_run(job_id, triggered_by="manual")
        assert run_id.startswith("run_")

        run = job_store.get_job_run(run_id)
        assert run is not None
        assert run["status"] == "queued"
        assert run["triggered_by"] == "manual"
        assert run["job_id"] == job_id

    def test_update_job_run_transitions(self, temp_db):
        """update_job_run should transition status and set fields."""
        from modules.scheduler import job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        run_id = job_store.create_job_run(job_id, triggered_by="cron")

        # QUEUED → RUNNING
        job_store.update_job_run(
            run_id, status="running", started_at="2026-01-01T00:00:00Z"
        )
        run = job_store.get_job_run(run_id)
        assert run["status"] == "running"
        assert run["started_at"] == "2026-01-01T00:00:00Z"

        # RUNNING → SUCCEEDED
        job_store.update_job_run(
            run_id, status="succeeded", ended_at="2026-01-01T00:00:05Z",
            duration_ms=5000.0,
        )
        run = job_store.get_job_run(run_id)
        assert run["status"] == "succeeded"
        assert run["duration_ms"] == 5000.0

    def test_list_job_runs_by_job(self, temp_db):
        """list_job_runs should filter by job_id."""
        from modules.scheduler import job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        job_store.create_job_run(job_id, triggered_by="cron")
        job_store.create_job_run(job_id, triggered_by="manual")

        runs = job_store.list_job_runs(job_id=job_id)
        assert len(runs) == 2

    def test_invalid_run_status_rejected(self, temp_db):
        """update_job_run should reject invalid status values."""
        from modules.scheduler import job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        run_id = job_store.create_job_run(job_id, triggered_by="cron")
        with pytest.raises(ValueError, match="invalid run status"):
            job_store.update_job_run(run_id, status="invalid_status")


# ── R2-4.2: Trigger whitelist ─────────────────────────────────


class TestTriggerWhitelist:
    def test_cron_trigger_accepted(self, temp_db):
        """cron trigger_type should be accepted."""
        from modules.scheduler import job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *",
            trigger_type="cron", params={},
        )
        assert job_id is not None

    def test_date_trigger_rejected(self, temp_db):
        """date trigger should raise UnsupportedTriggerError."""
        from modules.scheduler import job_store

        with pytest.raises(job_store.UnsupportedTriggerError):
            job_store.add_job(
                job_type="file_reindex", cron_expr="",
                trigger_type="date", params={},
            )

    def test_interval_trigger_rejected(self, temp_db):
        """interval trigger should raise UnsupportedTriggerError."""
        from modules.scheduler import job_store

        with pytest.raises(job_store.UnsupportedTriggerError):
            job_store.add_job(
                job_type="file_reindex", cron_expr="",
                trigger_type="interval", params={},
            )

    def test_supported_trigger_types_only_cron(self, temp_db):
        """SUPPORTED_TRIGGER_TYPES should only contain 'cron'."""
        from modules.scheduler import job_store

        assert job_store.SUPPORTED_TRIGGER_TYPES == frozenset({"cron"})


# ── R2-4.3: Job config passthrough ────────────────────────────


class TestJobConfigPassthrough:
    def test_add_job_to_scheduler_passes_config(self, temp_db):
        """_add_job_to_scheduler should pass timezone, max_instances,
        misfire_grace_time, coalesce to scheduler.add_job()."""
        from modules.scheduler import daemon

        mock_scheduler = MagicMock()
        daemon._add_job_to_scheduler(
            mock_scheduler,
            "test-job-1",
            "file_reindex",
            "0 9 * * *",
            {},
            timezone_str="Asia/Shanghai",
            max_instances=3,
            misfire_grace_time=120,
            coalesce=False,
        )
        mock_scheduler.add_job.assert_called_once()
        kwargs = mock_scheduler.add_job.call_args.kwargs
        assert kwargs["timezone"] == "Asia/Shanghai"
        assert kwargs["max_instances"] == 3
        assert kwargs["misfire_grace_time"] == 120
        assert kwargs["coalesce"] is False

    def test_add_job_to_scheduler_uses_tracking_wrapper(self, temp_db):
        """_add_job_to_scheduler should schedule execute_job_with_tracking,
        NOT _execute_job directly."""
        from modules.scheduler import daemon

        mock_scheduler = MagicMock()
        daemon._add_job_to_scheduler(
            mock_scheduler, "test-job-2", "file_reindex", "0 9 * * *", {}
        )
        kwargs = mock_scheduler.add_job.call_args.kwargs
        assert kwargs["func"] == daemon.execute_job_with_tracking

    def test_job_config_stored_in_sqlite(self, temp_db):
        """Job config fields should be persisted in scheduler_jobs."""
        from modules.scheduler import job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *",
            params={}, timezone_str="Asia/Shanghai",
            max_instances=2, misfire_grace_time=90, coalesce=False,
        )
        job = job_store.get_job(job_id)
        assert job["timezone"] == "Asia/Shanghai"
        assert job["max_instances"] == 2
        assert job["misfire_grace_time"] == 90
        assert job["coalesce"] is False


# ── R2-4.4: execute_job_with_tracking ─────────────────────────


class TestExecuteJobWithTracking:
    def test_tracking_creates_job_run_row(self, temp_db, monkeypatch):
        """execute_job_with_tracking should create a job_runs row."""
        from modules.scheduler import daemon, job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        # Mock _execute_job to avoid actual file operations
        monkeypatch.setattr(
            daemon, "_execute_job",
            lambda jt, p: {"job_type": jt, "success": True, "executed_at": "now"},
        )
        # Mock _scheduler to None so next_run_at lookup is skipped
        monkeypatch.setattr(daemon, "_scheduler", None)

        result = daemon.execute_job_with_tracking(job_id, triggered_by="manual")
        assert result["success"] is True
        assert result["run_status"] == "succeeded"
        assert "run_id" in result

        # Verify JobRun row was created
        run = job_store.get_job_run(result["run_id"])
        assert run is not None
        assert run["status"] == "succeeded"
        assert run["triggered_by"] == "manual"

    def test_tracking_records_failure(self, temp_db, monkeypatch):
        """execute_job_with_tracking should mark the run as FAILED on error."""
        from modules.scheduler import daemon, job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )

        def failing_execute(job_type, params):
            return {"job_type": job_type, "success": False, "error": "boom"}

        monkeypatch.setattr(daemon, "_execute_job", failing_execute)
        monkeypatch.setattr(daemon, "_scheduler", None)

        result = daemon.execute_job_with_tracking(job_id, triggered_by="cron")
        assert result["success"] is False
        assert result["run_status"] == "failed"

        run = job_store.get_job_run(result["run_id"])
        assert run["status"] == "failed"
        assert run["error"] == "boom"

    def test_tracking_increments_run_count(self, temp_db, monkeypatch):
        """execute_job_with_tracking should increment run_count on the job."""
        from modules.scheduler import daemon, job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        monkeypatch.setattr(
            daemon, "_execute_job",
            lambda jt, p: {"job_type": jt, "success": True},
        )
        monkeypatch.setattr(daemon, "_scheduler", None)

        daemon.execute_job_with_tracking(job_id)
        daemon.execute_job_with_tracking(job_id)

        job = job_store.get_job(job_id)
        assert job["run_count"] == 2

    def test_tracking_updates_last_run_at(self, temp_db, monkeypatch):
        """execute_job_with_tracking should update last_run_at."""
        from modules.scheduler import daemon, job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        assert job_store.get_job(job_id)["last_run_at"] is None

        monkeypatch.setattr(
            daemon, "_execute_job",
            lambda jt, p: {"job_type": jt, "success": True},
        )
        monkeypatch.setattr(daemon, "_scheduler", None)

        daemon.execute_job_with_tracking(job_id)
        job = job_store.get_job(job_id)
        assert job["last_run_at"] is not None


# ── R2-4.5: Async manual trigger ──────────────────────────────


class TestAsyncManualTrigger:
    def test_post_jobs_runs_returns_queued(self, temp_db, monkeypatch):
        """POST /jobs/{job_id}/runs should return 202 + run_id + queued."""
        from modules.scheduler import daemon, job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        # Mock _execute_job so the background thread finishes quickly
        monkeypatch.setattr(
            daemon, "_execute_job",
            lambda jt, p: {"job_type": jt, "success": True},
        )
        monkeypatch.setattr(daemon, "_scheduler", None)

        handler = MagicMock()
        handler.path = f"/jobs/{job_id}/runs"
        handler.rfile = MagicMock()
        handler.wfile = MagicMock()
        handler.headers = {}

        # Capture the response
        captured = {}

        def fake_send_json(status, data):
            captured["status"] = status
            captured["data"] = data

        handler._send_json = fake_send_json

        SchedulerHandler = daemon.SchedulerHandler
        SchedulerHandler.do_POST(handler)

        assert captured["status"] == 202
        assert captured["data"]["run_id"].startswith("run_")
        assert captured["data"]["status"] == "queued"

    def test_get_run_returns_run_status(self, temp_db, monkeypatch):
        """GET /runs/{run_id} should return the run's status."""
        from modules.scheduler import daemon, job_store

        job_id = job_store.add_job(
            job_type="file_reindex", cron_expr="0 9 * * *", params={}
        )
        run_id = job_store.create_job_run(job_id, triggered_by="manual")
        job_store.update_job_run(run_id, status="succeeded")

        handler = MagicMock()
        handler.path = f"/runs/{run_id}"

        captured = {}

        def fake_send_json(status, data):
            captured["status"] = status
            captured["data"] = data

        handler._send_json = fake_send_json

        SchedulerHandler = daemon.SchedulerHandler
        SchedulerHandler.do_GET(handler)

        assert captured["status"] == 200
        assert captured["data"]["run"]["run_id"] == run_id
        assert captured["data"]["run"]["status"] == "succeeded"


# ── R2-4.6: Migration quarantine ──────────────────────────────


class TestMigrationQuarantine:
    def test_unmappable_job_type_quarantined(self, temp_db, tmp_path):
        """Unmappable job_type should go to quarantine, not auto-convert."""
        from modules.scheduler import job_store

        jobs_json = tmp_path / "jobs.json"
        jobs_json.write_text(json.dumps({
            "job_good": {
                "job_type": "file_reindex",
                "cron_expr": "0 9 * * *",
                "params": {},
                "status": "active",
            },
            "job_bad_type": {
                "job_type": "nonexistent_type",
                "cron_expr": "0 10 * * *",
                "params": {},
                "status": "active",
            },
        }))

        report = job_store.migrate_from_json(json_path=str(jobs_json))
        assert report["migrated_count"] == 1
        assert report["quarantined_count"] == 1

        quarantined = job_store.list_quarantined_jobs()
        assert len(quarantined) == 1
        assert quarantined[0]["id"] == "job_bad_type"
        assert "nonexistent_type" in quarantined[0]["reason"]

    def test_missing_cron_expr_quarantined(self, temp_db, tmp_path):
        """cron job missing cron_expr should be quarantined."""
        from modules.scheduler import job_store

        jobs_json = tmp_path / "jobs.json"
        jobs_json.write_text(json.dumps({
            "job_no_cron": {
                "job_type": "file_reindex",
                "cron_expr": "",
                "params": {},
                "status": "active",
            },
        }))

        report = job_store.migrate_from_json(json_path=str(jobs_json))
        assert report["quarantined_count"] == 1
        assert report["migrated_count"] == 0

    def test_corrupt_json_does_not_record_version(self, temp_db, tmp_path):
        """Corrupt jobs.json should NOT record migration version (allow retry)."""
        from modules.scheduler import job_store

        jobs_json = tmp_path / "jobs.json"
        jobs_json.write_text("{ this is not valid json")

        report = job_store.migrate_from_json(json_path=str(jobs_json))
        assert "error" in report
        # Version should NOT be recorded
        assert job_store.get_migration_version() is None

    def test_valid_migration_records_version(self, temp_db, tmp_path):
        """Successful migration should record the version."""
        from modules.scheduler import job_store

        jobs_json = tmp_path / "jobs.json"
        jobs_json.write_text(json.dumps({
            "job_1": {
                "job_type": "file_reindex",
                "cron_expr": "0 9 * * *",
                "params": {},
                "status": "active",
            },
        }))

        report = job_store.migrate_from_json(json_path=str(jobs_json))
        assert report["migrated_count"] == 1
        assert job_store.get_migration_version() == job_store._MIGRATION_VERSION

    def test_idempotent_migration(self, temp_db, tmp_path):
        """Re-running migration should be a no-op."""
        from modules.scheduler import job_store

        jobs_json = tmp_path / "jobs.json"
        jobs_json.write_text(json.dumps({
            "job_1": {
                "job_type": "file_reindex",
                "cron_expr": "0 9 * * *",
                "params": {},
                "status": "active",
            },
        }))

        report1 = job_store.migrate_from_json(json_path=str(jobs_json))
        assert report1["migrated_count"] == 1

        report2 = job_store.migrate_from_json(json_path=str(jobs_json))
        assert report2["already_migrated"] is True
        assert report2["migrated_count"] == 0
