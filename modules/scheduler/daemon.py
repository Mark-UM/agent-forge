#!/usr/bin/env python3
"""调度器守护进程 — APScheduler 适配层，HTTP API + jobs.json 持久化。

Features:
- 端口 9225，HTTP API（参照 browser daemon 模式）
- APScheduler 3.x BackgroundScheduler
- jobs.json 原子持久化（tmp + os.replace）
- 仅监听 127.0.0.1（安全）
- Job 类型：file_reindex / report_collect / memory_review / custom

Usage:
    python -m modules.scheduler.daemon          # 启动 daemon
    python -m modules.scheduler.daemon --check  # 健康检查
"""
import sys
import os
import json
import time
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ── vendor/python-libs 路径注入 ────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENDOR_LIBS = os.path.join(_PROJECT_ROOT, "vendor", "python-libs")
if os.path.isdir(_VENDOR_LIBS) and _VENDOR_LIBS not in sys.path:
    sys.path.insert(0, _VENDOR_LIBS)

# ── APScheduler import（lazy，允许模块在未安装时 import 不崩溃）─
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _AP_SCHEDULER_AVAILABLE = True
except ImportError:
    _AP_SCHEDULER_AVAILABLE = False
    BackgroundScheduler = None
    CronTrigger = None

# ── 配置 ────────────────────────────────────────────────────────
DAEMON_PORT = 9225
DAEMON_HOST = "127.0.0.1"
JOBS_FILE = os.path.join(_PROJECT_ROOT, "_runtime", "scheduler", "jobs.json")

# 允许的 job 类型
JOB_TYPES = frozenset({
    "file_reindex",      # 调用 file_indexer 重建索引
    "report_collect",    # 调用 agent_wrapper.run_collection_pipeline
    "memory_review",     # 扫描 memory 文件，提取待办/截止日期
    "action_extract",    # 从指定报告中抽取待办入日程（C3 新增）
    "pattern_extract",   # 预留：v2.5 pattern_extractor 接入点
    "custom",            # 调用白名单 Python 模块函数
})

# custom job 允许调用的模块白名单
CUSTOM_MODULE_WHITELIST = frozenset({
    "modules.orchestrator.agent_wrapper",
    "modules.orchestrator.file_indexer",
    "modules.memory.hook",
})

# ── 全局状态 ────────────────────────────────────────────────────
_scheduler = None
_jobs_lock = threading.Lock()


# ── jobs.json 持久化 ────────────────────────────────────────────


def _ensure_jobs_dir():
    os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)


def _load_jobs() -> dict:
    """从 jobs.json 加载已持久化的任务。"""
    if not os.path.exists(JOBS_FILE):
        return {}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_jobs(jobs: dict) -> None:
    """原子写入 jobs.json（tmp + os.replace）。"""
    _ensure_jobs_dir()
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(JOBS_FILE), suffix=".tmp", prefix="jobs_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, JOBS_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ── Job 执行器 ──────────────────────────────────────────────────


def _execute_job(job_type: str, params: dict) -> dict:
    """执行 job，返回结果 dict。"""
    result = {"job_type": job_type, "executed_at": datetime.now().isoformat(), "success": False}

    try:
        if job_type == "file_reindex":
            from modules.orchestrator.file_indexer import index_directory
            root = params.get("root", os.path.join(_PROJECT_ROOT, "_data", "memory"))
            res = index_directory(root=root)
            result.update({"success": True, "detail": res})

        elif job_type == "report_collect":
            from modules.orchestrator.agent_wrapper import run_collection_pipeline
            target = params.get("target", "")
            output_path = params.get("output_path", "")
            res = run_collection_pipeline(target=target, output_path=output_path)
            result.update({"success": True, "detail": res})

        elif job_type == "memory_review":
            result.update({"success": True, "detail": "memory_review not yet implemented"})

        elif job_type == "action_extract":
            from modules.orchestrator.action_extractor import extract_from_file
            report_path = params.get("report_path", "")
            if not report_path:
                result["error"] = "report_path is required for action_extract"
                return result
            items = extract_from_file(report_path)
            result.update({
                "success": True,
                "detail": {"extracted_count": len(items), "items": items},
            })

        elif job_type == "custom":
            module_name = params.get("module", "")
            func_name = params.get("function", "")
            if module_name not in CUSTOM_MODULE_WHITELIST:
                result["error"] = f"module '{module_name}' not in whitelist"
                return result
            import importlib
            mod = importlib.import_module(module_name)
            func = getattr(mod, func_name, None)
            if func is None:
                result["error"] = f"function '{func_name}' not found in {module_name}"
                return result
            call_params = params.get("params", {})
            res = func(**call_params)
            result.update({"success": True, "detail": str(res)})

        else:
            result["error"] = f"unknown job_type: {job_type}"

    except Exception as e:
        result["error"] = str(e)

    return result


