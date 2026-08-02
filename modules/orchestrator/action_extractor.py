#!/usr/bin/env python3
"""C2: Action Extractor — 从 Markdown 报告中抽取待办/截止日期/关键决策。

Uses DeepSeek Flash API (deepseek-chat) to extract structured action items
from Markdown content, then writes them to the schedules table.

Design:
    - 复用 modules/search/summarize.py 的 DeepSeek API 调用模式
    - PII 脱敏：content 出境前调用 privacy.redact_outbound()
    - JSON output 格式：[{title, due_at, priority, description}]
    - 失败时返回空列表（不阻塞 pipeline）

Usage:
    from modules.orchestrator.action_extractor import extract_action_items

    items = extract_action_items(markdown_content, source_ref="/reports/weekly.md")
    # items: [{title, due_at, priority, description, schedule_id}]
"""
import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── DeepSeek API config ────────────────────────────────────────
_FLASH_API = "https://api.deepseek.com/v1/chat/completions"
_FLASH_MODEL = "deepseek-chat"
_MAX_CONTENT_CHARS = 8000  # Limit content to avoid token overflow
_TIMEOUT = 30

# ── Extraction prompt ──────────────────────────────────────────
_EXTRACTION_PROMPT = """你是一个待办事项抽取助手。从以下 Markdown 内容中抽取所有的待办事项、截止日期、重要决策。

输出格式：严格的 JSON 数组，每个元素包含：
- title: 简短标题（不超过 50 字）
- due_at: 截止日期 ISO 8601 格式（带时区，如 2026-08-01T23:59:00+08:00）。如果没有明确截止日期，设为 null
- priority: 优先级，必须是 low/medium/high/critical 之一
- description: 详细描述（可选）

如果内容中没有待办事项，返回空数组 []。

只输出 JSON 数组，不要其他文字。

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


def _call_flash_api(content: str, api_key: str) -> str:
    """Call DeepSeek Flash API to extract action items.

    Returns:
        str: Raw API response text (expected JSON array)
    """
    # Truncate content to avoid token overflow
    truncated = content[:_MAX_CONTENT_CHARS]

    # PII redaction
    redacted = _redact_pii(truncated)

    payload = {
        "model": _FLASH_MODEL,
        "messages": [
            {"role": "user", "content": _EXTRACTION_PROMPT + redacted}
        ],
        "max_tokens": 2048,
        "temperature": 0.1,  # Low temperature for structured extraction
        "response_format": {"type": "json_object"},  # Force JSON output
    }

    req = urllib.request.Request(
        _FLASH_API,
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
        raise RuntimeError(f"DeepSeek API error {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"DeepSeek API unreachable: {e}")


def _parse_extraction_response(response_text: str) -> list:
    """Parse the API response into a list of action items.

    Handles both raw JSON array and {"items": [...]} formats.
    """
    if not response_text or not response_text.strip():
        return []

    text = response_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last line (fences)
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the text
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    # Handle {"items": [...]} format
    if isinstance(parsed, dict):
        if "items" in parsed:
            parsed = parsed["items"]
        else:
            # Single item as dict
            parsed = [parsed]

    if not isinstance(parsed, list):
        return []

    # Validate and clean each item
    valid_items = []
    valid_priorities = {"low", "medium", "high", "critical"}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "").strip()
        if not title:
            continue
        # Normalize priority
        priority = item.get("priority", "medium").lower()
        if priority not in valid_priorities:
            priority = "medium"
        valid_items.append({
            "title": title[:200],  # Limit title length
            "due_at": item.get("due_at"),
            "priority": priority,
            "description": item.get("description", ""),
        })

    return valid_items


def extract_action_items(
    markdown_content: str,
    source_ref: str = "",
    api_key: Optional[str] = None,
    write_to_db: bool = True,
) -> list:
    """Extract action items from Markdown content.

    Args:
        markdown_content: Markdown text to extract from
        source_ref: Reference path (e.g., report file path)
        api_key: DeepSeek API key (default: from env DEEPSEEK_API_KEY)
        write_to_db: If True, write extracted items to schedules table

    Returns:
        list[dict]: Extracted action items with schedule_id if written to DB
    """
    if not markdown_content or not markdown_content.strip():
        return []

    if api_key is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return [{"error": "DEEPSEEK_API_KEY not set"}]

    try:
        response_text = _call_flash_api(markdown_content, api_key)
    except RuntimeError:
        return []  # API failure, return empty (don't block pipeline)

    items = _parse_extraction_response(response_text)

    # Write to database
    if write_to_db and items:
        try:
            from modules.orchestrator.schedule_store import add_schedule
            from datetime import datetime, timezone, timedelta

            for item in items:
                # Default due_at: 7 days from now if not specified
                due_at = item.get("due_at")
                if not due_at:
                    due_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

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
                except (ValueError, Exception):
                    # Skip invalid items
                    pass
        except ImportError:
            pass  # schedule_store not available, skip DB write

    return items


def extract_from_file(file_path: str, api_key: Optional[str] = None) -> list:
    """Extract action items from a Markdown file.

    Args:
        file_path: Path to Markdown file
        api_key: DeepSeek API key

    Returns:
        list[dict]: Extracted action items
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, UnicodeDecodeError):
        return []

    return extract_action_items(content, source_ref=file_path, api_key=api_key)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m modules.orchestrator.action_extractor <file_path>")
        sys.exit(0)

    file_path = sys.argv[1]
    items = extract_from_file(file_path)
    print(json.dumps(items, indent=2, ensure_ascii=False))
