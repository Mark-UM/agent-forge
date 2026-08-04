"""Tests for modules/scheduler/daemon.py — v1.8 Phase B2 + SC1/SC3 fixes."""
import json
import os
import tempfile
from pathlib import Path

import pytest


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def temp_jobs_file(tmp_path, monkeypatch):
    """Patch JOBS_FILE to a temporary path (deprecated, kept for compat)."""
    from modules.scheduler import daemon

    temp_jobs = tmp_path / "jobs.json"
    monkeypatch.setattr(daemon, "JOBS_FILE", str(temp_jobs))
    monkeypatch.setattr(daemon, "_ensure_jobs_dir", lambda: None)
    yield temp_jobs


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """SC1: Patch job_store _DB_PATH to a temp SQLite file."""
    from modules.scheduler import job_store

    temp_db_path = tmp_path / "test-scheduler.db"
    monkeypatch.setattr(job_store, "_DB_PATH", temp_db_path)
    job_store.init_job_store()
    yield temp_db_path


# ── Health check tests ─────────────────────────────────────────


def test_health_check_returns_dict():
    """health_check should return a dict with expected keys."""
    from modules.scheduler.daemon import health_check

    result = health_check()

    assert isinstance(result, dict)
    assert "apscheduler_available" in result
    assert "jobs_file" in result  # deprecated but kept for compat
    assert "daemon_port" in result
    assert "job_types" in result


def test_health_check_apscheduler_available():
    """APScheduler should be available (installed in vendor/python-libs)."""
    from modules.scheduler.daemon import health_check

    result = health_check()
    assert result["apscheduler_available"] is True
    assert result["apscheduler_version"] is not None


def test_health_check_port():
    """Daemon port should be 9225."""
    from modules.scheduler.daemon import health_check

    assert health_check()["daemon_port"] == 9225


def test_health_check_job_types():
    """Job types should include all expected types."""
    from modules.scheduler.daemon import health_check

    types = health_check()["job_types"]
    assert "file_reindex" in types
    assert "report_collect" in types
    assert "memory_review" in types
    assert "action_extract" in types
    assert "custom" in types
    assert "pattern_extract" not in types


def test_sc1_health_check_reports_sqlite_source():
    """SC1: health_check should report SQLite as the state source."""
    from modules.scheduler.daemon import health_check

    result = health_check()
    assert result["state_source"] == "sqlite"
    assert "db_path" in result
    assert "migration_version" in result
    assert "total_jobs" in result
    assert "active_jobs" in result


# ── Jobs persistence tests (SC1: SQLite-backed) ────────────────


def test_load_jobs_empty(temp_db):
    """SC1: Loading jobs from empty SQLite returns empty dict."""
    from modules.scheduler.daemon import _load_jobs

    assert _load_jobs() == {}


def test_sc1_save_jobs_is_deprecated_noop(temp_db):
    """SC1: _save_jobs is deprecated and is a no-op."""
    from modules.scheduler.daemon import _save_jobs
    import warnings

    jobs = {"job_001": {"job_type": "file_reindex"}}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _save_jobs(jobs)
        deprecation = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation) >= 1


def test_load_jobs_after_add(temp_db):
    """SC1: _load_jobs reads from SQLite after add_job."""
    from modules.scheduler.daemon import _load_jobs
    from modules.scheduler import job_store

    job_store.add_job(
        job_type="file_reindex",
        cron_expr="0 9 * * *",
        params={"root": "/tmp"},
    )
    loaded = _load_jobs()
    assert len(loaded) == 1
    job_id = list(loaded.keys())[0]
    assert loaded[job_id]["job_type"] == "file_reindex"
    assert loaded[job_id]["cron_expr"] == "0 9 * * *"
    assert loaded[job_id]["params"] == {"root": "/tmp"}
    assert loaded[job_id]["status"] == "active"


# ── Job ID generation tests ────────────────────────────────────


def test_generate_job_id_unique():
    """Job IDs should be unique."""
    from modules.scheduler.daemon import _generate_job_id

    ids = {_generate_job_id() for _ in range(100)}
    assert len(ids) == 100  # All unique (uuid4 ensures this)


def test_generate_job_id_format():
    """Job ID should start with 'job_'."""
    from modules.scheduler.daemon import _generate_job_id

    job_id = _generate_job_id()
    assert job_id.startswith("job_")


# ── Job execution tests ────────────────────────────────────────


