#!/usr/bin/env python3
"""Loopback-only APScheduler service with atomic JSON persistence."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    Path(JOBS_FILE).parent.mkdir(parents=True, exist_ok=True)


def _quarantine_corrupt_jobs_file(path: Path) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        os.replace(path, backup)
    except OSError:
        pass


def _load_jobs() -> dict:
    """Load jobs; preserve malformed data in a timestamped quarantine file."""
    path = Path(JOBS_FILE)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"unable to read scheduler persistence: {exc}") from exc
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("jobs root must be an object")
        return data
    except (json.JSONDecodeError, UnicodeError, ValueError):
        _quarantine_corrupt_jobs_file(path)
        return {}


def _save_jobs(jobs: dict) -> None:
    if not isinstance(jobs, dict):
        raise TypeError("jobs must be a dictionary")
    _ensure_jobs_dir()
    target = Path(JOBS_FILE)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp", prefix="jobs_"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(jobs, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


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
            items = extract_from_file(report_path)
            result.update(
                {"success": True, "detail": {"extracted_count": len(items), "items": items}}
            )
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
    scheduler, job_id: str, job_type: str, cron_expr: str, params: dict
) -> bool:
    trigger = _cron_trigger(cron_expr)
    scheduler.add_job(
        func=_execute_job,
        trigger=trigger,
        args=[job_type, params],
        id=job_id,
        replace_existing=True,
    )
    return True


def init_scheduler():
    """Start APScheduler and restore each persisted active job."""
    global _scheduler
    if not _AP_SCHEDULER_AVAILABLE:
        return None
    if _scheduler is not None:
        return _scheduler
    scheduler = BackgroundScheduler()
    scheduler.start()
    try:
        jobs = _load_jobs()
    except Exception:
        scheduler.shutdown(wait=False)
        raise
    changed = False
    for job_id, job_data in jobs.items():
        if not isinstance(job_data, dict):
            jobs[job_id] = {
                "status": "error",
                "restore_error": "persisted job must be an object",
            }
            changed = True
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
            )
        except Exception as exc:
            print(f"[scheduler] failed to restore {job_id}: {exc}")
            job_data["status"] = "error"
            job_data["restore_error"] = str(exc)
            changed = True
    if changed:
        try:
            _save_jobs(jobs)
        except Exception:
            scheduler.shutdown(wait=False)
            raise
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
                if job_type not in JOB_TYPES:
                    self._send_json(400, {"error": f"invalid job_type: {job_type}"})
                    return
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                with _jobs_lock:
                    jobs = _load_jobs()
                    job_id = _generate_job_id()
                    _add_job_to_scheduler(_scheduler, job_id, job_type, cron_expr, params)
                    jobs[job_id] = {
                        "job_type": job_type,
                        "cron_expr": cron_expr,
                        "params": params,
                        "status": "active",
                        "created_at": datetime.now().isoformat(),
                    }
                    try:
                        _save_jobs(jobs)
                    except Exception:
                        _scheduler.remove_job(job_id)
                        raise
                self._send_json(201, {"job_id": job_id, "status": "created"})
                return

            parts = path.strip("/").split("/")
            if len(parts) != 3 or parts[0] != "jobs":
                self._send_json(404, {"error": "not found"})
                return
            job_id, operation = parts[1], parts[2]
            with _jobs_lock:
                jobs = _load_jobs()
                if job_id not in jobs:
                    self._send_json(404, {"error": "job not found"})
                    return
                job_data = jobs[job_id]
                if operation == "trigger":
                    result = _execute_job(job_data["job_type"], job_data.get("params", {}))
                    self._send_json(200 if result["success"] else 500, result)
                    return
                if _scheduler is None:
                    self._send_json(503, {"error": "scheduler is unavailable"})
                    return
                previous_status = job_data.get("status")
                if operation == "pause":
                    _scheduler.pause_job(job_id)
                    job_data["status"] = "paused"
                    try:
                        _save_jobs(jobs)
                    except Exception:
                        job_data["status"] = previous_status
                        _scheduler.resume_job(job_id)
                        raise
                elif operation == "resume":
                    added = False
                    try:
                        _scheduler.resume_job(job_id)
                    except JobLookupError:
                        _add_job_to_scheduler(
                            _scheduler,
                            job_id,
                            job_data["job_type"],
                            job_data["cron_expr"],
                            job_data.get("params", {}),
                        )
                        added = True
                    job_data["status"] = "active"
                    try:
                        _save_jobs(jobs)
                    except Exception:
                        job_data["status"] = previous_status
                        if added:
                            _scheduler.remove_job(job_id)
                        elif previous_status == "paused":
                            _scheduler.pause_job(job_id)
                        raise
                else:
                    self._send_json(404, {"error": "not found"})
                    return
            self._send_json(200, {"job_id": job_id, "status": job_data["status"]})
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
                jobs = _load_jobs()
                if job_id not in jobs:
                    self._send_json(404, {"error": "job not found"})
                    return
                removed = jobs[job_id]
                if _scheduler is not None:
                    try:
                        _scheduler.remove_job(job_id)
                    except JobLookupError:
                        pass
                jobs.pop(job_id)
                try:
                    _save_jobs(jobs)
                except Exception:
                    jobs[job_id] = removed
                    if _scheduler is not None and removed.get("status") in {
                        "active",
                        "paused",
                    }:
                        _add_job_to_scheduler(
                            _scheduler,
                            job_id,
                            removed["job_type"],
                            removed["cron_expr"],
                            removed.get("params", {}),
                        )
                        if removed.get("status") == "paused":
                            _scheduler.pause_job(job_id)
                    raise
            self._send_json(200, {"job_id": job_id, "status": "deleted"})
        except (TypeError, ValueError) as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})


def run_daemon() -> None:
    print(f"[scheduler] starting on {DAEMON_HOST}:{DAEMON_PORT}")
    print(f"[scheduler] APScheduler available: {_AP_SCHEDULER_AVAILABLE}")
    init_scheduler()
    server = HTTPServer((DAEMON_HOST, DAEMON_PORT), SchedulerHandler)
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
    return {
        "apscheduler_available": _AP_SCHEDULER_AVAILABLE,
        "apscheduler_version": package_version,
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
