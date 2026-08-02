#!/usr/bin/env python3
"""B1: Agent Wrapper — browser-use 适配层，端到端收集管道。

Architecture:
    1. Primary: browser-use Agent（LLM 驱动，自动决策）
    2. Fallback: browser daemon HTTP API（手动 navigate → screenshot）
    3. Vision: modules/vision/recognize.py 识别截图
    4. Summarize: modules/search/aggregator.py 生成 Markdown
    5. Write: 原子写入 output_path

Pipeline:
    target URL → navigate → wait → screenshot → OCR/Vision → summarize → write file

Usage:
    from modules.orchestrator.agent_wrapper import run_collection_pipeline

    result = run_collection_pipeline(
        target="https://example.com",
        output_path="_runtime/reports/example.md"
    )
"""
import os
import sys
import json
import time
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_VENDOR_LIBS = _PROJECT_ROOT / "vendor" / "python-libs"
if _VENDOR_LIBS.is_dir() and str(_VENDOR_LIBS) not in sys.path:
    sys.path.insert(0, str(_VENDOR_LIBS))

_REPORTS_DIR = _PROJECT_ROOT / "_runtime" / "reports"

# ── Lazy browser-use import ────────────────────────────────────
_BROWSER_USE_AVAILABLE = None


def _check_browser_use() -> bool:
    """Check if browser-use is available. Lazy import + cache."""
    global _BROWSER_USE_AVAILABLE
    if _BROWSER_USE_AVAILABLE is not None:
        return _BROWSER_USE_AVAILABLE
    try:
        import browser_use  # noqa: F401
        _BROWSER_USE_AVAILABLE = True
    except ImportError:
        _BROWSER_USE_AVAILABLE = False
    return _BROWSER_USE_AVAILABLE


# ── Browser daemon client (fallback) ───────────────────────────

BROWSER_DAEMON_PORT = 9223
BROWSER_DAEMON_HOST = "127.0.0.1"