def test_execute_job_unknown_type():
    """Unknown job type should return error."""
    from modules.scheduler.daemon import _execute_job

    result = _execute_job("unknown_type", {})
    assert result["success"] is False
    assert "error" in result


def test_execute_job_memory_review():
    """memory_review delegates to the real read-only memory reviewer."""
    from unittest.mock import patch
    from modules.scheduler.daemon import _execute_job

    expected = {"success": True, "action_count": 2, "report_path": "report.md"}
    with patch("modules.memory.hook.review_memory", return_value=expected):
        result = _execute_job("memory_review", {})
    assert result["success"] is True
    assert result["detail"] == expected


def test_execute_job_report_collect_propagates_failure():
    from unittest.mock import patch
    from modules.scheduler.daemon import _execute_job

    with patch(
        "modules.orchestrator.agent_wrapper.run_collection_pipeline",
        return_value={"success": False, "error": "all backends unavailable"},
    ):
        result = _execute_job("report_collect", {"target": "https://example.com"})

    assert result["success"] is False
    assert result["detail"]["error"] == "all backends unavailable"


def test_execute_job_action_extract_missing_path():
    """action_extract job without report_path should fail."""
    from modules.scheduler.daemon import _execute_job

    result = _execute_job("action_extract", {})
    assert result["success"] is False
    assert "report_path" in result["error"]


def test_execute_job_custom_whitelist_violation():
    """Custom job with non-whitelisted module should fail."""
    from modules.scheduler.daemon import _execute_job

    result = _execute_job("custom", {
        "module": "os",
        "function": "system",
        "params": {"command": "echo hacked"},
    })
    assert result["success"] is False
    assert "not in whitelist" in result.get("error", "")


def test_execute_job_custom_propagates_structured_failure():
    from unittest.mock import patch
    from modules.scheduler.daemon import _execute_job

    with patch(
        "modules.memory.hook.check_memory_health",
        return_value={"success": False, "error": "unhealthy"},
    ):
        result = _execute_job(
            "custom",
            {
                "module": "modules.memory.hook",
                "function": "check_memory_health",
                "params": {},
            },
        )

    assert result["success"] is False
    assert result["detail"]["error"] == "unhealthy"


# ── Cron validation tests ──────────────────────────────────────


def test_cron_validation_5_fields(temp_jobs_file):
    """Cron expression must have exactly 5 fields."""
    from modules.scheduler.daemon import _add_job_to_scheduler
    from modules.scheduler.daemon import _AP_SCHEDULER_AVAILABLE

    if not _AP_SCHEDULER_AVAILABLE:
        pytest.skip("APScheduler not available")

    # Create a mock scheduler
    from unittest.mock import MagicMock
    mock_scheduler = MagicMock()

    # Valid 5-field cron
    _add_job_to_scheduler(mock_scheduler, "job_1", "file_reindex", "0 9 * * *", {})
    mock_scheduler.add_job.assert_called_once()

    # Invalid 4-field cron
    mock_scheduler.reset_mock()
    with pytest.raises(ValueError, match="5 fields"):
        _add_job_to_scheduler(mock_scheduler, "job_2", "file_reindex", "0 9 *", {})


# ── APScheduler integration tests ──────────────────────────────


def test_apscheduler_import():
    """APScheduler should be importable via vendor path."""
    import sys
    vendor = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))),
        "vendor", "python-libs"
    )
    if vendor not in sys.path:
        sys.path.insert(0, vendor)

    import apscheduler
    assert apscheduler.__version__


def test_init_and_shutdown_scheduler():
    """Scheduler should init and shutdown cleanly."""
    from modules.scheduler.daemon import init_scheduler, shutdown_scheduler, _AP_SCHEDULER_AVAILABLE

    if not _AP_SCHEDULER_AVAILABLE:
        pytest.skip("APScheduler not available")

    sched = init_scheduler()
    assert sched is not None

    shutdown_scheduler()
    # Should not raise


# ── SC1: Migration tests ───────────────────────────────────────


