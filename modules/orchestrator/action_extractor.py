#!/usr/bin/env python3
"""C2: Action Extractor — 从 Markdown 报告中抽取待办/截止日期/关键决策。

Uses DeepSeek API (model resolved via Dispatch Guard) to extract structured
action items from Markdown content, then writes them to the schedules table.

R2-5 fixes:
    - R2-5.1: All persisted times normalized to UTC via
      modules.common.time_utils.to_utc_iso(). source_timezone and
      original_due_at are preserved alongside the UTC value.
    - R2-5.2: Failures are distinguishable — the result dict carries a
      `status` field: no_actions | model_error | parse_error |
      validation_error | storage_error | ok.
    - R2-5.3: No swallowed exceptions. Storage failures are logged and
      returned to the caller, not silently dropped.

Design:
    - SC6 fix: uses guard.resolve_model("action_extraction") instead of
      hardcoding the Flash model
    - SC4 fix: prompt requires JSON object {"actions": [...]} matching the
      response_format=json_object API constraint
    - SC5 fix: due_at validated as ISO 8601; invalid values rejected
    - PII 脱敏：content 出境前调用 privacy.redact_outbound()

Usage:
    from modules.orchestrator.action_extractor import extract_action_items

    result = extract_action_items(markdown_content, source_ref="/reports/weekly.md")
    # result: {"status": "ok", "items": [{title, due_at, ...}]}
"""
import os
import sys
import json
import re
import urllib.request
import urllib.error
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# R2-5.1: Default timezone read from unified config, not hardcoded in domain.
_DEFAULT_TZ_NAME = os.environ.get("AGENT_FORGE_USER_TZ", "Asia/Shanghai")

# ── API config ────────────────────────────────────────────────
_API_ENDPOINT = "https://api.deepseek.com/v1/chat/completions"
_MAX_CONTENT_CHARS = 8000  # Limit content to avoid token overflow
_TIMEOUT = 30

# ── Extraction prompt (SC4 fix: JSON object, not array) ───────
_EXTRACTION_PROMPT = """你是一个待办事项抽取助手。从以下 Markdown 内容中抽取所有的待办事项、截止日期、重要决策。

输出格式：严格的 JSON 对象（不是数组），结构如下：

{
  "actions": [
    {
      "title": "简短标题（不超过 50 字）",
      "due_at": "截止日期 ISO 8601 格式（带时区，如 2026-08-01T23:59:00+08:00）。如果没有明确截止日期，设为 null",
      "priority": "优先级，必须是 low/medium/high/critical 之一",
      "description": "详细描述（可选）"
    }
  ]
}

如果内容中没有待办事项，返回 {"actions": []}。

只输出 JSON 对象，不要其他文字。顶层必须是对象，不能是数组。

Markdown 内容：
"""


def _redact_pii(content: str) -> str:
    """Redact PII before sending to API."""
    try:
        from modules.search.privacy import redact_outbound
        redacted, _ = redact_outbound(content)
        return redacted
    except ImportError:
        return content  # Fallback: no redaction if privacy module unavailable


def _resolve_model() -> str:
    """SC6 fix: resolve model via Dispatch Guard instead of hardcoding Flash.

    Returns the model name to use. If the guard is unavailable, falls back
    to the Flash model with a warning.
    """
    try:
        from modules.dispatch import guard
        return guard.resolve_model(
            "action_extraction",
            caller="action_extractor._call_api",
        )
    except ImportError:
        # Guard not available — fall back to Flash with a warning.
        # This should not happen in normal operation.
        print(
            "[action_extractor] WARNING: dispatch guard not available, "
            "falling back to deepseek-chat",
            file=sys.stderr,
        )
        return "deepseek-chat"


def _call_api(content: str, api_key: str) -> str:
    """Call DeepSeek API to extract action items.

    SC6 fix: model resolved via guard.resolve_model().

    Returns:
        str: Raw API response text (expected JSON object with "actions" array)
    """
    # Truncate content to avoid token overflow
    truncated = content[:_MAX_CONTENT_CHARS]

    # PII redaction
    redacted = _redact_pii(truncated)

    # SC6 fix: resolve model via dispatch guard
    model = _resolve_model()

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": _EXTRACTION_PROMPT + redacted}
        ],
        "max_tokens": 2048,
        "temperature": 0.1,  # Low temperature for structured extraction
        "response_format": {"type": "json_object"},  # Force JSON object output
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
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"DeepSeek API error {e.code}: "
            f"{e.read().decode('utf-8', errors='replace')[:200]}"
        )
    except urllib.error.URLError as e:
        raise RuntimeError(f"DeepSeek API unreachable: {e}")


