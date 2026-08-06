"""Flash/Pro role policy and backward-compatible guarded invocation.

The policy remains intentionally small: utility/review tasks may use Flash;
implementation/reasoning tasks use Pro. Actual model transport is delegated to
Model Gateway so the guard no longer implements a second HTTP stack.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from modules.dispatch.compat import invoke_with_urlopen
from modules.dispatch.gateway import FLASH_MODEL, PRO_MODEL

FLASH_ALLOWED_TASKS = frozenset({
    "classify",
    "quality_scoring",
    "i18n",
    "summarize",
    "aggregator",
    "action_extraction",
    "review_code",
    "review_structure",
    "review_risk",
    "integration_check",
    "doc_alignment_check",
    "ui_check",
    "delivery_check",
})

FLASH_FORBIDDEN_TASKS = frozenset({
    "implement_core",
    "implement_ui",
    "implement_render",
    "implement_contract",
    "implement_test",
    "implement_config",
    "planner",
    "architect",
    "refactor",
})

_RUNTIME_DIR = Path(__file__).resolve().parent.parent.parent / "_runtime" / "dispatch"
_GUARD_LOG = _RUNTIME_DIR / "guard_log.jsonl"


class FlashRoleViolationError(Exception):
    """Raised when strict mode rejects a Flash request."""


def _log_guard_event(event_type: str, task_type: str, detail: Dict[str, Any]) -> None:
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "task_type": task_type,
            "detail": detail,
        }
        with open(_GUARD_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _emit_warning(task_type: str, caller: Optional[str], reason: str) -> None:
    message = (
        f"[flash_guard] REDIRECT Flash→Pro for task_type={task_type!r} "
        f"caller={caller or 'unknown'} reason={reason}"
    )
    print(message, file=sys.stderr)
    _log_guard_event("redirect", task_type, {
        "caller": caller,
        "reason": reason,
        "from_model": FLASH_MODEL,
        "to_model": PRO_MODEL,
    })


def check_task_allowed(task_type: str) -> bool:
    if not task_type or not isinstance(task_type, str):
        return False
    return task_type in FLASH_ALLOWED_TASKS


def resolve_model(task_type: str, requested_model: Optional[str] = None,
                  caller: Optional[str] = None,
                  strict: bool = False) -> str:
    if requested_model == PRO_MODEL:
        return PRO_MODEL
    if check_task_allowed(task_type):
        return FLASH_MODEL

    if task_type in FLASH_FORBIDDEN_TASKS:
        reason = "task explicitly forbidden on Flash"
    else:
        reason = "task not in allowlist (unknown task — defaulting to Pro for safety)"

    if strict:
        _log_guard_event("blocked", task_type, {
            "caller": caller,
            "reason": reason,
        })
        raise FlashRoleViolationError(
            f"Task {task_type!r} is forbidden on Flash (caller={caller}). "
            f"Reason: {reason}. Use Pro model instead."
        )

    _emit_warning(task_type, caller, reason)
    return PRO_MODEL


def _gateway_error(error: str | None, timeout: int) -> RuntimeError:
    text = str(error or "unknown model error")
    import re
    match = re.search(r"HTTP Error (\d+):?\s*(.*)", text)
    if match:
        return RuntimeError(
            f"Flash guard API HTTP {match.group(1)}: {match.group(2)}".rstrip()
        )
    lowered = text.lower()
    if "urlopen error" in lowered or "url error" in lowered:
        return RuntimeError(f"Flash guard API URL error: {text}")
    if "timed out" in lowered or "timeout" in lowered:
        return RuntimeError(f"Flash guard API timeout after {timeout}s")
    if "no choices" in lowered or "content is not a string" in lowered:
        return RuntimeError(f"Flash guard API response structure invalid: {text}")
    return RuntimeError(f"Flash guard API failed: {text}")


def call_flash(
    task_type: str,
    prompt: str,
    api_key: Optional[str] = None,
    timeout: int = 30,
    max_tokens: int = 500,
    temperature: float = 0.0,
    system_prompt: Optional[str] = None,
    caller: Optional[str] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Resolve policy and invoke Model Gateway through the legacy urllib seam."""
    model = resolve_model(task_type, caller=caller, strict=strict)
    redirected = model == PRO_MODEL and task_type not in FLASH_ALLOWED_TASKS

    if api_key is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set (do not use ANTHROPIC_AUTH_TOKEN for DeepSeek)"
        )

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = invoke_with_urlopen(
        task_type=task_type,
        messages=messages,
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=model,
        timeout_seconds=float(timeout),
        max_retries=0,
        max_tokens=max_tokens,
        temperature=temperature,
        metadata={"caller": caller or "dispatch.guard"},
        record_run=False,
    )
    if not response.success:
        raise _gateway_error(response.error, timeout)
    return {
        "content": response.content.strip(),
        "model_used": model,
        "redirected": redirected,
        "raw": {
            "id": response.raw_id,
            "usage": dict(response.usage),
            "attempts": response.attempts,
        },
    }


def list_allowed_tasks() -> list:
    return sorted(FLASH_ALLOWED_TASKS)


def list_forbidden_tasks() -> list:
    return sorted(FLASH_FORBIDDEN_TASKS)


def get_guard_log_tail(n: int = 20) -> list:
    if not _GUARD_LOG.exists():
        return []
    try:
        lines = _GUARD_LOG.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-n:]:
            if line.strip():
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return result
    except OSError:
        return []