def test_sc1_migrate_from_json_idempotent(temp_db, tmp_path, monkeypatch):
    """SC1: Migration from jobs.json should be idempotent."""
    from modules.scheduler import job_store

    # Create a legacy jobs.json
    jobs_json = tmp_path / "jobs.json"
    legacy_data = {
        "job_abc123": {
            "job_type": "file_reindex",
            "cron_expr": "0 9 * * *",
            "params": {"root": "/tmp"},
            "status": "active",
            "created_at": "2026-07-21T10:00:00",
        }
    }
    jobs_json.write_text(json.dumps(legacy_data), encoding="utf-8")

    # First migration
    report1 = job_store.migrate_from_json(json_path=str(jobs_json), backup=True)
    assert report1["migrated_count"] == 1
    assert report1["skipped_count"] == 0
    assert report1["already_migrated"] is False
    assert report1["migration_version"] == "1"

    # Second migration (should be idempotent — already_migrated=True)
    report2 = job_store.migrate_from_json(json_path=str(jobs_json), backup=True)
    assert report2["already_migrated"] is True
    assert report2["migrated_count"] == 0

    # Verify job was migrated to SQLite
    job = job_store.get_job("job_abc123")
    assert job is not None
    assert job["job_type"] == "file_reindex"
    assert job["cron_expr"] == "0 9 * * *"
    assert job["payload"] == {"root": "/tmp"}
    assert job["status"] == "active"


def test_sc1_migrate_creates_backup(temp_db, tmp_path):
    """SC1: Migration should create a backup of jobs.json."""
    from modules.scheduler import job_store

    jobs_json = tmp_path / "jobs.json"
    legacy_data = {
        "job_backup_test": {
            "job_type": "memory_review",
            "cron_expr": "0 0 * * *",
            "params": {},
            "status": "active",
            "created_at": "2026-07-21T10:00:00",
        }
    }
    jobs_json.write_text(json.dumps(legacy_data), encoding="utf-8")

    report = job_store.migrate_from_json(json_path=str(jobs_json), backup=True)
    assert report["migrated_count"] == 1
    assert report["backed_up"] is True
    assert report["backup_path"] is not None
    # Backup file should exist
    assert Path(report["backup_path"]).exists()


def test_sc1_migrate_no_json_file(temp_db, tmp_path):
    """SC1: Migration with no jobs.json should record version and return."""
    from modules.scheduler import job_store

    non_existent = tmp_path / "nonexistent.json"
    report = job_store.migrate_from_json(json_path=str(non_existent))
    assert report["migrated_count"] == 0
    assert report["already_migrated"] is False
    # Version should be recorded
    assert job_store.get_migration_version() == "1"


def test_sc1_migrate_corrupt_json(temp_db, tmp_path):
    """SC1: Corrupt jobs.json should not crash migration.

    R2-4.6: Corrupt jobs.json must NOT record the migration version, so
    future migration attempts can retry once the file is fixed.
    """
    from modules.scheduler import job_store

    jobs_json = tmp_path / "jobs.json"
    jobs_json.write_text("{corrupt json", encoding="utf-8")

    report = job_store.migrate_from_json(json_path=str(jobs_json))
    assert report["migrated_count"] == 0
    # R2-4.6: Version should NOT be recorded (allow retries)
    assert job_store.get_migration_version() is None


def test_sc1_migrate_skips_existing_jobs(temp_db, tmp_path):
    """SC1: Migration should skip jobs that already exist in SQLite."""
    from modules.scheduler import job_store

    # Pre-insert a job with the same ID
    job_store.add_job(
        job_type="file_reindex",
        cron_expr="0 9 * * *",
        params={"existing": True},
        job_id="job_existing_001",
    )

    # Create jobs.json with the same job_id
    jobs_json = tmp_path / "jobs.json"
    legacy_data = {
        "job_existing_001": {
            "job_type": "file_reindex",
            "cron_expr": "0 10 * * *",  # Different cron
            "params": {"new": True},
            "status": "active",
            "created_at": "2026-07-21T10:00:00",
        }
    }
    jobs_json.write_text(json.dumps(legacy_data), encoding="utf-8")

    report = job_store.migrate_from_json(json_path=str(jobs_json), backup=False)
    assert report["migrated_count"] == 0
    assert report["skipped_count"] == 1

    # Original job should be unchanged
    job = job_store.get_job("job_existing_001")
    assert job["cron_expr"] == "0 9 * * *"  # Original, not migrated


