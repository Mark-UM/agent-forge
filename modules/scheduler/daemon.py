#!/usr/bin/env python3
"""Loopback-only APScheduler service with SQLite persistence (SC1 fix).

SC1: Replaced dual state source (jobs.json + SQLite schedules) with a single
SQLite authority. The `scheduler_jobs` table in `_runtime/mcp-sqlite.db` is
the only source of truth for cron jobs. Migration from the legacy jobs.json
is performed automatically on init (idempotent, with backup).

SC3: Uses ThreadingHTTPServer instead of HTTPServer to avoid blocking on
long-running job triggers.

Backward compatibility:
    - JOBS_FILE constant retained (deprecated, for health_check display only)
    - _load_jobs() reads from SQLite, returns legacy dict format
    - _save_jobs() is a deprecated no-op (SQLite is authoritative)
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import uuid
import warnings
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from modules.bootstrap.dependencies import activate_vendor_path

activate_vendor_path()

try:
    from apscheduler.jobstores.base import JobLookupError
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    _AP_SCHEDULER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    JobLookupError = LookupError
    BackgroundScheduler = None
    CronTrigger = None
    _AP_SCHEDULER_AVAILABLE = False

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(os.environ.get("AGENT_FORGE_SCHEDULER_PORT", "9225"))
JOBS_FILE = str(_PROJECT_ROOT / "_runtime" / "scheduler" / "jobs.json")
MAX_BODY_BYTES = 1_000_000

JOB_TYPES = frozenset(
    {"file_reindex", "report_collect", "memory_review", "action_extract", "custom"}
)
CUSTOM_CALLABLE_WHITELIST = {
    "modules.orchestrator.agent_wrapper": frozenset({"run_collection_pipeline"}),
    "modules.orchestrator.file_indexer": frozenset({"index_directory"}),
    "modules.memory.hook": frozenset({"review_memory", "check_memory_health"}),
}
CUSTOM_MODULE_WHITELIST = frozenset(CUSTOM_CALLABLE_WHITELIST)

_scheduler = None
_jobs_lock = threading.RLock()


def _ensure_jobs_dir() -> None:
    """Deprecated no-op. SQLite is the authority now (SC1 fix)."""
    # Kept for backward compatibility with tests that patch this function.
    # Previously created the jobs.json directory; now a no-op since SQLite
    # handles its own directory creation via job_store._get_conn().
    pass


def _quarantine_corrupt_jobs_file(path: Path) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        os.replace(path, backup)
    except OSError:
        pass


def _load_jobs() -> dict:
    """SC1 fix: Load jobs from SQLite, returning legacy dict format.

    Reads from the `scheduler_jobs` table (via job_store module) and returns
    a dict mapping job_id → {job_type, cron_expr, params, status, created_at}
    for backward compatibility with the HTTP /status and /jobs endpoints.

    Returns:
        dict: Empty dict if no jobs or job_store unavailable.
    """
    try:
        from modules.scheduler import job_store
        jobs_list = job_store.list_jobs(limit=1000)
    except (ImportError, Exception):
        return {}

    # Convert SQLite rows to legacy dict format
    result = {}
    for job in jobs_list:
        job_id = job["job_id"]
        result[job_id] = {
            "job_type": job["job_type"],
            "cron_expr": job.get("cron_expr") or "",
            "params": job.get("payload") or {},
            "status": job["status"],
            "created_at": job["created_at"],
            # R2-4.3: Include job config fields so init_scheduler can pass
            # them through to APScheduler.add_job().
            "timezone": job.get("timezone") or "UTC",
            "max_instances": int(job.get("max_instances", 1)),
            "misfire_grace_time": int(job.get("misfire_grace_time", 60)),
            "coalesce": bool(job.get("coalesce", 1)),
        }
    return result


def _save_jobs(jobs: dict) -> None:
    """Deprecated: SQLite is the authoritative store (SC1 fix).

    This function is kept for backward compatibility but is a no-op.
    All job state changes go through job_store functions which write
    directly to SQLite.

    Args:
        jobs: Legacy jobs dict (ignored — SQLite is authoritative)
    """
    warnings.warn(
        "_save_jobs() is deprecated (SC1 fix): SQLite is the authoritative "
        "store. Use job_store.add_job() / update_job_status() / delete_job() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # No-op: SQLite is the source of truth
    return


def _execute_job(job_type: str, params: dict) -> dict:
    result = {
        "job_type": job_type,
        "executed_at": datetime.now().isoformat(),
        "success": False,
    }
    if not isinstance(params, dict):
        result["error"] = "params must be an object"
        return result
    try:
        if job_type == "file_reindex":
            from modules.orchestrator.file_indexer import index_directory

            root = params.get("root", str(_PROJECT_ROOT / "_data" / "memory"))
            detail = index_directory(root=root)
            result.update({"success": bool(detail.get("success", True)), "detail": detail})
        elif job_type == "report_collect":
            from modules.orchestrator.agent_wrapper import run_collection_pipeline

            detail = run_collection_pipeline(
                target=params.get("target", ""),
                task=params.get("task", "Collect and summarize this source."),
                output_path=params.get("output_path", ""),
            )
            result.update({"success": bool(detail.get("success")), "detail": detail})
        elif job_type == "memory_review":
            from modules.memory.hook import review_memory

            detail = review_memory(output_path=params.get("output_path") or None)
            result.update({"success": bool(detail.get("success")), "detail": detail})
        elif job_type == "action_extract":
            from modules.orchestrator.action_extractor import extract_from_file

            report_path = params.get("report_path", "")
            if not report_path:
                result["error"] = "report_path is required for action_extract"
                return result
            # R2-5.2: extract_from_file now returns a dict with status/items/error
            extraction_result = extract_from_file(report_path)
            status = extraction_result.get("status", "ok")
            items = extraction_result.get("items", [])
            success = status in ("ok", "no_actions", "validation_error")
            result.update({
                "success": success,
                "detail": {
                    "extraction_status": status,
                    "extracted_count": len(items),
                    "items": items,
                    "error": extraction_result.get("error"),
                },
            })
        elif job_type == "custom":
            module_name = params.get("module", "")
            function_name = params.get("function", "")
            if function_name not in CUSTOM_CALLABLE_WHITELIST.get(module_name, ()):
                result["error"] = (
                    f"callable '{module_name}.{function_name}' is not in whitelist"
                )
                return result
            function = getattr(importlib.import_module(module_name), function_name)
            call_params = params.get("params", {})
            if not isinstance(call_params, dict):
                result["error"] = "custom params must be an object"
                return result
            detail = function(**call_params)
            succeeded = bool(detail.get("success", True)) if isinstance(detail, dict) else True
            result.update({"success": succeeded, "detail": detail})
        else:
            result["error"] = f"unknown job_type: {job_type}"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _generate_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _cron_trigger(cron_expr: str):
    if not isinstance(cron_expr, str):
        raise ValueError("cron expression must be a string")
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression must contain 5 fields: {cron_expr}")
    if not _AP_SCHEDULER_AVAILABLE:
        raise RuntimeError("APScheduler is unavailable")
    return CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )


def _add_job_to_scheduler(
    scheduler, job_id: str, job_type: str, cron_expr: str, params: dict,
    timezone_str: str = "UTC",
    max_instances: int = 1,
    misfire_grace_time: int = 60,
    coalesce: bool = True,
) -> bool:
    """R2-4.3: Pass job config (timezone, max_instances, misfire_grace_time,
    coalesce) through to APScheduler.add_job().

    R2-4.4: The scheduled function is `execute_job_with_tracking`, NOT
    `_execute_job` directly. The wrapper creates a JobRun row, marks it
    RUNNING, calls `_execute_job`, then marks SUCCEEDED / FAILED.
    """
    trigger = _cron_trigger(cron_expr)
    scheduler.add_job(
        func=execute_job_with_tracking,
        trigger=trigger,
        args=[job_id, "cron"],
        id=job_id,
        replace_existing=True,
        timezone=timezone_str,
        max_instances=max_instances,
        misfire_grace_time=misfire_grace_time,
        coalesce=coalesce,
    )
    return True


def execute_job_with_tracking(job_id: str, triggered_by: str = "cron") -> dict:
    """R2-4.4: Execution wrapper that records a JobRun for every execution.

    APScheduler must call THIS function, not `_execute_job` directly. The
    wrapper:

    1. Creates a job_runs row (status=QUEUED)
    2. Marks it RUNNING + updates last_run_at on the job
    3. Calls `_execute_job(job_type, params)`
    4. Updates the run row to SUCCEEDED / FAILED
    5. Updates run_count + last_error + next_run_at on the job

    Args:
        job_id: The scheduler job ID.
        triggered_by: 'cron' | 'manual' | 'test'.

    Returns:
        dict: The execution result from `_execute_job`, augmented with
              run_id and run_status.
    """
    import time as _time
    from modules.scheduler import job_store

    job_data = job_store.get_job(job_id)
    if job_data is None:
        return {
            "job_id": job_id,
            "success": False,
            "error": f"job not found: {job_id}",
        }

    job_type = job_data["job_type"]
    params = job_data.get("payload") or {}

    # 1. Create JobRun row (QUEUED)
    run_id = job_store.create_job_run(job_id, triggered_by=triggered_by)

    # 2. Mark RUNNING + update last_run_at
    started_at = datetime.now(timezone.utc).isoformat()
    run_count_at_start = job_data.get("run_count", 0)
    job_store.update_job_run(
        run_id, status="running", started_at=started_at,
        run_count_at_start=run_count_at_start,
    )
    job_store.update_job_run_info(job_id, last_run_at=started_at)

    t0 = _time.perf_counter()
    error_msg = None
    try:
        result = _execute_job(job_type, params)
        success = bool(result.get("success", False))
        if not success:
            error_msg = result.get("error", "execution failed")
    except Exception as exc:
        result = {
            "job_type": job_type,
            "executed_at": started_at,
            "success": False,
            "error": str(exc),
        }
        success = False
        error_msg = str(exc)

    duration_ms = (_time.perf_counter() - t0) * 1000.0
    ended_at = datetime.now(timezone.utc).isoformat()

    # 4. Update run row
    job_store.update_job_run(
        run_id,
        status="succeeded" if success else "failed",
        ended_at=ended_at,
        duration_ms=duration_ms,
        error=error_msg,
    )

    # 5. Update job run info
    next_run_at = None
    if _scheduler is not None:
        try:
            ap_job = _scheduler.get_job(job_id)
            if ap_job is not None and ap_job.next_run_time is not None:
                next_run_at = ap_job.next_run_time.isoformat()
        except Exception:
            pass
    job_store.update_job_run_info(
        job_id,
        last_run_at=started_at,
        next_run_at=next_run_at,
        last_error=error_msg,
        increment_run_count=True,
    )

    # Augment result with run tracking
    result["run_id"] = run_id
    result["run_status"] = "succeeded" if success else "failed"
    return result


def init_scheduler():
    """Start APScheduler, migrate legacy jobs.json, and restore active jobs.

    SC1 fix: Migrates from jobs.json to SQLite first (idempotent), then
    loads jobs from the SQLite `scheduler_jobs` table.
    """
    global _scheduler
    if not _AP_SCHEDULER_AVAILABLE:
        return None
    if _scheduler is not None:
        return _scheduler

    # SC1: Migrate legacy jobs.json → SQLite (idempotent, with backup)
    try:
        from modules.scheduler import job_store
        job_store.init_job_store()
        migration_report = job_store.migrate_from_json()
        if migration_report["migrated_count"] > 0:
            print(f"[scheduler] SC1 migration: {migration_report['migrated_count']} "
                  f"jobs migrated from jobs.json to SQLite")
    except Exception as exc:
        print(f"[scheduler] SC1 migration warning: {exc}")

    scheduler = BackgroundScheduler()
    scheduler.start()

    # Load jobs from SQLite (single source of truth)
    try:
        jobs = _load_jobs()
    except Exception:
        scheduler.shutdown(wait=False)
        raise

    for job_id, job_data in jobs.items():
        if not isinstance(job_data, dict):
            continue
        if job_data.get("status") != "active":
            continue
        try:
            _add_job_to_scheduler(
                scheduler,
                job_id,
                job_data["job_type"],
                job_data["cron_expr"],
                job_data.get("params", {}),
                timezone_str=job_data.get("timezone") or "UTC",
                max_instances=int(job_data.get("max_instances", 1)),
                misfire_grace_time=int(job_data.get("misfire_grace_time", 60)),
                coalesce=bool(job_data.get("coalesce", 1)),
            )
        except Exception as exc:
            print(f"[scheduler] failed to restore {job_id}: {exc}")
            # Update job status to error in SQLite
            try:
                from modules.scheduler import job_store
                job_store.update_job_status(job_id, "error")
                job_store.update_job_run_info(job_id, last_error=str(exc))
            except Exception:
                pass

    _scheduler = scheduler
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


class SchedulerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def _send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if not 0 < length <= MAX_BODY_BYTES:
            raise ValueError(f"Request body must be 1..{MAX_BODY_BYTES} bytes")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/status":
            jobs = _load_jobs()
            next_runs = {}
            if _scheduler is not None:
                for job in _scheduler.get_jobs():
                    next_runs[job.id] = (
                        job.next_run_time.isoformat() if job.next_run_time else None
                    )
            self._send_json(
                200,
                {
                    "status": "running" if _scheduler is not None else "degraded",
                    "apscheduler_available": _AP_SCHEDULER_AVAILABLE,
                    "total_jobs": len(jobs),
                    "active_jobs": sum(
                        job.get("status") == "active" for job in jobs.values()
                    ),
                    "next_runs": next_runs,
                },
            )
        elif path == "/jobs":
            self._send_json(200, {"jobs": _load_jobs()})
        else:
            # R2-4.5: GET /runs/{run_id} — poll a manual trigger's status.
            parts = path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "runs":
                run_id = parts[1]
                from modules.scheduler import job_store
                run = job_store.get_job_run(run_id)
                if run is None:
                    self._send_json(404, {"error": "run not found"})
                    return
                self._send_json(200, {"run": run})
                return
            # GET /jobs/{job_id}/runs — list runs for a job
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "runs":
                job_id = parts[1]
                from modules.scheduler import job_store
                runs = job_store.list_job_runs(job_id=job_id, limit=50)
                self._send_json(200, {"job_id": job_id, "runs": runs})
                return
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            if path == "/close":
                shutdown_scheduler()
                self._send_json(200, {"status": "shutting down"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/jobs":
                if _scheduler is None:
                    self._send_json(503, {"error": "scheduler is unavailable"})
                    return
                body = self._read_body()
                job_type = body.get("job_type", "")
                cron_expr = body.get("cron_expr", "")
                params = body.get("params", {})
                trigger_type = body.get("trigger_type", "cron")
                # R2-4.3: Accept job config from the request body.
                timezone_str = body.get("timezone", "UTC")
                max_instances = int(body.get("max_instances", 1))
                misfire_grace_time = int(body.get("misfire_grace_time", 60))
                coalesce = bool(body.get("coalesce", True))
                if job_type not in JOB_TYPES:
                    self._send_json(400, {"error": f"invalid job_type: {job_type}"})
                    return
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                with _jobs_lock:
                    from modules.scheduler import job_store
                    # R2-4.2: job_store.add_job enforces the trigger whitelist
                    # (rejects 'date' / 'interval' with UnsupportedTriggerError).
                    try:
                        job_id = job_store.add_job(
                            job_type=job_type,
                            cron_expr=cron_expr,
                            params=params,
                            trigger_type=trigger_type,
                            status="active",
                            timezone_str=timezone_str,
                            max_instances=max_instances,
                            misfire_grace_time=misfire_grace_time,
                            coalesce=coalesce,
                        )
                    except job_store.UnsupportedTriggerError as exc:
                        self._send_json(400, {
                            "error": "UNSUPPORTED_TRIGGER",
                            "message": str(exc),
                            "supported": sorted(job_store.SUPPORTED_TRIGGER_TYPES),
                        })
                        return
                    try:
                        _add_job_to_scheduler(
                            _scheduler, job_id, job_type, cron_expr, params,
                            timezone_str=timezone_str,
                            max_instances=max_instances,
                            misfire_grace_time=misfire_grace_time,
                            coalesce=coalesce,
                        )
                    except Exception:
                        # Rollback: mark job as error in SQLite
                        job_store.update_job_status(job_id, "error")
                        raise
                self._send_json(201, {"job_id": job_id, "status": "created"})
                return

            parts = path.strip("/").split("/")
            # R2-4.5: POST /jobs/{job_id}/runs — async manual trigger.
            # Returns run_id + status=QUEUED immediately. The job runs in a
            # background thread. Use GET /runs/{run_id} to poll status.
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "runs":
                job_id = parts[1]
                with _jobs_lock:
                    from modules.scheduler import job_store
                    job_data = job_store.get_job(job_id)
                    if job_data is None:
                        self._send_json(404, {"error": "job not found"})
                        return
                    # Create the JobRun row (QUEUED) immediately
                    run_id = job_store.create_job_run(job_id, triggered_by="manual")
                # Run in a background thread (do NOT hold the lock during execution)
                worker = threading.Thread(
                    target=execute_job_with_tracking,
                    args=(job_id, "manual"),
                    daemon=True,
                    name=f"job-run-{run_id}",
                )
                worker.start()
                self._send_json(202, {
                    "job_id": job_id,
                    "run_id": run_id,
                    "status": "queued",
                })
                return

            # Legacy: POST /jobs/{job_id}/trigger (deprecated, kept for compat)
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "trigger":
                job_id = parts[1]
                with _jobs_lock:
                    from modules.scheduler import job_store
                    job_data = job_store.get_job(job_id)
                    if job_data is None:
                        self._send_json(404, {"error": "job not found"})
                        return
                    # R2-4.4: Use the tracking wrapper (creates JobRun row).
                    result = execute_job_with_tracking(job_id, triggered_by="manual")
                self._send_json(200 if result.get("success") else 500, result)
                return

            if len(parts) != 3 or parts[0] != "jobs":
                self._send_json(404, {"error": "not found"})
                return
            job_id, operation = parts[1], parts[2]
            with _jobs_lock:
                from modules.scheduler import job_store
                # SC1: Read from SQLite
                job_data = job_store.get_job(job_id)
                if job_data is None:
                    self._send_json(404, {"error": "job not found"})
                    return
                if _scheduler is None:
                    self._send_json(503, {"error": "scheduler is unavailable"})
                    return
                if operation == "pause":
                    _scheduler.pause_job(job_id)
                    job_store.update_job_status(job_id, "paused")
                elif operation == "resume":
                    added = False
                    try:
                        _scheduler.resume_job(job_id)
                    except JobLookupError:
                        _add_job_to_scheduler(
                            _scheduler,
                            job_id,
                            job_data["job_type"],
                            job_data.get("cron_expr") or "",
                            job_data.get("payload") or {},
                            timezone_str=job_data.get("timezone") or "UTC",
                            max_instances=int(job_data.get("max_instances", 1)),
                            misfire_grace_time=int(job_data.get("misfire_grace_time", 60)),
                            coalesce=bool(job_data.get("coalesce", 1)),
                        )
                        added = True
                    job_store.update_job_status(job_id, "active")
                else:
                    self._send_json(404, {"error": "not found"})
                    return
            # Read current status from SQLite for the response
            current = job_store.get_job(job_id)
            current_status = current["status"] if current else "unknown"
            self._send_json(200, {"job_id": job_id, "status": current_status})
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def do_DELETE(self):
        try:
            path = self.path.split("?", 1)[0]
            parts = path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != "jobs":
                self._send_json(404, {"error": "not found"})
                return
            job_id = parts[1]
            with _jobs_lock:
                from modules.scheduler import job_store
                # SC1: Read from SQLite
                job_data = job_store.get_job(job_id)
                if job_data is None:
                    self._send_json(404, {"error": "job not found"})
                    return
                # Remove from APScheduler
                if _scheduler is not None:
                    try:
                        _scheduler.remove_job(job_id)
                    except JobLookupError:
                        pass
                # SC1: Soft-delete in SQLite
                job_store.delete_job(job_id)
            self._send_json(200, {"job_id": job_id, "status": "deleted"})
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def run_daemon() -> None:
    print(f"[scheduler] starting on {DAEMON_HOST}:{DAEMON_PORT}")
    print(f"[scheduler] APScheduler available: {_AP_SCHEDULER_AVAILABLE}")
    init_scheduler()
    # SC3: Use ThreadingHTTPServer to avoid blocking on long job triggers
    server = ThreadingHTTPServer((DAEMON_HOST, DAEMON_PORT), SchedulerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_scheduler()
        server.server_close()


def health_check() -> dict:
    try:
        package_version = version("APScheduler") if _AP_SCHEDULER_AVAILABLE else None
    except PackageNotFoundError:
        package_version = None
    # SC1: Report SQLite as the state source
    try:
        from modules.scheduler import job_store
        job_store.init_job_store()
        migration_version = job_store.get_migration_version()
        with job_store._get_conn() as conn:
            total_jobs = conn.execute(
                "SELECT COUNT(*) FROM scheduler_jobs WHERE status != 'deleted'"
            ).fetchone()[0]
            active_jobs = conn.execute(
                "SELECT COUNT(*) FROM scheduler_jobs WHERE status = 'active'"
            ).fetchone()[0]
    except Exception:
        migration_version = None
        total_jobs = 0
        active_jobs = 0
    return {
        "apscheduler_available": _AP_SCHEDULER_AVAILABLE,
        "apscheduler_version": package_version,
        "state_source": "sqlite",
        "db_path": str(_PROJECT_ROOT / "_runtime" / "mcp-sqlite.db"),
        "migration_version": migration_version,
        "total_jobs": total_jobs,
        "active_jobs": active_jobs,
        # Deprecated fields (kept for backward compat)
        "jobs_file": JOBS_FILE,
        "jobs_file_exists": Path(JOBS_FILE).exists(),
        "daemon_port": DAEMON_PORT,
        "job_types": sorted(JOB_TYPES),
    }


if __name__ == "__main__":
    import sys

    if "--check" in sys.argv:
        print(json.dumps(health_check(), indent=2))
    else:
        run_daemon()
