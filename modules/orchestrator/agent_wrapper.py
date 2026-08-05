#!/usr/bin/env python3
"""Secure end-to-end web collection pipeline.

Default backend order:

1. authenticated browser daemon with global request interception;
2. fail-closed secure Fetch for static content.

The legacy ``browser-use`` backend is selected only when its first availability
check sees ``AGENT_FORGE_ALLOW_UNGUARDED_BROWSER_USE=1``. It accepts only the
upstream Browser Use or Anthropic clients; DeepSeek requests must use Agent
Forge Model Gateway and are therefore not passed through this unguarded backend.
The cached boolean remains externally controllable for backward-compatible
tests and explicit in-process dependency injection.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from modules.bootstrap.dependencies import activate_vendor_path
from modules.common.security import (
    UnsafeNetworkTarget,
    load_or_create_service_token,
    validate_outbound_url,
)
from modules.mcp import fetch_mcp as _legacy_fetch_module

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
activate_vendor_path()
_REPORTS_DIR = _PROJECT_ROOT / "_runtime" / "reports"
_BROWSER_USE_AVAILABLE: bool | None = None
_LEGACY_FETCH_ORIGINAL = _legacy_fetch_module.fetch_url

BROWSER_DAEMON_HOST = "127.0.0.1"
BROWSER_DAEMON_PORT = int(os.environ.get("AGENT_FORGE_BROWSER_PORT", "9223"))
MAX_DAEMON_RESPONSE_BYTES = 5_000_000
MAX_COLLECTED_TEXT_CHARS = 200_000
MAX_SCROLLS = 5


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _browser_use_installed() -> bool:
    try:
        from browser_use import Agent  # noqa: F401
        return True
    except ImportError:
        return False


def _check_browser_use() -> bool:
    """Return the cached selectable state for the browser-use backend.

    A caller that explicitly sets ``_BROWSER_USE_AVAILABLE`` retains the legacy
    cache contract. On first detection, installation alone is insufficient:
    the reduced-security override must also be enabled.
    """

    global _BROWSER_USE_AVAILABLE
    if _BROWSER_USE_AVAILABLE is not None:
        return _BROWSER_USE_AVAILABLE
    _BROWSER_USE_AVAILABLE = _browser_use_installed() and _env_bool(
        "AGENT_FORGE_ALLOW_UNGUARDED_BROWSER_USE", False
    )
    return _BROWSER_USE_AVAILABLE


def _validate_target(target: str) -> str:
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must not be empty")
    candidate = target.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("target must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("target HTTP(S) URLs containing credentials are not allowed")
    validate_outbound_url(candidate)
    return candidate


def _validate_output_path(output_path: str) -> str:
    candidate = Path(output_path).expanduser().resolve()
    if candidate.suffix.lower() != ".md":
        raise ValueError("output_path must use the .md extension")
    allowed_roots = (_REPORTS_DIR.parent.resolve(), Path(tempfile.gettempdir()).resolve())
    for root in allowed_roots:
        try:
            candidate.relative_to(root)
            return str(candidate)
        except ValueError:
            continue
    raise ValueError("output_path must be inside _runtime or the OS temporary directory")


def _browser_token() -> str:
    return load_or_create_service_token(
        "browser", runtime_root=_PROJECT_ROOT / "_runtime"
    )


def _call_browser_daemon(
    endpoint: str,
    method: str = "GET",
    data: dict | None = None,
    *,
    timeout: float = 30,
) -> dict:
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ValueError("endpoint must be an absolute daemon path")
    url = f"http://{BROWSER_DAEMON_HOST}:{BROWSER_DAEMON_PORT}{endpoint}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {_browser_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_DAEMON_RESPONSE_BYTES + 1)
            if len(raw) > MAX_DAEMON_RESPONSE_BYTES:
                return {"error": "browser daemon response exceeded safety limit"}
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return {"error": "browser daemon returned a non-object response"}
            if payload.get("ok") is False and "error" in payload:
                return {"error": str(payload["error"]), "daemon_response": payload}
            return payload
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(MAX_DAEMON_RESPONSE_BYTES).decode("utf-8"))
        except Exception:
            detail = {"error": str(exc)}
        return {
            "error": f"browser daemon HTTP {exc.code}: {detail.get('error', exc.reason)}"
        }
    except urllib.error.URLError as exc:
        return {"error": f"browser daemon unreachable: {exc}"}
    except Exception as exc:
        return {"error": str(exc)}


def _recognize_screenshot(screenshot_path: str) -> dict:
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
        return {
            "text": "",
            "success": False,
            "error": result.stderr.strip() or "vision CLI failed",
        }
    except subprocess.TimeoutExpired:
        return {"text": "", "success": False, "error": "vision CLI timeout (120s)"}
    except FileNotFoundError:
        return {"text": "", "success": False, "error": "python executable not found"}
    except Exception as exc:
        return {"text": "", "success": False, "error": str(exc)}


def _summarize_content(content: str, query: str = "") -> str:
    bounded = content[:MAX_COLLECTED_TEXT_CHARS]
    try:
        from modules.search.aggregator import aggregate_results

        result_group = [{
            "sub_query": query or "collected content",
            "results": [{
                "title": "Collected Content",
                "snippet": bounded,
                "url": "",
                "source": "collection",
            }],
        }]
        aggregated = aggregate_results(query or "summarize collected content", result_group)
        if isinstance(aggregated, dict):
            return aggregated.get("markdown", str(aggregated))
        return str(aggregated)
    except ImportError:
        return f"# Collection Report\n\n**Query**: {query}\n\n**Content**:\n\n{bounded}"
    except Exception as exc:
        return (
            f"# Collection Report\n\n**Error**: {exc}\n\n"
            f"**Raw Content**:\n\n{bounded}"
        )


def _atomic_write(filepath: str, content: str) -> None:
    parent = os.path.dirname(filepath) or "."
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=parent, suffix=".tmp", prefix=Path(filepath).stem + "_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, filepath)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _create_browser_use_agent(target: str, task: str):
    target = _validate_target(target)
    browser_use_key = os.environ.get("BROWSER_USE_API_KEY", "").strip()
    anthropic_key = (
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
        or os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    )
    if not (browser_use_key or anthropic_key):
        raise RuntimeError(
            "browser-use requires BROWSER_USE_API_KEY or ANTHROPIC_API_KEY. "
            "DeepSeek-backed collection must use the guarded Browser daemon and "
            "Agent Forge Model Gateway, not this reduced-security backend."
        )
    if not _env_bool("AGENT_FORGE_ALLOW_UNGUARDED_BROWSER_USE", False):
        raise RuntimeError(
            "browser-use is disabled because its network requests are not guarded by "
            "the Agent Forge SSRF policy"
        )

    if browser_use_key:
        from browser_use import ChatBrowserUse

        llm = ChatBrowserUse(
            model=os.environ.get("BROWSER_USE_MODEL", "bu-2-0"),
            api_key=browser_use_key,
        )
    else:
        from browser_use import ChatAnthropic

        llm = ChatAnthropic(
            model=os.environ.get("BROWSER_USE_MODEL", "claude-sonnet-4-5"),
            api_key=anthropic_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
        )

    from browser_use import Agent

    agent_task = (
        f"Open {target}. {task}. Return a concise factual result including the "
        "source URL. Do not perform purchases, submissions, or other external "
        "side effects."
    )
    return Agent(task=agent_task, llm=llm, use_vision=False)


def _run_browser_use_agent(agent):
    import asyncio

    max_steps = int(os.environ.get("BROWSER_USE_MAX_STEPS", "25"))
    if not 1 <= max_steps <= 100:
        raise ValueError("BROWSER_USE_MAX_STEPS must be between 1 and 100")
    coroutine = agent.run(max_steps=max_steps)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    outcome: list[object] = []
    failure: list[BaseException] = []

    def runner() -> None:
        try:
            outcome.append(asyncio.run(coroutine))
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return outcome[0]


def _run_with_browser_use(target: str, task: str, output_path: str) -> dict:
    try:
        agent = _create_browser_use_agent(target, task)
        history = _run_browser_use_agent(agent)
        final_result = getattr(history, "final_result", None)
        content = final_result() if callable(final_result) else str(history)
        if not content or not content.strip():
            return {
                "success": False,
                "error": "browser-use completed without a final result",
                "backend": "browser_use",
            }
        report = f"""# Collection Report

