"""Task classification with heuristic and Model Gateway modes.

Two modes:
- heuristic (default, zero cost): keyword rule matching
- flash (optional): Gateway-routed classification, falling back to heuristic

The model mode returns strict JSON. Any network, gateway, parsing, or validation
failure returns ``None`` so callers retain the deterministic heuristic fallback.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from . import context
from modules.dispatch.gateway import GatewayRequest, ModelGateway


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
    return "coding"


def _gateway_transport(url, headers, body, timeout):
    request = urllib.request.Request(
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("classification response must be a JSON object")
    return payload


def _strip_markdown_fences(content: str) -> str:
    value = content.strip()
    if not value.startswith("```"):
        return value
    parts = value.split("\n", 1)
    if len(parts) > 1:
        return parts[1].rsplit("```", 1)[0].strip()
    if value.endswith("```"):
        return value[3:-3].strip()
    return value[3:].strip()


def classify_flash(message: str, timeout: int = 15) -> Optional[str]:
    """Gateway-routed classification. Returns ``None`` on any failure."""
    if not os.environ.get("DEEPSEEK_API_KEY", ""):
        return None

    prompt = CLASSIFY_PROMPT_TEMPLATE.format(user_message=message)
    try:
        response = ModelGateway(
            env=os.environ,
            transport=_gateway_transport,
        ).invoke(
            GatewayRequest(
                task_type="classify",
                messages=({"role": "user", "content": prompt},),
                temperature=0.0,
                max_tokens=100,
                timeout_seconds=float(timeout),
                max_retries=0,
                metadata={"caller": "prompt.classify"},
            ),
            record_run=False,
        )
        if not response.success:
            return None
        result = json.loads(_strip_markdown_fences(response.content))
        task = str(result.get("task_type", "")).strip().lower()
        return task if task in VALID_TASK_TYPES else None
    except Exception:
        return None


def classify(message: str, mode: str = "heuristic") -> str:
    """Classify a task and always return a valid task type."""
    if mode == "flash":
        result = classify_flash(message)
        if result:
            return result
    return classify_heuristic(message)
