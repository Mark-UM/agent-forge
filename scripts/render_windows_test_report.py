#!/usr/bin/env python3
"""Generate Markdown and JSON test reports from a pytest JUnit XML file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def _integer(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def read_summary(path: Path) -> dict[str, object]:
    root = ET.parse(path).getroot()
    suites = list(root.iter("testsuite"))
    if not suites:
        raise ValueError(f"no testsuite element found in {path}")

    tests = sum(_integer(suite.get("tests")) for suite in suites)
    failures = sum(_integer(suite.get("failures")) for suite in suites)
    errors = sum(_integer(suite.get("errors")) for suite in suites)
    skipped = sum(_integer(suite.get("skipped")) for suite in suites)
    duration = sum(float(suite.get("time") or 0.0) for suite in suites)
    return {
        "tests": tests,
        "passed": tests - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "duration_seconds": round(duration, 3),
    }


def render_markdown(
    summary: dict[str, object],
    *,
    commit: str,
    run_id: str,
    platform: str,
) -> str:
    success = summary["failures"] == 0 and summary["errors"] == 0
    status = "PASS" if success else "FAIL"
    generated_at = datetime.now(timezone.utc).isoformat()
    return f"""# Agent Forge Test Report

This report was generated from the pytest JUnit artifact produced by CI. It is
an execution record, not a manually maintained test-count claim.

| Field | Value |
|---|---|
| Status | **{status}** |
| Tested commit | `{commit}` |
| GitHub Actions run | `{run_id}` |
| Platform | `{platform}` |
| Generated at | `{generated_at}` |
| Tests | {summary['tests']} |
| Passed | {summary['passed']} |
| Failed assertions | {summary['failures']} |
| Errors | {summary['errors']} |
| Skipped | {summary['skipped']} |
| Duration | {summary['duration_seconds']} seconds |

## Release interpretation

A release candidate is valid only when the Windows fast contracts, full pytest
suite, strict Registry validation, Model Gateway no-bypass check, and clean
Windows Vendor/Chromium/Supervisor smoke all pass for the same branch head.

The XML and JSON files packaged with this report remain the machine-readable
sources of truth.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--platform", default="windows-latest / Python 3.11")
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--json", dest="json_path", type=Path, required=True)
    args = parser.parse_args()

    summary = read_summary(args.junit)
    payload = {
        **summary,
        "success": summary["failures"] == 0 and summary["errors"] == 0,
        "tested_commit": args.commit,
        "github_actions_run": args.run_id,
        "platform": args.platform,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(
        render_markdown(
            summary,
            commit=args.commit,
            run_id=args.run_id,
            platform=args.platform,
        ),
        encoding="utf-8",
        newline="\n",
    )
    args.json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
