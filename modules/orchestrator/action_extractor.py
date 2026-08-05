#!/usr/bin/env python3
"""Extract action items from Markdown and persist validated schedules.

Model routing, outbound redaction, timeout and structured JSON response options
flow through Model Gateway. Public status semantics remain explicit:
``no_actions``, ``model_error``, ``parse_error``, ``validation_error``,
``storage_error`` or ``ok``.
"""
from __future__ import annotations

import os
import sys
import json
import re
import urllib.request
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from modules.dispatch.compat import invoke_with_urlopen

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TZ_NAME = os.environ.get("AGENT_FORGE_USER_TZ", "Asia/Shanghai")
_MAX_CONTENT_CHARS = 8000
_TIMEOUT = 30

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
    try:
        from modules.search.privacy import redact_outbound
        redacted, _ = redact_outbound(content)
        return redacted
    except ImportError:
        return content


def _resolve_model() -> str:
    """Preserve the Dispatch Guard policy seam used by existing callers."""
    try:
        from modules.dispatch import guard
        return guard.resolve_model(
            "action_extraction",
            caller="action_extractor._call_api",
        )
    except ImportError:
        print(
            "[action_extractor] WARNING: dispatch guard not available, "
            "falling back to deepseek-chat",
            file=sys.stderr,
        )
        return "deepseek-chat"


def _model_error(error: str | None) -> RuntimeError:
    text = str(error or "unknown model error")
    match = re.search(r"HTTP Error (\d+):?\s*(.*)", text)
    if match:
        return RuntimeError(
            f"DeepSeek API error {match.group(1)}: {match.group(2)}".rstrip()
        )
    lowered = text.lower()
    if "urlopen error" in lowered or "url error" in lowered:
        return RuntimeError(f"DeepSeek API unreachable: {text}")
    if "timeout" in lowered or "timed out" in lowered:
        return RuntimeError(f"DeepSeek API timeout: {text}")
    return RuntimeError(f"DeepSeek API error: {text}")


def _call_api(content: str, api_key: str) -> str:
    """Call the unified Gateway and return the raw JSON response text."""
    truncated = content[:_MAX_CONTENT_CHARS]
    redacted = _redact_pii(truncated)
    model = _resolve_model()
    response = invoke_with_urlopen(
        task_type="action_extraction",
        messages=(
            {"role": "user", "content": _EXTRACTION_PROMPT + redacted},
        ),
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=model,
        max_tokens=2048,
        temperature=0.1,
        timeout_seconds=float(_TIMEOUT),
        max_retries=0,
        metadata={"caller": "orchestrator.action_extractor"},
        extra_body={"response_format": {"type": "json_object"}},
        record_run=False,
    )
    if not response.success:
        raise _model_error(response.error)
    return response.content


def _validate_due_at(due_at) -> tuple:
    from modules.common.time_utils import to_utc_iso, parse_iso_with_tz

    original = due_at if isinstance(due_at, str) else None
    if due_at is None or (isinstance(due_at, str) and not due_at.strip()):
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
    try:
        parsed = parse_iso_with_tz(text, default_timezone=_DEFAULT_TZ_NAME)
    except (ValueError, TypeError) as exc:
        return (None, None, original,
                f"due_at '{text}' is not valid ISO 8601: {exc}")

    was_naive = not re.search(r'[+-]\d{2}:\d{2}$|Z$', text)
    source_tz = _DEFAULT_TZ_NAME if was_naive else None
    utc_iso, _, _ = to_utc_iso(parsed, original=original)
    return utc_iso, source_tz, original, None


def _parse_extraction_response(response_text: str) -> list:
    if not response_text or not response_text.strip():
        return []

    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.startswith("```")]
        text = "\n".join(lines)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            return []

    if isinstance(parsed, dict):
        if "actions" in parsed:
            actions = parsed["actions"]
            if not isinstance(actions, list):
                return []
            parsed = actions
        elif "items" in parsed:
            import warnings
            warnings.warn(
                "API returned {'items': [...]} (deprecated); "
                "use {'actions': [...]} instead",
                DeprecationWarning,
                stacklevel=2,
            )
            parsed = parsed["items"]
        else:
            parsed = [parsed]
    elif isinstance(parsed, list):
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

    valid_items = []
    valid_priorities = {"low", "medium", "high", "critical"}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        priority = str(item.get("priority", "medium")).lower()
        if priority not in valid_priorities:
            priority = "medium"

        raw_due_at = item.get("due_at")
        validated_due_at, source_tz, original_due_at, due_at_error = _validate_due_at(raw_due_at)
        if due_at_error:
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
            "due_at_utc": validated_due_at,
            "source_timezone": source_tz,
            "original_due_at": original_due_at,
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
    if not markdown_content or not markdown_content.strip():
        return {"status": "no_actions", "items": [], "error": None}

    if api_key is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {
            "status": "model_error",
            "items": [],
            "error": "DEEPSEEK_API_KEY not set",
        }

    try:
        response_text = _call_api(markdown_content, api_key)
    except RuntimeError as exc:
        return {
            "status": "model_error",
            "items": [],
            "error": str(exc),
        }

    items = _parse_extraction_response(response_text)
    if not items:
        return {"status": "no_actions", "items": [], "error": None}

    has_validation_errors = any(item.get("validation_error") for item in items)
    storage_errors = []
    if write_to_db:
        try:
            from modules.orchestrator.schedule_store import add_schedule
        except ImportError as exc:
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
                    tb = traceback.format_exc()
                    storage_errors.append(
                        f"Failed to store '{item.get('title', '?')}': {exc}\n{tb}"
                    )

    if storage_errors and has_validation_errors:
        status = "validation_error"
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
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            content = handle.read()
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
    print(json.dumps(extract_from_file(sys.argv[1]), indent=2, ensure_ascii=False))