- **Target**: {target}
- **Task**: {task}
- **Backend**: browser_use (explicit reduced-security override)
- **Generated**: {datetime.now(timezone.utc).isoformat()}
- **Security warning**: backend network requests are not intercepted by Agent Forge

---

{content.strip()}
"""
        _atomic_write(output_path, report)
        return {
            "success": True,
            "backend": "browser_use",
            "output_path": output_path,
            "content_length": len(content),
            "degraded_security": True,
            "warnings": ["unguarded_browser_use_explicitly_enabled"],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "backend": "browser_use"}


def _bounded_scroll_count() -> int:
    try:
        count = int(os.environ.get("AGENT_FORGE_COLLECTION_SCROLLS", "2"))
    except ValueError as exc:
        raise ValueError("AGENT_FORGE_COLLECTION_SCROLLS must be an integer") from exc
    if not 0 <= count <= MAX_SCROLLS:
        raise ValueError(f"AGENT_FORGE_COLLECTION_SCROLLS must be between 0 and {MAX_SCROLLS}")
    return count


def _merge_text_snapshots(snapshots: list[str]) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for value in snapshots:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return "\n\n--- lazy-load snapshot ---\n\n".join(unique)[:MAX_COLLECTED_TEXT_CHARS]


def _run_with_browser_daemon(target: str, task: str, output_path: str) -> dict:
    steps: list[dict] = []
    navigation = _call_browser_daemon(
        "/navigate", method="POST", data={"url": target}
    )
    steps.append({"step": "navigate", "result": navigation})
    if "error" in navigation:
        return {
            "success": False,
            "backend": "browser_daemon",
            "steps": steps,
            "error": navigation["error"],
        }

    wait_result = _call_browser_daemon(
        "/wait", method="POST", data={"selector": "body", "timeout": 15_000}
    )
    steps.append({"step": "wait_for_body", "result": wait_result})

    snapshots: list[str] = []
    text_result = _call_browser_daemon("/text", method="POST", data={})
    steps.append(
        {
            "step": "extract_visible_text",
            "result": {
                "ok": "error" not in text_result,
                "length": len(text_result.get("text", "")),
            },
        }
    )
    if text_result.get("text"):
        snapshots.append(text_result["text"])

    for index in range(_bounded_scroll_count()):
        scroll_result = _call_browser_daemon(
            "/scroll",
            method="POST",
            data={"x": 0, "y": min((index + 1) * 250_000, 1_000_000)},
        )
        steps.append({"step": f"scroll_{index + 1}", "result": scroll_result})
        if "error" in scroll_result:
            break
        time.sleep(1)
        next_text = _call_browser_daemon("/text", method="POST", data={})
        if next_text.get("text"):
            snapshots.append(next_text["text"])

    content_text = _merge_text_snapshots(snapshots)
    screenshot_path = ""
    vision_text = ""
    capture = _env_bool("AGENT_FORGE_COLLECTION_CAPTURE_SCREENSHOT", False)
    if capture or len(content_text) < 200:
        screenshot_path = str(_REPORTS_DIR / f"screenshot_{int(time.time())}.png")
        screenshot_result = _call_browser_daemon(
            "/screenshot", method="POST", data={"path": screenshot_path}
        )
        steps.append({"step": "screenshot", "result": screenshot_result})
        if "error" in screenshot_result:
            if not content_text:
                return {
                    "success": False,
                    "backend": "browser_daemon",
                    "steps": steps,
                    "error": screenshot_result["error"],
                }
        else:
            screenshot_path = screenshot_result.get("path", screenshot_path)
            recognition = _recognize_screenshot(screenshot_path)
            steps.append({"step": "recognize", "result": recognition})
            vision_text = recognition.get("text", "")

    combined = content_text
    if vision_text:
        combined = (combined + "\n\n--- visual recognition ---\n\n" + vision_text).strip()
    if not combined:
        return {
            "success": False,
            "backend": "browser_daemon",
            "steps": steps,
            "error": "no text was returned or recognized from browser content",
        }

    summary = _summarize_content(combined, query=task)
    final_url = navigation.get("url", target)
    report_content = f"""# Collection Report

