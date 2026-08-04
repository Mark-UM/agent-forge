"""
Flash Role Guard — central dispatch interceptor (Phase 2, Direction 4)

Source: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 4
Plan: `upgrade_plan/CODING_DISCIPLINE_UPGRADE.md` Section 2B

Policy (per user constraint):
    - Pro (deepseek-reasoner) for core/most tasks
    - Flash (deepseek-chat) for utility tasks (review/scoring/classify/i18n/summarize/aggregator)
    - No other models allowed

Decision 2 implementation: auto-redirect to Pro with warning log (non-blocking).
When a caller requests Flash for a forbidden task, the guard:
    1. Logs a warning with task_type + caller info
    2. Auto-redirects to Pro model
    3. Returns the result as if Flash was called (caller sees no difference)

This is non-blocking because:
    - Pro is strictly more capable than Flash for forbidden tasks
    - The user explicitly approved this behavior in Decision 2
    - Forcing a hard failure would break existing search pipelines

Usage:
    from modules.dispatch import guard

    # Option A: Use the safe entry point (handles redirect transparently)
    result = guard.call_flash(
        task_type="quality_scoring",
        prompt="score this result...",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        timeout=30,
    )

    # Option B: Pre-check before your own API call
    if guard.check_task_allowed("quality_scoring"):
        # call Flash directly
    else:
        # call Pro instead
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

# ── Model constants ────────────────────────────────────────────
# Per user constraint: only these two models are allowed.
FLASH_MODEL = "deepseek-chat"        # DeepSeek V4 Flash — utility tasks
PRO_MODEL = "deepseek-reasoner"      # DeepSeek V4 Pro — core/most tasks
_API_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"

# ── Allowlist / Forbidden list ─────────────────────────────────
# Flash is a REVIEWER/UTILITY, never an IMPLEMENTER.
# Source: plan Section 2A + source doc Section 3.2 Direction 4
FLASH_ALLOWED_TASKS = frozenset({
    # Utility tasks (Flash is fine — fast, cheap, has fallback)
    "classify",              # task type classification (modules/prompt/classify.py)
    "quality_scoring",       # LLM-based scoring (modules/search/quality.py)
    "i18n",                  # translation (modules/search/i18n.py)
    "summarize",             # URL summarization (modules/search/summarize.py)
    "aggregator",            # result synthesis (modules/search/aggregator.py — has fallback)
    "action_extraction",     # extract action items from markdown (modules/orchestrator/action_extractor.py)
    # Review tasks (Flash as REVIEWER — its core strength per source doc)
    "review_code",           # 3-stage review: code (agents/review-code.md)
    "review_structure",      # 3-stage review: structure (agents/review-structure.md)
    "review_risk",           # 3-stage review: risk (agents/review-risk.md)
    "integration_check",     # pre-delivery integration link check (Phase 3 skill)
    "doc_alignment_check",   # comparing code vs doc naming (future)
    "ui_check",              # UI component architecture enforcement (Phase 3D skill)
    "delivery_check",        # delivery checklist execution (Phase 3C skill)
})

FLASH_FORBIDDEN_TASKS = frozenset({
    # Implementation tasks (Pro only — Flash quality collapses here)
    "implement_core",        # core algorithm modules (StackEngine/ScoreManager/StateMachine)
    "implement_ui",          # UI architecture (UIManager/panels/components)
    "implement_render",      # complex rendering (PostProcessing/BloomPass/shaders)
    "implement_contract",    # types.ts / contract files (naming-critical)
    "implement_test",        # test files (Flash produces soft assertions)
    "implement_config",      # engineering config (ESLint/CI/Vite/vitest)
    "planner",               # MindSearch query decomposition (entry point, quality matters)
    "architect",             # system design / architecture decisions
    "refactor",              # multi-file refactoring (requires deep understanding)
})

# ── Log file ───────────────────────────────────────────────────
_RUNTIME_DIR = Path(__file__).resolve().parent.parent.parent / "_runtime" / "dispatch"
_GUARD_LOG = _RUNTIME_DIR / "guard_log.jsonl"


class FlashRoleViolationError(Exception):
    """Raised when a forbidden task is dispatched to Flash.

    NOTE: This exception is NOT raised by default. The default behavior per
    Decision 2 is auto-redirect to Pro with warning log. Callers can opt into
    strict mode by passing strict=True to call_flash().
    """


def _log_guard_event(event_type: str, task_type: str, detail: Dict[str, Any]) -> None:
    """Append a guard event to the dispatch guard log.

    Args:
        event_type: "redirect" | "allowed" | "blocked"
        task_type: the task_type requested
        detail: additional context (caller, reason, etc.)
    """
    try:
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "task_type": task_type,
            "detail": detail,
        }
        with open(_GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # Logging is best-effort; never crash the caller
        pass


def _emit_warning(task_type: str, caller: Optional[str], reason: str) -> None:
    """Emit a stderr warning (visible to user) + log the redirect."""
    msg = (f"[flash_guard] REDIRECT Flash→Pro for task_type={task_type!r} "
           f"caller={caller or 'unknown'} reason={reason}")
    print(msg, file=sys.stderr)
    _log_guard_event("redirect", task_type, {
        "caller": caller,
        "reason": reason,
        "from_model": FLASH_MODEL,
        "to_model": PRO_MODEL,
    })


def check_task_allowed(task_type: str) -> bool:
    """Check if a task type is allowed on Flash (no side effects).

    Args:
        task_type: canonical task type string (see FLASH_ALLOWED_TASKS /
                   FLASH_FORBIDDEN_TASKS for the vocabulary)

    Returns:
        True if Flash is allowed for this task, False otherwise.
        Unknown task types default to False (safer — redirect to Pro).
    """
    if not task_type or not isinstance(task_type, str):
        return False
    return task_type in FLASH_ALLOWED_TASKS


def resolve_model(task_type: str, requested_model: Optional[str] = None,
                  caller: Optional[str] = None,
                  strict: bool = False) -> str:
    """Resolve which model to use for a given task type.

    This is the core policy function. It does NOT make any API call —
    it just returns the model name the caller should use.

    Args:
        task_type: canonical task type string
        requested_model: model the caller wanted to use (None = auto)
        caller: caller identifier for logging (e.g., "planner.plan_query")
        strict: if True, raise FlashRoleViolationError instead of auto-redirecting

    Returns:
        Model name string (FLASH_MODEL or PRO_MODEL)

    Raises:
        FlashRoleViolationError: if strict=True AND task_type is forbidden
    """
    # If caller explicitly requests Pro, always allow (Pro can do anything Flash can)
    if requested_model == PRO_MODEL:
        return PRO_MODEL

    # If task is explicitly allowed on Flash → use Flash
    if check_task_allowed(task_type):
        return FLASH_MODEL

    # If task is forbidden OR unknown → redirect to Pro
    if task_type in FLASH_FORBIDDEN_TASKS:
        reason = f"task explicitly forbidden on Flash"
    else:
        reason = f"task not in allowlist (unknown task — defaulting to Pro for safety)"

    if strict:
        _log_guard_event("blocked", task_type, {
            "caller": caller,
            "reason": reason,
        })
        raise FlashRoleViolationError(
            f"Task {task_type!r} is forbidden on Flash (caller={caller}). "
            f"Reason: {reason}. Use Pro model instead."
        )

    # Auto-redirect: emit warning + return Pro model
    _emit_warning(task_type, caller, reason)
    return PRO_MODEL


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
    """Safe entry point for calling Flash model with guard enforcement.

    This function:
        1. Resolves which model to use (Flash or auto-redirect to Pro)
        2. Makes the actual API call
        3. Returns the parsed response

    Args:
        task_type: canonical task type (see FLASH_ALLOWED_TASKS)
        prompt: user prompt content
        api_key: DeepSeek API key (None → env DEEPSEEK_API_KEY)
        timeout: API timeout in seconds
        max_tokens: max response tokens
        temperature: sampling temperature
        system_prompt: optional system message
        caller: caller identifier for logging
        strict: if True, raise on forbidden task instead of auto-redirecting

    Returns:
        dict with keys:
            - "content": str — model response text
            - "model_used": str — which model was actually called
            - "redirected": bool — True if auto-redirected from Flash to Pro
            - "raw": dict — raw API response (for debugging)

    Raises:
        FlashRoleViolationError: if strict=True AND task is forbidden
        RuntimeError: on API failure (network, auth, parse)
    """
    # Resolve model (may auto-redirect with warning)
    model = resolve_model(task_type, caller=caller, strict=strict)
    redirected = model == PRO_MODEL and task_type not in FLASH_ALLOWED_TASKS

    # Resolve API key
    if api_key is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set (do not use ANTHROPIC_AUTH_TOKEN for DeepSeek)"
        )

    # Build messages
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    req = urllib.request.Request(
        _API_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Flash guard API HTTP {e.code}: "
            f"{e.read().decode('utf-8', errors='replace')[:200]}"
        )
    except urllib.error.URLError as e:
        raise RuntimeError(f"Flash guard API URL error: {e.reason}")
    except TimeoutError:
        raise RuntimeError(f"Flash guard API timeout after {timeout}s")

    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Flash guard API response structure invalid: {e}")

    return {
        "content": content,
        "model_used": model,
        "redirected": redirected,
        "raw": data,
    }


def list_allowed_tasks() -> list:
    """Return sorted list of Flash-allowed task types (for /prompt stats)."""
    return sorted(FLASH_ALLOWED_TASKS)


def list_forbidden_tasks() -> list:
    """Return sorted list of Flash-forbidden task types (for /prompt stats)."""
    return sorted(FLASH_FORBIDDEN_TASKS)


def get_guard_log_tail(n: int = 20) -> list:
    """Return the last n guard log entries (for debugging/stats).

    Args:
        n: number of entries to return (most recent)

    Returns:
        list of dict entries (parsed from JSONL)
    """
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
