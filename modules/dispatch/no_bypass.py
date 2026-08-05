#!/usr/bin/env python3
"""Fail CI when first-party business code bypasses Model Gateway.

The check is intentionally narrow and cheap. It does not police networking in
general; it only prevents DeepSeek endpoint ownership from spreading back into
Prompt, Search, Orchestrator, or other business modules. The canonical gateway
owns transport; this checker is allow-listed only because it necessarily stores
the endpoint-detection regexes themselves.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = PROJECT_ROOT / "modules"
ALLOWED_PATHS = frozenset(
    {
        Path("modules/dispatch/gateway.py"),
        Path("modules/dispatch/no_bypass.py"),
    }
)
FORBIDDEN_PATTERNS = (
    ("deepseek endpoint domain", re.compile(r"api\.deepseek\.com", re.IGNORECASE)),
    (
        "deepseek chat completions path",
        re.compile(r"(?:deepseek|DEEPSEEK)[\s\S]{0,300}/(?:v1/)?chat/completions"),
    ),
)


@dataclass(frozen=True)
class BypassFinding:
    path: str
    line: int
    rule: str
    excerpt: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "rule": self.rule,
            "excerpt": self.excerpt,
        }


def _python_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        relative_parts = path.relative_to(root).parts
        if "tests" in relative_parts or "__pycache__" in relative_parts:
            continue
        yield path


def scan_model_bypasses(
    *,
    project_root: Path = PROJECT_ROOT,
    modules_root: Optional[Path] = None,
    allowed_paths: Iterable[Path] = ALLOWED_PATHS,
) -> list[BypassFinding]:
    """Return endpoint ownership violations without mutating the tree."""

    project_root = project_root.resolve()
    scan_root = (modules_root or project_root / "modules").resolve()
    allowed = {Path(value).as_posix() for value in allowed_paths}
    findings: list[BypassFinding] = []

    for path in _python_files(scan_root):
        try:
            relative = path.resolve().relative_to(project_root).as_posix()
        except ValueError:
            relative = path.resolve().as_posix()
        if relative in allowed:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        for rule, pattern in FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                source_line = lines[line - 1].strip() if line <= len(lines) else ""
                findings.append(
                    BypassFinding(
                        path=relative,
                        line=line,
                        rule=rule,
                        excerpt=source_line[:240],
                    )
                )
    return findings


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that DeepSeek requests go through Model Gateway"
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    findings = scan_model_bypasses(project_root=args.project_root)
    if not findings:
        print("Model Gateway no-bypass check: OK")
        return 0
    print("Model Gateway no-bypass check: FAILED")
    for finding in findings:
        print(
            f"  {finding.path}:{finding.line}: {finding.rule}: "
            f"{finding.excerpt}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
