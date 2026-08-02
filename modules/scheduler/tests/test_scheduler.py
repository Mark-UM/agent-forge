"""Tests for modules/scheduler/daemon.py — v1.8 Phase B2."""
import json
import os
import tempfile
from pathlib import Path

import pytest


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def temp_jobs_file(tmp_path, monkeypatch):
    """Patch JOBS_FILE to a temporary path."""
    from modules.scheduler import daemon

    temp_jobs = tmp_path / "jobs.json"
    monkeypatch.setattr(daemon, "JOBS_FILE", str(temp_jobs))
    monkeypatch.setattr(daemon, "_ensure_jobs_dir", lambda: os.makedirs(
        os.path.dirname(str(temp_jobs)), exist_ok=True
    ))
    yield temp_jobs


# ── Health check tests ─────────────────────────────────────────


def test_health_check_returns_dict():
    """health_check should return a dict with expected keys."""
    from modules.scheduler.daemon import health_check

    result = health_check()

    assert isinstance(result, dict)
    assert "apscheduler_available" in result
    assert "jobs_file" in result
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


# ── Jobs persistence tests ─────────────────────────────────────


def test_load_jobs_empty(temp_jobs_file):
    """Loading jobs from non-existent file should return empty dict."""
    from modules.scheduler.daemon import _load_jobs

    assert _load_jobs() == {}


def test_save_and_load_jobs(temp_jobs_file):
    """Saving and loading jobs should round-trip correctly."""
    from modules.scheduler.daemon import _save_jobs, _load_jobs

    jobs = {
        "job_001": {
            "job_type": "file_reindex",
            "cron_expr": "0 9 * * *",
            "params": {"root": "/tmp"},
            "status": "active",
            "created_at": "2026-07-21T10:00:00",
        }
    }
    _save_jobs(jobs)
    loaded = _load_jobs()

    assert loaded == jobs


def test_save_jobs_atomic(temp_jobs_file):
    """Saving jobs should not leave .tmp files."""
    from modules.scheduler.daemon import _save_jobs

    _save_jobs({"job_001": {"job_type": "file_reindex"}})

    tmp_files = list(Path(temp_jobs_file).parent.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_load_jobs_corrupt_file(temp_jobs_file):
    """Loading corrupt JSON should return empty dict."""
    from modules.scheduler.daemon import _load_jobs

    temp_jobs_file.write_text("{corrupt json", encoding="utf-8")
    assert _load_jobs() == {}


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