- **Target**: {target}
- **Final URL**: {final_url}
- **Task**: {task}
- **Backend**: authenticated browser daemon
- **Generated**: {datetime.now(timezone.utc).isoformat()}
- **DOM characters**: {len(content_text)}
- **Vision characters**: {len(vision_text)}
- **Screenshot**: {screenshot_path or "not captured"}

---

{summary}
"""
    _atomic_write(output_path, report_content)
    steps.append(
        {"step": "write", "result": {"path": output_path, "size": len(report_content)}}
    )
    return {
        "success": True,
        "backend": "browser_daemon",
        "output_path": output_path,
        "source_url": final_url,
        "content_length": len(combined),
        "steps": steps,
        "security": {"authenticated": True, "network_policy": "fail_closed"},
    }


def _select_fetch_function():
    """Use secure Fetch unless an explicit in-process adapter was injected."""

    if _legacy_fetch_module.fetch_url is not _LEGACY_FETCH_ORIGINAL:
        return _legacy_fetch_module.fetch_url
    from modules.mcp.secure_fetch_mcp import fetch_url

    return fetch_url


def _run_with_fetch(target: str, task: str, output_path: str) -> dict:
    try:
        fetched = _select_fetch_function()(target, max_length=50_000)
        content = fetched.get("content", "")
        if not content:
            return {"success": False, "backend": "fetch", "error": "empty response"}
        summary = _summarize_content(content, query=task)
        report = f"""# Collection Report

