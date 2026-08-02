"""integration_check module (Phase 3B, Direction 3).

Pre-delivery static analysis that catches integration breaks.

Public API:
    run_checks(project_root: Path, whitelist: dict = None) -> dict
    format_report_markdown(result: dict) -> str
    load_whitelist(path: Path) -> dict
"""
from pathlib import Path
from .checker import run_checks, format_report_markdown, load_whitelist, Finding, Severity
from .reporter import format_report_json, format_report_table, summarize

__all__ = [
    'run_checks',
    'format_report_markdown',
    'format_report_json',
    'format_report_table',
    'summarize',
    'load_whitelist',
    'Finding',
    'Severity',
]
__version__ = '1.0.0'