# ── APScheduler 适配 ────────────────────────────────────────────


def _generate_job_id() -> str:
    """生成唯一 job ID。"""
    import uuid
    return f"job_{uuid.uuid4().hex[:12]}"


def _add_job_to_scheduler(scheduler, job_id: str, job_type: str, cron_expr: str, params: dict) -> bool:
    """向 APScheduler 添加任务。"""
    if not _AP_SCHEDULER_AVAILABLE:
        return False

    # 解析 cron 表达式（5 字段：minute hour day month weekday）
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式必须是 5 字段: {cron_expr}")

    trigger = CronTrigger(
        minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4]
    )

    scheduler.add_job(
        func=_execute_job,
        trigger=trigger,
        args=[job_type, params],
        id=job_id,
        replace_existing=True,
    )
    return True


def init_scheduler():
    """初始化 APScheduler 并恢复持久化的任务。"""
    global _scheduler

    if not _AP_SCHEDULER_AVAILABLE:
        print("[scheduler] WARNING: APScheduler not installed, running in stub mode")
        return None

    _scheduler = BackgroundScheduler()
    _scheduler.start()

    # 恢复持久化的任务
    jobs = _load_jobs()
    for job_id, job_data in jobs.items():
        if job_data.get("status") == "active":
            try:
                _add_job_to_scheduler(
                    _scheduler,
                    job_id,
                    job_data["job_type"],
                    job_data["cron_expr"],
                    job_data.get("params", {}),
                )
            except Exception as e:
                print(f"[scheduler] WARNING: failed to restore job {job_id}: {e}")

    return _scheduler