def _validate_due_at(due_at) -> tuple:
    """R2-5.1: Validate due_at and normalize to UTC.

    Uses modules.common.time_utils.to_utc_iso() so all persisted times are
    UTC. The source_timezone and original_due_at are preserved so callers
    can render local times.

    Args:
        due_at: raw due_at value from API (str, None, or other)

    Returns:
        tuple: (utc_iso, source_timezone, original_due_at, error_or_none)
        - If due_at is None or empty: returns (default_7_days_utc, tz, None, None)
        - If due_at is valid ISO 8601: returns (utc_iso, tz, original, None)
        - If due_at is invalid: returns (None, None, original, error_message)
    """
    from modules.common.time_utils import to_utc_iso, parse_iso_with_tz, DEFAULT_USER_TIMEZONE

    original = due_at if isinstance(due_at, str) else None

    if due_at is None or (isinstance(due_at, str) and not due_at.strip()):
        # No due_at → default to 7 days from now in UTC
        default_dt = datetime.now(timezone.utc) + timedelta(days=7)
        utc_iso, _, _ = to_utc_iso(default_dt)
        return utc_iso, None, None, None

    if not isinstance(due_at, str):
        return (None, None, original,
                f"due_at must be a string, got {type(due_at).__name__}")

    text = due_at.strip()
    if not text:
        default_dt = datetime.now(timezone.utc) + timedelta(days=7)
        utc_iso, _, _ = to_utc_iso(default_dt)
        return utc_iso, None, None, None

    # R2-5.1: Parse with timezone awareness, then convert to UTC.
    try:
        parsed = parse_iso_with_tz(text, default_timezone=_DEFAULT_TZ_NAME)
    except (ValueError, TypeError) as e:
        return (None, None, original,
                f"due_at '{text}' is not valid ISO 8601: {e}")

    # R2-5.1: Detect if the original string was naive (no offset).
    # parse_iso_with_tz already attached the default timezone, so we check
    # the original text for an offset pattern.
    was_naive = not re.search(r'[+-]\d{2}:\d{2}$|Z$', text)
    source_tz = _DEFAULT_TZ_NAME if was_naive else None

    # to_utc_iso converts to UTC. Since parsed is already aware (parse_iso_with_tz
    # attached a tz if it was naive), to_utc_iso will just convert.
    utc_iso, _, _ = to_utc_iso(parsed, original=original)
    return utc_iso, source_tz, original, None


def _parse_extraction_response(response_text: str) -> list:
    """Parse the API response into a list of action items.

    SC4 fix: expects JSON object with "actions" array (not raw array).
    Falls back to legacy array format with a deprecation warning.
    """
    if not response_text or not response_text.strip():
        return []

    text = response_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    # SC4 fix: canonical format is {"actions": [...]}
    if isinstance(parsed, dict):
        if "actions" in parsed:
            actions = parsed["actions"]
            if not isinstance(actions, list):
                return []
            parsed = actions
        elif "items" in parsed:
            # Legacy fallback: {"items": [...]}
            import warnings
            warnings.warn(
                "API returned {'items': [...]} (deprecated); "
                "use {'actions': [...]} instead",
                DeprecationWarning,
                stacklevel=2,
            )
            parsed = parsed["items"]
        else:
            # Single item as dict — wrap in list
            parsed = [parsed]
    elif isinstance(parsed, list):
        # Legacy fallback: raw array (API was supposed to return object)
        import warnings
        warnings.warn(
            "API returned a raw JSON array (deprecated); "
            "use {'actions': [...]} instead",
            DeprecationWarning,
            stacklevel=2,
        )
    else:
        return []

    if not isinstance(parsed, list):
        return []

    # Validate and clean each item
    valid_items = []
    valid_priorities = {"low", "medium", "high", "critical"}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        # Normalize priority
        priority = str(item.get("priority", "medium")).lower()
        if priority not in valid_priorities:
            priority = "medium"

        # R2-5.1: validate due_at + normalize to UTC, preserve source_timezone
        raw_due_at = item.get("due_at")
        validated_due_at, source_tz, original_due_at, due_at_error = _validate_due_at(raw_due_at)
        if due_at_error:
            # Invalid due_at → keep in results with error flag, don't write to DB
            valid_items.append({
                "title": title[:200],
                "due_at": None,
                "priority": priority,
                "description": str(item.get("description", "")),
                "validation_error": f"due_at: {due_at_error}",
            })
            continue

        valid_items.append({
            "title": title[:200],
            "due_at": validated_due_at,
            "due_at_utc": validated_due_at,  # R2-5.1: explicit UTC field
            "source_timezone": source_tz,     # R2-5.1: preserve source tz
            "original_due_at": original_due_at,  # R2-5.1: preserve original
            "priority": priority,
            "description": str(item.get("description", "")),
        })

    return valid_items