- **Target**: {target}
- **Final URL**: {fetched.get("url", target)}
- **Task**: {task}
- **Backend**: secure fetch
- **Generated**: {datetime.now(timezone.utc).isoformat()}
- **Bytes read**: {fetched.get("bytes_read", "unknown")}

---

{summary}
"""
        _atomic_write(output_path, report)
        return {
            "success": True,
            "backend": "fetch",
            "output_path": output_path,
            "source_url": fetched.get("url", target),
            "content_length": len(content),
            "security": {"network_policy": "fail_closed"},
        }
    except Exception as exc:
        return {"success": False, "backend": "fetch", "error": str(exc)}


def run_collection_pipeline(
    target: str,
    output_path: str = "",
    task: str = "collect and summarize content",
) -> dict:
    try:
        target = _validate_target(target)
    except (ValueError, UnsafeNetworkTarget) as exc:
        return {"success": False, "backend": "none", "error": str(exc), "errors": []}

    if not output_path:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = str(_REPORTS_DIR / f"report_{int(time.time())}.md")
    try:
        output_path = _validate_output_path(output_path)
    except ValueError as exc:
        return {"success": False, "backend": "none", "error": str(exc), "errors": []}

    errors: list[str] = []
    if _check_browser_use():
        result = _run_with_browser_use(target, task, output_path)
        if result.get("success"):
            return result
        errors.append(result.get("error", "browser-use failed"))

    daemon_result = _run_with_browser_daemon(target, task, output_path)
    if daemon_result.get("success"):
        if errors:
            daemon_result["primary_backend"] = "browser_use"
            daemon_result["primary_error"] = errors[0]
            daemon_result["fallback_errors"] = errors.copy()
        return daemon_result
    errors.append(daemon_result.get("error", "browser daemon failed"))

    fetch_result = _run_with_fetch(target, task, output_path)
    if fetch_result.get("success"):
        fetch_result["fallback_errors"] = errors
        return fetch_result
    errors.append(fetch_result.get("error", "fetch failed"))
    return {
        "success": False,
        "backend": "none",
        "error": "all collection backends failed",
        "errors": errors,
    }


def get_backend_status() -> dict:
    installed = _browser_use_installed()
    selectable = _check_browser_use()
    status = {
        "browser_use_installed": installed,
        "browser_use_available": selectable,
        "browser_use_security": (
            "explicit_reduced_security_override"
            if selectable
            else "disabled_until_network_policy_adapter_exists"
        ),
        "browser_daemon_reachable": False,
        "secure_fetch_available": False,
    }
    daemon_status = _call_browser_daemon("/status")
    if "error" not in daemon_status:
        status["browser_daemon_reachable"] = True
        status["browser_daemon_status"] = daemon_status
    try:
        from modules.mcp import secure_fetch_mcp  # noqa: F401

        status["secure_fetch_available"] = True
    except ImportError:
        pass
    return status


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m modules.orchestrator.agent_wrapper [status|collect] [args]")
        raise SystemExit(0)
    command = sys.argv[1]
    if command == "status":
        print(json.dumps(get_backend_status(), indent=2, ensure_ascii=False))
    elif command == "collect":
        if len(sys.argv) < 3:
            print("Usage: collect <url> [output_path]")
            raise SystemExit(1)
        result = run_collection_pipeline(
            target=sys.argv[2],
            output_path=sys.argv[3] if len(sys.argv) > 3 else "",
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Unknown command: {command}")
        raise SystemExit(2)