def shutdown_scheduler():
    """关闭调度器。"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# ── HTTP API ────────────────────────────────────────────────────


class SchedulerHandler(BaseHTTPRequestHandler):
    """调度器 HTTP API handler。"""

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0 or length > 1_000_000:  # 1MB 限制
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/status":
            jobs = _load_jobs()
            active_count = sum(1 for j in jobs.values() if j.get("status") == "active")
            next_runs = {}
            if _scheduler and _AP_SCHEDULER_AVAILABLE:
                for job in _scheduler.get_jobs():
                    next_runs[job.id] = job.next_run_time.isoformat() if job.next_run_time else None
            self._send_json(200, {
                "status": "running" if _scheduler else "stub",
                "apscheduler_available": _AP_SCHEDULER_AVAILABLE,
                "total_jobs": len(jobs),
                "active_jobs": active_count,
                "next_runs": next_runs,
            })

        elif path == "/jobs":
            jobs = _load_jobs()
            self._send_json(200, {"jobs": jobs})

        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/jobs":
            body = self._read_body()
            job_type = body.get("job_type", "")
            cron_expr = body.get("cron_expr", "")
            params = body.get("params", {})

            if job_type not in JOB_TYPES:
                self._send_json(400, {"error": f"invalid job_type: {job_type}"})
                return

            with _jobs_lock:
                jobs = _load_jobs()
                job_id = _generate_job_id()

                if _scheduler and _AP_SCHEDULER_AVAILABLE:
                    try:
                        _add_job_to_scheduler(_scheduler, job_id, job_type, cron_expr, params)
                    except Exception as e:
                        self._send_json(400, {"error": str(e)})
                        return

                jobs[job_id] = {
                    "job_type": job_type,
                    "cron_expr": cron_expr,
                    "params": params,
                    "status": "active",
                    "created_at": datetime.now().isoformat(),
                }
                _save_jobs(jobs)

            self._send_json(201, {"job_id": job_id, "status": "created"})

        elif path.startswith("/jobs/") and path.endswith("/trigger"):
            job_id = path.split("/")[2]
            with _jobs_lock:
                jobs = _load_jobs()
                if job_id not in jobs:
                    self._send_json(404, {"error": "job not found"})
                    return
                job_data = jobs[job_id]
            # 手动触发（不经过 scheduler）
            result = _execute_job(job_data["job_type"], job_data.get("params", {}))
            self._send_json(200, result)

        elif path.startswith("/jobs/") and path.endswith("/pause"):
            job_id = path.split("/")[2]
            with _jobs_lock:
                jobs = _load_jobs()
                if job_id not in jobs:
                    self._send_json(404, {"error": "job not found"})
                    return
                jobs[job_id]["status"] = "paused"
                _save_jobs(jobs)
                if _scheduler and _AP_SCHEDULER_AVAILABLE:
                    try:
                        _scheduler.pause_job(job_id)
                    except Exception:
                        pass
            self._send_json(200, {"job_id": job_id, "status": "paused"})

        elif path.startswith("/jobs/") and path.endswith("/resume"):
            job_id = path.split("/")[2]
            with _jobs_lock:
                jobs = _load_jobs()
                if job_id not in jobs:
                    self._send_json(404, {"error": "job not found"})
                    return
                jobs[job_id]["status"] = "active"
                _save_jobs(jobs)
                if _scheduler and _AP_SCHEDULER_AVAILABLE:
                    try:
                        _scheduler.resume_job(job_id)
                    except Exception:
                        pass
            self._send_json(200, {"job_id": job_id, "status": "active"})

        elif path == "/close":
            shutdown_scheduler()
            self._send_json(200, {"status": "shutting down"})
            threading.Thread(target=lambda: time.sleep(0.5) or os._exit(0)).start()

        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        path = self.path.split("?")[0]

        if path.startswith("/jobs/"):
            job_id = path.split("/")[2]
            with _jobs_lock:
                jobs = _load_jobs()
                if job_id not in jobs:
                    self._send_json(404, {"error": "job not found"})
                    return
                del jobs[job_id]
                _save_jobs(jobs)
                if _scheduler and _AP_SCHEDULER_AVAILABLE:
                    try:
                        _scheduler.remove_job(job_id)
                    except Exception:
                        pass
            self._send_json(200, {"job_id": job_id, "status": "deleted"})
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        """静默日志（避免 stdout 污染）。"""
        pass


def run_daemon():
    """启动调度器 daemon。"""
    print(f"[scheduler] starting on {DAEMON_HOST}:{DAEMON_PORT}")
    print(f"[scheduler] APScheduler available: {_AP_SCHEDULER_AVAILABLE}")
    print(f"[scheduler] jobs file: {JOBS_FILE}")

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
    """健康检查（不启动 daemon，仅检查依赖和配置）。"""
    return {
        "apscheduler_available": _AP_SCHEDULER_AVAILABLE,
        "apscheduler_version": _AP_SCHEDULER_AVAILABLE and __import__("apscheduler").__version__,
        "jobs_file": JOBS_FILE,
        "jobs_file_exists": os.path.exists(JOBS_FILE),
        "daemon_port": DAEMON_PORT,
        "job_types": list(JOB_TYPES),
    }


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(json.dumps(health_check(), indent=2))
    else:
        run_daemon()
