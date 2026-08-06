"""Authenticated production Scheduler with cron/date/interval support.

This entry point reuses the mature legacy HTTP and execution surface while
replacing its production seams:

* every request requires a bearer token;
* asynchronous manual triggers update one authoritative JobRun;
* job creation and restoration use the trigger runtime for cron, date, and
  interval schedules;
* one-shot date jobs become ``completed`` or ``error`` after execution.
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
from modules.scheduler import trigger_runtime


_SERVICE_TOKEN = load_or_create_service_token(
    "scheduler", runtime_root=legacy._PROJECT_ROOT / "_runtime"
)


def execute_job_with_tracking(
    job_id: str,
    triggered_by: str = "cron",
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute one job and update one authoritative run record."""

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
    trigger_type = str(job_data.get("trigger_type") or "cron")
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
        # Validation failure means extraction did not produce a valid contract;
        # it must not be promoted to a successful Scheduler run.
        if job_type == "action_extract":
            extraction_status = (
                result.get("detail", {}).get("extraction_status")
                if isinstance(result.get("detail"), dict)
                else None
            )
            if extraction_status == "validation_error":
                success = False
                result["success"] = False
                result.setdefault("error", "action extraction validation failed")
        if not success:
            error_message = str(result.get("error") or "execution failed")
    except Exception as exc:
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
                next_run_at = scheduled_job.next_run_time.astimezone(
                    timezone.utc
                ).isoformat()
        except Exception:
            next_run_at = None

    job_store.update_job_run_info(
        job_id,
        last_run_at=started_at,
        next_run_at=next_run_at,
        last_error=error_message,
        increment_run_count=True,
    )
    if trigger_type == "date":
        trigger_runtime.finish_one_shot(
            job_id, success=success, error=error_message
        )
        next_run_at = None

    result["run_id"] = run_id
    result["run_status"] = run_status
    result["trigger_type"] = trigger_type
    result["next_run_at"] = next_run_at
    return result


def _add_job_to_scheduler_compat(
    scheduler,
    job_id: str,
    job_type: str = "",
    cron_expr: str = "",
    params: dict | None = None,
    timezone_str: str = "UTC",
    max_instances: int = 1,
    misfire_grace_time: int = 60,
    coalesce: bool = True,
) -> bool:
    """Compatibility adapter used by restore and resume paths.

    Persisted job data is authoritative; legacy positional arguments are
    intentionally ignored.
    """

    del (
        job_type,
        cron_expr,
        params,
        timezone_str,
        max_instances,
        misfire_grace_time,
        coalesce,
    )
    return trigger_runtime.add_to_scheduler(
        scheduler,
        job_id=job_id,
        execute_fn=execute_job_with_tracking,
    )


class SecureSchedulerHandler(legacy.SchedulerHandler):
    """Authenticated handler with corrected runs and production triggers."""

    server_version = "AgentForgeScheduler/3"

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

    def _create_job(self) -> None:
        if legacy._scheduler is None:
            self._send_json(503, {"error": "scheduler is unavailable"})
            return
        body = self._read_body()
        with legacy._jobs_lock:
            job_id = trigger_runtime.add_job(
                **trigger_runtime.job_from_request(body)
            )
            try:
                trigger_runtime.add_to_scheduler(
                    legacy._scheduler,
                    job_id=job_id,
                    execute_fn=execute_job_with_tracking,
                )
            except Exception as exc:
                from modules.scheduler import job_store

                job_store.update_job_status(job_id, "error")
                job_store.update_job_run_info(job_id, last_error=str(exc))
                raise
        from modules.scheduler import job_store

        stored = job_store.get_job(job_id) or {}
        self._send_json(
            201,
            {
                "job_id": job_id,
                "status": "created",
                "trigger_type": stored.get("trigger_type"),
                "timezone": stored.get("timezone"),
                "next_run_at": stored.get("next_run_at"),
            },
        )

    def _queue_manual_run(self, job_id: str) -> None:
        from modules.scheduler import job_store

        with legacy._jobs_lock:
            job_data = job_store.get_job(job_id)
            if job_data is None:
                self._send_json(404, {"error": "job not found"})
                return
            if job_data.get("status") in {"deleted", "completed"}:
                self._send_json(
                    409,
                    {"error": f"job is {job_data.get('status')}"},
                )
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

    def do_POST(self):  # noqa: N802
        if not self._authenticated():
            return
        path = self.path.split("?", 1)[0]
        try:
            if path == "/jobs":
                self._create_job()
                return
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "runs":
                self._queue_manual_run(parts[1])
                return
            return super().do_POST()
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


# Restore/resume code in the compatibility daemon resolves these globals at
# execution time, so production automatically uses the new trigger runtime.
legacy.execute_job_with_tracking = execute_job_with_tracking
legacy._add_job_to_scheduler = _add_job_to_scheduler_compat


def run_daemon() -> None:
    trigger_runtime.init_trigger_store()
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
    trigger_runtime.init_trigger_store()
    report = dict(legacy.health_check())
    report.update(
        {
            "authenticated": True,
            "token_fingerprint": token_fingerprint(_SERVICE_TOKEN),
            "manual_run_single_source": True,
            "supported_trigger_types": sorted(
                trigger_runtime.SUPPORTED_TRIGGER_TYPES
            ),
            "timezone_resolution": "request > AGENT_FORGE_USER_TZ > TZ > system > UTC",
        }
    )
    return report


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        print(json.dumps(health_check(), indent=2))
    else:
        run_daemon()