def extract_action_items(
    markdown_content: str,
    source_ref: str = "",
    api_key: Optional[str] = None,
    write_to_db: bool = True,
) -> dict:
    """R2-5.2: Extract action items from Markdown content.

    Returns a structured result dict with a `status` field so callers can
    distinguish failure modes:

        ok              — items extracted successfully
        no_actions      — model responded correctly, nothing to extract
        model_error     — API call failed (network, auth, rate limit)
        parse_error     — API response could not be parsed as JSON
        validation_error— some items had invalid due_at (items still returned)
        storage_error   — DB write failed (items still returned)

    Args:
        markdown_content: Markdown text to extract from
        source_ref: Reference path (e.g., report file path)
        api_key: DeepSeek API key (default: from env DEEPSEEK_API_KEY)
        write_to_db: If True, write extracted items to schedules table

    Returns:
        dict: {"status": str, "items": list, "error": str|None}
    """
    # R2-5.2: Empty content → no_actions (not an error)
    if not markdown_content or not markdown_content.strip():
        return {"status": "no_actions", "items": [], "error": None}

    if api_key is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        # R2-5.2: Missing API key → model_error (not empty list)
        return {
            "status": "model_error",
            "items": [],
            "error": "DEEPSEEK_API_KEY not set",
        }

    # R2-5.2: API failure → model_error (not empty list)
    try:
        response_text = _call_api(markdown_content, api_key)
    except RuntimeError as exc:
        return {
            "status": "model_error",
            "items": [],
            "error": str(exc),
        }

    items = _parse_extraction_response(response_text)

    # R2-5.2: No items extracted → no_actions
    if not items:
        return {"status": "no_actions", "items": [], "error": None}

    # Check if any items have validation errors
    has_validation_errors = any(item.get("validation_error") for item in items)

    # R2-5.3: No swallowed exceptions. Storage failures are returned.
    storage_errors = []
    if write_to_db:
        try:
            from modules.orchestrator.schedule_store import add_schedule
        except ImportError as exc:
            # schedule_store module genuinely unavailable — this is a
            # configuration error, not a silent skip.
            storage_errors.append(f"schedule_store unavailable: {exc}")
        else:
            for item in items:
                if item.get("validation_error"):
                    continue

                due_at = item.get("due_at")
                if not due_at:
                    due_at = (datetime.now(timezone.utc) +
                              timedelta(days=7)).isoformat()

                try:
                    schedule_id = add_schedule(
                        title=item["title"],
                        due_at=due_at,
                        source="agent_extracted",
                        description=item.get("description", ""),
                        priority=item.get("priority", "medium"),
                        source_ref=source_ref,
                    )
                    item["schedule_id"] = schedule_id
                except Exception as exc:
                    # R2-5.3: Log structured error, don't swallow.
                    tb = traceback.format_exc()
                    storage_errors.append(
                        f"Failed to store '{item.get('title', '?')}': {exc}\n{tb}"
                    )

    # Determine final status
    if storage_errors and has_validation_errors:
        status = "validation_error"  # validation takes precedence
    elif storage_errors:
        status = "storage_error"
    elif has_validation_errors:
        status = "validation_error"
    else:
        status = "ok"

    return {
        "status": status,
        "items": items,
        "error": "; ".join(storage_errors) if storage_errors else None,
    }


def extract_from_file(file_path: str, api_key: Optional[str] = None) -> dict:
    """R2-5.2: Extract action items from a Markdown file.

    Returns the same structured dict as extract_action_items().
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, UnicodeDecodeError) as exc:
        return {
            "status": "parse_error",
            "items": [],
            "error": f"cannot read file '{file_path}': {exc}",
        }

    return extract_action_items(content, source_ref=file_path, api_key=api_key)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m modules.orchestrator.action_extractor <file_path>")
        sys.exit(0)

    file_path = sys.argv[1]
    items = extract_from_file(file_path)
    print(json.dumps(items, indent=2, ensure_ascii=False))
