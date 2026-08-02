"""
Task Classifier — task type classification (v1.5 P0)

Two modes:
- heuristic (default, zero cost): keyword rule matching
- flash (optional): DeepSeek V4 Flash classification, falls back to heuristic on failure

Flash mode outputs strict JSON; on any failure (network, parsing, invalid result),
returns None and the caller falls back to heuristic.

Phase 2: classify is in the Flash ALLOWED task list (utility, simple, fast).
The flash_guard.check_task_allowed("classify") returns True, so classify_flash
runs on Flash unchanged. The guard is wired in for observability — if the
allowlist changes, classify will automatically redirect to Pro.
"""
import json
import os
import urllib.request
from typing import Optional

from . import context
from modules.dispatch import guard


CLASSIFY_PROMPT_TEMPLATE = """You are a task classifier. Given the user message, classify it into exactly one task type.

## Task Types
- coding: implementing features, fixing bugs, adding tests, refactoring
- review: code review, PR review, diff review
- research: web search, technical research, comparison
- debugging: bug investigation, crash analysis, error tracing
- planning: design, architecture, large feature planning
- writing: documentation, reports, prose
- automation: browser automation, system automation

## Output Format (STRICT JSON, no markdown fences)

{{
  "task_type": "<one of: coding|review|research|debugging|planning|writing|automation>",
  "confidence": <0.0-1.0>,
  "rationale": "<one short sentence>"
}}

## User Message
{user_message}
"""

VALID_TASK_TYPES = {
    "coding", "review", "research", "debugging",
    "planning", "writing", "automation",
}


def classify_heuristic(message: str) -> str:
    """Heuristic classification via keyword matching."""
    signals = context.detect_context_signals(message)
    if signals["task_hints"]:
        return signals["task_hints"][0]
    return "coding"  # default


def classify_flash(message: str, timeout: int = 15) -> Optional[str]:
    """Flash model classification. Returns None on any failure.

    Phase 2: Uses guard.resolve_model("classify") to determine which model
    to use. classify is in the Flash allowed list, so this normally returns
    deepseek-chat. If the allowlist is ever changed, the guard will
    auto-redirect to Pro with a warning log.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        # 仅使用 DEEPSEEK_API_KEY，避免将 Anthropic token 误发给 DeepSeek
        return None

    # Phase 2: resolve model via guard (classify is allowlisted → Flash)
    model = guard.resolve_model("classify", caller="classify.classify_flash")
    prompt = CLASSIFY_PROMPT_TEMPLATE.format(user_message=message)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 100,
        "stream": False,
    }

    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        # Tolerate markdown fences: ```json\n...\n``` or ```\n...\n``` or ```...```
        if content.startswith("```"):
            parts = content.split("\n", 1)
            if len(parts) > 1:
                # Multi-line: ```lang\n<body>\n```
                content = parts[1].rsplit("```", 1)[0]
            elif content.endswith("```"):
                # Single-line: ```<body>```
                content = content[3:-3]
            else:
                # Malformed: ```<body> (no closing fence)
                content = content[3:]
        result = json.loads(content)
        task = result.get("task_type", "").strip().lower()
        if task in VALID_TASK_TYPES:
            return task
        return None
    except Exception:
        return None


def classify(message: str, mode: str = "heuristic") -> str:
    """
    Classify task type.

    Args:
        message: user message
        mode: "heuristic" | "flash"

    Returns:
        task_type string (always returns a valid type; falls back to heuristic on flash failure)
    """
    if mode == "flash":
        result = classify_flash(message)
        if result:
            return result
        # Fall back to heuristic silently
    return classify_heuristic(message)
