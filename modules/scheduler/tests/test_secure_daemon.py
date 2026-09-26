"""Regression checks for the production Scheduler manual-run boundary."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from modules.scheduler import daemon, job_store


@pytest.fixture
def secure_daemon():
    """Restore the legacy globals changed by the production adapter import."""
    original_execute = daemon.execute_job_with_tracking
    original_schedule = daemon._add_job_to_scheduler
    from modules.scheduler import secure_daemon as adapter

    yield adapter

    daemon.execute_job_with_tracking = original_execute
    daemon._add_job_to_scheduler = original_schedule


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "_DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(job_store, "_JOBS_JSON_PATH", tmp_path / "jobs.json")
    job_store.init_job_store()
    yield


def _job_id():
    return job_store.add_job(
        job_type="file_reindex", cron_expr="0 9 * * *", params={}
    )


def _handler():
    handler = MagicMock()
    handler._send_json = MagicMock()
    return handler



def test_manual_run_reuses_returned_run_id(isolated_store, monkeypatch, secure_daemon):
    job_id = _job_id()
    monkeypatch.setattr(
        secure_daemon.legacy,
        "_execute_job",
        lambda job_type, params: {"job_type": job_type, "success": True},
    )
    handler = _handler()
    secure_daemon.SecureSchedulerHandler._queue_manual_run(handler, job_id)
    status, response = handler._send_json.call_args.args
    assert status == 202
    run_id = response["run_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        run = job_store.get_job_run(run_id)
        if run["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert run["status"] == "succeeded"
    assert len(job_store.list_job_runs(job_id)) == 1

def test_manual_run_worker_failure_becomes_terminal(isolated_store, monkeypatch, secure_daemon):
    job_id = _job_id()

    def fail_before_execution(*args, **kwargs):
        raise RuntimeError("worker setup failed")

    monkeypatch.setattr(
        secure_daemon, "execute_job_with_tracking", fail_before_execution
    )
    handler = _handler()
    secure_daemon.SecureSchedulerHandler._queue_manual_run(handler, job_id)
    status, response = handler._send_json.call_args.args
    assert status == 202
    run_id = response["run_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        run = job_store.get_job_run(run_id)
        if run["status"] == "failed":
            break
        time.sleep(0.01)
    assert run["status"] == "failed"
    assert len(job_store.list_job_runs(job_id)) == 1


def test_manual_run_thread_start_failure_is_recorded(isolated_store, monkeypatch, secure_daemon):
    job_id = _job_id()

    def fail_start(self):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    handler = _handler()
    secure_daemon.SecureSchedulerHandler._queue_manual_run(handler, job_id)
    status, response = handler._send_json.call_args.args
    assert status == 500
    assert response["error"] == "worker could not start"
    assert job_store.get_job_run(response["run_id"])["status"] == "failed"
    assert len(job_store.list_job_runs(job_id)) == 1