def test_sc1_single_source_no_json_writes(temp_db, tmp_path, monkeypatch):
    """SC1: After migration, daemon should not write to jobs.json."""
    from modules.scheduler import daemon
    from modules.scheduler import job_store

    # Create legacy jobs.json
    jobs_json = tmp_path / "jobs.json"
    jobs_json.write_text(json.dumps({
        "job_test_001": {
            "job_type": "file_reindex",
            "cron_expr": "0 9 * * *",
            "params": {},
            "status": "active",
            "created_at": "2026-07-21T10:00:00",
        }
    }), encoding="utf-8")

    monkeypatch.setattr(job_store, "_JOBS_JSON_PATH", jobs_json)
    monkeypatch.setattr(daemon, "JOBS_FILE", str(jobs_json))

    # Migrate
    job_store.migrate_from_json(json_path=str(jobs_json))

    # Add a new job via daemon's _load_jobs (reads from SQLite)
    loaded = daemon._load_jobs()
    assert "job_test_001" in loaded

    # jobs.json should still exist (as backup) but not be the source
    # The key point: daemon reads from SQLite, not jobs.json


def test_sc1_job_store_crud(temp_db):
    """SC1: job_store CRUD operations work correctly."""
    from modules.scheduler import job_store

    # Create
    job_id = job_store.add_job(
        job_type="memory_review",
        cron_expr="0 0 * * *",
        params={"output_path": "/tmp/report.md"},
    )
    assert job_id.startswith("job_")

    # Read
    job = job_store.get_job(job_id)
    assert job is not None
    assert job["job_type"] == "memory_review"
    assert job["payload"] == {"output_path": "/tmp/report.md"}
    assert job["status"] == "active"
    assert job["run_count"] == 0

    # Update status
    assert job_store.update_job_status(job_id, "paused") is True
    assert job_store.get_job(job_id)["status"] == "paused"

    # Update run info
    assert job_store.update_job_run_info(
        job_id,
        last_run_at="2026-08-04T10:00:00+00:00",
        next_run_at="2026-08-05T00:00:00+00:00",
        increment_run_count=True,
    ) is True
    job = job_store.get_job(job_id)
    assert job["last_run_at"] == "2026-08-04T10:00:00+00:00"
    assert job["run_count"] == 1

    # Delete (soft)
    assert job_store.delete_job(job_id) is True
    # Should not appear in list_jobs (excludes deleted)
    jobs = job_store.list_jobs()
    assert all(j["job_id"] != job_id for j in jobs)
    # But get_job still returns it (with status=deleted)
    assert job_store.get_job(job_id)["status"] == "deleted"


def test_sc1_job_store_list_filter(temp_db):
    """SC1: list_jobs filters by status correctly."""
    from modules.scheduler import job_store

    job_store.add_job(job_type="file_reindex", cron_expr="0 9 * * *", job_id="j1")
    job_store.add_job(job_type="memory_review", cron_expr="0 0 * * *", job_id="j2")
    job_store.add_job(job_type="report_collect", cron_expr="0 12 * * *", job_id="j3")
    job_store.update_job_status("j2", "paused")

    all_jobs = job_store.list_jobs()
    assert len(all_jobs) == 3  # j1, j2, j3 (paused still listed)

    active_only = job_store.list_jobs(status="active")
    assert len(active_only) == 2
    assert all(j["status"] == "active" for j in active_only)

    paused_only = job_store.list_jobs(status="paused")
    assert len(paused_only) == 1
    assert paused_only[0]["job_id"] == "j2"


# ── SC3: ThreadingHTTPServer tests ─────────────────────────────


def test_sc3_uses_threading_http_server():
    """SC3: daemon should use ThreadingHTTPServer, not HTTPServer."""
    from modules.scheduler import daemon
    import http.server

    # Check that ThreadingHTTPServer is imported (not just HTTPServer)
    assert hasattr(daemon, "ThreadingHTTPServer")
    assert daemon.ThreadingHTTPServer is http.server.ThreadingHTTPServer


def test_sc3_run_daemon_uses_threading_server(monkeypatch):
    """SC3: run_daemon should instantiate ThreadingHTTPServer."""
    from modules.scheduler import daemon

    created_servers = []

    class FakeServer:
        def __init__(self, addr, handler):
            created_servers.append((type(self).__name__, addr, handler))

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    # Patch ThreadingHTTPServer to capture instantiation
    monkeypatch.setattr(daemon, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(daemon, "init_scheduler", lambda: None)
    monkeypatch.setattr(daemon, "shutdown_scheduler", lambda: None)

    daemon.run_daemon()

    assert len(created_servers) == 1
    assert created_servers[0][0] == "FakeServer"
