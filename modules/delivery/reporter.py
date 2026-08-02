"""modules.delivery.reporter — formatters for delivery checklist output.

Provides Markdown, JSON, and table formatters for the result dict from
`modules.delivery.checklist.run_delivery_checklist`.
"""
import json
from collections import defaultdict


def format_report_json(result: dict) -> str:
    """Format result as indented JSON string."""
    return json.dumps(result, indent=2, ensure_ascii=False)


def format_summary_table(result: dict) -> str:
    """Format the per-category summary as a Markdown table.

    Returns a string like:
        | Category | Errors | Warnings | Info | Status |
        |----------|--------|----------|------|--------|
        | engineering | 2 | 0 | 0 | FAIL |
        ...
    """
    lines = []
    lines.append('| Category | Errors | Warnings | Info | Status |')
    lines.append('|----------|--------|----------|------|--------|')
    for cat, stats in result.get('categories', {}).items():
        status = 'PASS' if stats['pass'] else 'FAIL'
        lines.append(
            f"| {cat} | {stats['errors']} | {stats['warnings']} | "
            f"{stats['infos']} | {status} |"
        )
    s = result.get('summary', {})
    overall = 'PASS' if s.get('pass') else 'FAIL'
    lines.append(
        f"| **OVERALL** | **{s.get('errors', 0)}** | **{s.get('warnings', 0)}** | "
        f"**{s.get('infos', 0)}** | **{overall}** |"
    )
    return '\n'.join(lines)


def format_report_markdown(result: dict) -> str:
    """Format full result as a Markdown report.

    Includes:
    - Header with project root
    - Per-category summary table
    - Detailed findings table (if any)
    - Overall PASS/FAIL verdict
    """
    lines = []
    lines.append('## Delivery Checklist Report')
    lines.append('')
    lines.append(f'**Project**: `{result.get("project_root", "")}`')
    lines.append('')

    # Summary table
    lines.append('### Per-Category Summary')
    lines.append('')
    lines.append(format_summary_table(result))
    lines.append('')

    # Findings detail
    findings = result.get('findings', [])
    if not findings:
        lines.append('✅ No findings. All checks passed.')
        lines.append('')
    else:
        lines.append('### Findings Detail')
        lines.append('')
        lines.append('| # | Category | Severity | File:Line | Finding |')
        lines.append('|---|----------|----------|-----------|---------|')
        for i, f in enumerate(findings, 1):
            sev = f.get('severity', 'error').upper()
            location = f"{f.get('file', '')}:{f.get('line', 0)}"
            finding_text = f.get('finding', '').replace('|', '\\|')
            lines.append(
                f"| {i} | {f.get('category', '')} | {sev} | "
                f"{location} | {finding_text} |"
            )
        lines.append('')

    # Overall verdict
    s = result.get('summary', {})
    overall = 'PASS' if s.get('pass') else 'FAIL'
    lines.append(
        f'## Overall: {overall} '
        f'({s.get("errors", 0)} errors, {s.get("warnings", 0)} warnings, '
        f'{s.get("infos", 0)} info)'
    )
    if not s.get('pass'):
        lines.append('')
        lines.append('⚠️  Project is NOT ready for delivery. Resolve all ERROR-severity findings.')

    return '\n'.join(lines)


def group_by_category(result: dict) -> dict:
    """Group findings by category. Returns {category: [findings]}."""
    grouped = defaultdict(list)
    for f in result.get('findings', []):
        grouped[f.get('category', '')].append(f)
    return dict(grouped)


def group_by_severity(result: dict) -> dict:
    """Group findings by severity. Returns {severity: [findings]}."""
    grouped = defaultdict(list)
    for f in result.get('findings', []):
        grouped[f.get('severity', '')].append(f)
    return dict(grouped)


def summarize(result: dict) -> str:
    """One-line summary: 'PASS: 0 errors, 2 warnings, 1 info' or 'FAIL: ...'."""
    s = result.get('summary', {})
    status = 'PASS' if s.get('pass') else 'FAIL'
    return (
        f"{status}: {s.get('errors', 0)} errors, "
        f"{s.get('warnings', 0)} warnings, {s.get('infos', 0)} info"
    )
