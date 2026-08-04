"""Authenticated scheduler daemon with single-Run manual execution.

This module is the production entry point for the scheduler.  It deliberately
reuses the mature storage, APScheduler restoration and HTTP compatibility code
from :mod:`modules.scheduler.daemon`, while replacing the two unsafe seams:

* every HTTP request requires the scheduler bearer token;
* an asynchronous manual trigger creates exactly one ``job_runs`` row and the
  worker updates that same row instead of silently creating a second run.

The compatibility module remains importable for older tests and library users,
but manifests and operator documentation should point at this entry point.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import time
from typing import Any

from modules.common.security import (
    bearer_token_matches,
    load_or_create_service_token,
    token_fingerprint,
)
from modules.scheduler import daemon as legacy


_SERVICE_TOKEN = load_or_create_service_token(
    "scheduler", runtime_root=legacy._PROJECT_ROOT / "_runtime"
)


def execute_job_with_tracking(
    job_id: str,
    triggered_by: str = "cron",
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a job and update one authoritative run record.

    ``run_id`` is supplied by the asynchronous HTTP handler.  Cron and legacy
    synchronous callers omit it and this function creates a run itself.  This
    preserves existing APIs while closing the orphaned-queued-run defect.
    """

    from modules.scheduler import job_store

    job_data = job_store.get_job(job_id)
    if job_data is None:
        return {
            "job_id": job_id,
            "run_id": run_id,
            "run_status": "failed",
            "success": False,
            "error": f"job not found: {job_id}",
        }

    if run_id is None:
        run_id = job_store.create_job_run(job_id, triggered_by=triggered_by)
    else:
        existing = job_store.get_job_run(run_id)
        if existing is None:
            raise ValueError(f"run not found: {run_id}")
        if existing.get("job_id") != job_id:
            raise ValueError("run_id does not belong to job_id")
        if existing.get("status") != "queued":
            raise ValueError(
                f"run must be queued before execution, got {existing.get('status')}"
            )

    job_type = job_data["job_type"]
    params = job_data.get("payload") or {}
    started_at = datetime.now(timezone.utc).isoformat()
    run_count_at_start = int(job_data.get("run_count", 0))
    job_store.update_job_run(
        run_id,
        status="running",
        started_at=started_at,
        run_count_at_start=run_count_at_start,
    )
    job_store.update_job_run_info(job_id, last_run_at=started_at)

    started = time.perf_counter()
    error_message: str | None = None
    try:
        result = legacy._execute_job(job_type, params)
        success = bool(result.get("success", False))
        if not success:
            error_message = str(result.get("error") or "execution failed")
    except Exception as exc:  # defensive boundary around user-configured jobs
        success = False
        error_message = str(exc)
        result = {
            "job_type": job_type,
            "executed_at": started_at,
            "success": False,
            "error": error_message,
        }

    ended_at = datetime.now(timezone.utc).isoformat()
    duration_ms = (time.perf_counter() - started) * 1000.0
    run_status = "succeeded" if success else "failed"
    job_store.update_job_run(
        run_id,
        status=run_status,
        ended_at=ended_at,
        duration_ms=duration_ms,
        error=error_message,
    )

    next_run_at = None
    scheduler = legacy._scheduler
    if scheduler is not None:
        try:
            scheduled_job = scheduler.get_job(job_id)
            if scheduled_job is not None and scheduled_job.next_run_time is not None:
                next_run_at = scheduled_job.next_run_time.isoformat()
        except Exception:
            next_run_at = None

    job_store.update_job_run_info(
        job_id,
        last_run_at=started_at,
        next_run_at=next_run_at,
        last_error=error_message,
        increment_run_count=True,
    )
    result["run_id"] = run_id
    result["run_status"] = run_status
    return result


class SecureSchedulerHandler(legacy.SchedulerHandler):
    """Compatibility handler with authentication and corrected async runs."""

    server_version = "AgentForgeScheduler/2"

    def _authenticated(self) -> bool:
        if bearer_token_matches(self.headers.get("Authorization"), _SERVICE_TOKEN):
            return True
        self._send_json(
            401,
            {
                "error": "unauthorized",
                "message": "Use Authorization: Bearer <scheduler token>",
            },
        )
        return False

    def do_GET(self):  # noqa: N802
        if not self._authenticated():
            return
        return super().do_GET()

    def do_DELETE(self):  # noqa: N802
        if not self._authenticated():
            return
        return super().do_DELETE()

    def do_POST(self):  # noqa: N802
        if not self._authenticated():
            return

        path = self.path.split("?", 1)[0]
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "jobs" or parts[2] != "runs":
            return super().do_POST()

        job_id = parts[1]
        try:
            from modules.scheduler import job_store

            with legacy._jobs_lock:
                job_data = job_store.get_job(job_id)
                if job_data is None:
                    self._send_json(404, {"error": "job not found"})
                    return
                run_id = job_store.create_job_run(job_id, triggered_by="manual")

            worker = threading.Thread(
                target=execute_job_with_tracking,
                kwargs={
                    "job_id": job_id,
                    "triggered_by": "manual",
                    "run_id": run_id,
                },
                daemon=True,
                name=f"job-run-{run_id}",
            )
            worker.start()
            self._send_json(
                202,
                {"job_id": job_id, "run_id": run_id, "status": "queued"},
            )
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


# Ensure APScheduler and the legacy synchronous trigger both use the corrected
# implementation.  The optional ``run_id`` remains keyword-only, so the old
# two-positional-argument call contract is unchanged.
legacy.execute_job_with_tracking = execute_job_with_tracking


def run_daemon() -> None:
    print(f"[scheduler] starting securely on {legacy.DAEMON_HOST}:{legacy.DAEMON_PORT}")
    print(f"[scheduler] token fingerprint: {token_fingerprint(_SERVICE_TOKEN)}")
    print(f"[scheduler] APScheduler available: {legacy._AP_SCHEDULER_AVAILABLE}")
    legacy.init_scheduler()
    server = legacy.ThreadingHTTPServer(
        (legacy.DAEMON_HOST, legacy.DAEMON_PORT), SecureSchedulerHandler
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        legacy.shutdown_scheduler()
        server.server_close()


def health_check() -> dict[str, Any]:
    report = dict(legacy.health_check())
    report.update(
        {
            "authenticated": True,
            "token_fingerprint": token_fingerprint(_SERVICE_TOKEN),
            "manual_run_single_source": True,
        }
    )
    return report


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        print(json.dumps(health_check(), indent=2))
    else:
        run_daemon()
