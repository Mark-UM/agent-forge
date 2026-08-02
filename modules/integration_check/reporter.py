#!/usr/bin/env python3
"""integration_check.reporter — output formatters (Phase 3B Direction 3).

Provides:
- format_report_json(result) — parseable JSON
- format_report_table(result) — Markdown table (alias of format_report_markdown)
- summarize(result) — one-line summary string
"""
import json
from typing import Optional

from .checker import format_report_markdown


def format_report_json(result: dict) -> str:
    """Format result as indented JSON string."""
    return json.dumps(result, indent=2, ensure_ascii=False)


def format_report_table(result: dict) -> str:
    """Format result as Markdown table. Alias for format_report_markdown."""
    return format_report_markdown(result)


def summarize(result: dict) -> str:
    """One-line summary: PASS/FAIL (X errors, Y warnings, Z info)."""
    s = result.get('summary', {})
    status = 'PASS' if s.get('pass', False) else 'FAIL'
    return (
        f"{status} "
        f"({s.get('errors', 0)} errors, "
        f"{s.get('warnings', 0)} warnings, "
        f"{s.get('infos', 0)} info)"
    )


def group_by_rule(result: dict) -> dict:
    """Group findings by rule name.

    Returns:
        dict: {rule_name: [finding_dict, ...]}
    """
    grouped = {}
    for f in result.get('findings', []):
        rule = f.get('rule', 'unknown')
        grouped.setdefault(rule, []).append(f)
    return grouped


def group_by_severity(result: dict) -> dict:
    """Group findings by severity.

    Returns:
        dict: {'error': [...], 'warning': [...], 'info': [...]}
    """
    grouped = {'error': [], 'warning': [], 'info': []}
    for f in result.get('findings', []):
        sev = f.get('severity', 'info')
        grouped.setdefault(sev, []).append(f)
    return grouped