def _call_browser_daemon(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Call browser daemon HTTP API (fallback mode).

    Args:
        endpoint: API path (e.g., "/navigate", "/screenshot")
        method: HTTP method
        data: JSON body for POST

    Returns:
        dict: Response JSON
    """
    import urllib.request
    import urllib.error

    url = f"http://{BROWSER_DAEMON_HOST}:{BROWSER_DAEMON_PORT}{endpoint}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"browser daemon unreachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


# ── Vision module integration ──────────────────────────────────


def _recognize_screenshot(screenshot_path: str) -> dict:
    """Call vision module to recognize screenshot content via subprocess CLI.

    Returns:
        dict: {text: str, success: bool, error: str}
    """
    import subprocess

    try:
        result = subprocess.run(
            [sys.executable, "-m", "modules.vision.recognize", screenshot_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode == 0:
            return {"text": result.stdout.strip(), "success": True}
        else:
            return {"text": "", "success": False, "error": result.stderr.strip() or "vision CLI failed"}
    except subprocess.TimeoutExpired:
        return {"text": "", "success": False, "error": "vision CLI timeout (120s)"}
    except FileNotFoundError:
        return {"text": "", "success": False, "error": "python executable not found"}
    except Exception as e:
        return {"text": "", "success": False, "error": str(e)}


# ── Aggregator integration ─────────────────────────────────────


def _summarize_content(content: str, query: str = "") -> str:
    """Call aggregator to generate Markdown summary.

    Returns:
        str: Markdown formatted summary
    """
    try:
        from modules.search.aggregator import aggregate_results
        # Wrap content as a single "search result" for aggregator
        results = [{"title": "Collected Content", "snippet": content[:5000], "url": ""}]
        aggregated = aggregate_results(results, query=query)
        if isinstance(aggregated, dict):
            return aggregated.get("markdown", str(aggregated))
        return str(aggregated)
    except ImportError:
        # Fallback: simple formatting
        return f"# Collection Report\n\n**Query**: {query}\n\n**Content**:\n\n{content[:5000]}"
    except Exception as e:
        return f"# Collection Report\n\n**Error**: {e}\n\n**Raw Content**:\n\n{content[:5000]}"


# ── Atomic file write ──────────────────────────────────────────


def _atomic_write(filepath: str, content: str) -> None:
    """Atomically write content to file."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(filepath), suffix=".tmp", prefix=Path(filepath).stem + "_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ── Primary: browser-use Agent ─────────────────────────────────


def _run_with_browser_use(target: str, task: str, output_path: str) -> dict:
    """Run collection pipeline using browser-use Agent.

    This is the primary path when browser-use is installed.
    """
    try:
        from browser_use import Agent
        # Note: browser-use requires an LLM; we use DeepSeek via langchain bridge
        # For now, this is a placeholder that will be configured with actual LLM
        # when browser-use is fully integrated
        return {
            "success": False,
            "error": "browser-use Agent requires LLM configuration (TODO: integrate DeepSeek)",
            "backend": "browser_use",
        }
    except Exception as e:
        return {"success": False, "error": str(e), "backend": "browser_use"}


# ── Fallback: browser daemon + vision + aggregator ────────────


def _run_with_browser_daemon(target: str, task: str, output_path: str) -> dict:
    """Run collection pipeline using browser daemon HTTP API.

    Fallback when browser-use is not available.
    """
    steps = []

    # Step 1: Navigate to target
    nav_result = _call_browser_daemon("/navigate", method="POST", data={"url": target})
    steps.append({"step": "navigate", "result": nav_result})
    if "error" in nav_result:
        return {"success": False, "backend": "browser_daemon", "steps": steps, "error": nav_result["error"]}

    # Step 2: Wait for page load
    time.sleep(3)
    steps.append({"step": "wait", "result": {"waited": 3}})

    # Step 3: Screenshot
    screenshot_path = str(_REPORTS_DIR / f"screenshot_{int(time.time())}.png")
    screenshot_result = _call_browser_daemon(
        "/screenshot", method="POST", data={"path": screenshot_path}
    )
    steps.append({"step": "screenshot", "result": screenshot_result})
    if "error" in screenshot_result:
        return {"success": False, "backend": "browser_daemon", "steps": steps, "error": screenshot_result["error"]}

    # Step 4: Recognize screenshot content
    actual_path = screenshot_result.get("path", screenshot_path)
    recognize_result = _recognize_screenshot(actual_path)
    steps.append({"step": "recognize", "result": recognize_result})

    # Step 5: Summarize
    content_text = recognize_result.get("text", "")
    if not content_text:
        return {
            "success": False,
            "backend": "browser_daemon",
            "steps": steps,
            "error": "no text recognized from screenshot",
        }

    summary = _summarize_content(content_text, query=task)
    steps.append({"step": "summarize", "result": {"summary_length": len(summary)}})

    # Step 6: Write to file
    report_content = f"""# Collection Report

- **Target**: {target}
- **Task**: {task}
- **Backend**: browser_daemon
- **Generated**: {datetime.now().isoformat()}
- **Screenshot**: {actual_path}

---

{summary}
"""
    _atomic_write(output_path, report_content)
    steps.append({"step": "write", "result": {"path": output_path, "size": len(report_content)}})

    return {
        "success": True,
        "backend": "browser_daemon",
        "output_path": output_path,
        "steps": steps,
    }


# ── Public API ─────────────────────────────────────────────────


def run_collection_pipeline(
    target: str,
    output_path: str = "",
    task: str = "collect and summarize content",
) -> dict:
    """Run end-to-end collection pipeline.

    Automatically selects browser-use (primary) or browser daemon (fallback).

    Args:
        target: URL to collect from
        output_path: Path to write report (default: _runtime/reports/<timestamp>.md)
        task: Task description for the agent

    Returns:
        dict: {success, backend, output_path, steps, error}
    """
    if not output_path:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(_REPORTS_DIR / f"report_{int(time.time())}.md")

    # Select backend
    if _check_browser_use():
        result = _run_with_browser_use(target, task, output_path)
        # If browser-use fails, fallback to daemon
        if not result.get("success"):
            result["_fallback_attempted"] = True
            daemon_result = _run_with_browser_daemon(target, task, output_path)
            daemon_result["primary_backend"] = "browser_use"
            daemon_result["primary_error"] = result.get("error")
            return daemon_result
        return result
    else:
        return _run_with_browser_daemon(target, task, output_path)


def get_backend_status() -> dict:
    """Check which backend is available.

    Returns:
        dict: {browser_use_available, browser_daemon_reachable}
    """
    status = {
        "browser_use_available": _check_browser_use(),
        "browser_daemon_reachable": False,
    }

    # Check browser daemon
    result = _call_browser_daemon("/status")
    if "error" not in result:
        status["browser_daemon_reachable"] = True
        status["browser_daemon_status"] = result

    return status


if __name__ == "__main__":
    # CLI: python -m modules.orchestrator.agent_wrapper [status|collect]
    if len(sys.argv) < 2:
        print("Usage: python -m modules.orchestrator.agent_wrapper [status|collect] [args]")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        print(json.dumps(get_backend_status(), indent=2, ensure_ascii=False))

    elif cmd == "collect":
        if len(sys.argv) < 3:
            print("Usage: collect <url> [output_path]")
            sys.exit(1)
        url = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else ""
        result = run_collection_pipeline(target=url, output_path=out)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    else:
        print(f"Unknown command: {cmd}")
