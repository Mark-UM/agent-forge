#!/usr/bin/env python3
"""Auto-generate _docs/TEST_REPORT.generated.md from a pytest JSON report.

Reads a pytest-json-report JSON file and produces a structured Markdown report
that distinguishes:
  - test function count
  - parametrized case count
  - logical assertion count (approximated by counting `assert` statements in
    the collected test source files)
  - skipped reasons
  - external integration execution status (heuristic: tests that touch network
    or external services, reported by markers or name patterns)

Usage:
    python -m scripts.generate_test_report \\
        --json _runtime/baseline.json \\
        --out  _docs/TEST_REPORT.generated.md

The report is fully reproducible from the JSON; no hand-edited numbers.
"""
import argparse
import ast
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_report(json_path: Path) -> dict:
    if not json_path.exists():
        raise FileNotFoundError(f"pytest JSON report not found: {json_path}")
    return json.loads(json_path.read_text(encoding='utf-8'))


def _count_asserts_in_file(path: Path) -> int:
    """Count `assert` statements in a Python test file via AST."""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            count += 1
    return count


def _is_parametrized(test_node: dict) -> bool:
    """A parametrized test has a `parametrize` marker or `[...]` in nodeid."""
    markers = test_node.get('markers', []) or []
    for m in markers:
        if isinstance(m, dict) and 'parametrize' in (m.get('name', '') or ''):
            return True
        if isinstance(m, str) and 'parametrize' in m:
            return True
    return bool(re.search(r'\[[^\]]+\]', test_node.get('nodeid', '')))


def _classify_skip(test_node: dict) -> str:
    """Return a human-readable skip reason category."""
    markers = test_node.get('markers', []) or []
    for m in markers:
        name = m.get('name', '') if isinstance(m, dict) else str(m)
        if name in ('skip', 'skipif', 'xfail'):
            return name
    call = test_node.get('call', {}) or {}
    longrepr = call.get('longrepr', '') or ''
    if 'skip' in str(longrepr).lower():
        return 'explicit_skip'
    return 'unknown'


def _is_external_integration(test_node: dict) -> bool:
    """Heuristic: test calls external services (network/API)."""
    nodeid = test_node.get('nodeid', '').lower()
    keywords = (
        'mcp', 'serper', 'searxng', 'arxiv', 'semantic_scholar',
        'fetch', 'network', 'e2e', 'smoke', 'live', 'integration',
        'playwright', 'browser', 'vision', 'siliconflow', 'chromadb',
    )
    if any(k in nodeid for k in keywords):
        return True
    markers = test_node.get('markers', []) or []
    for m in markers:
        name = m.get('name', '') if isinstance(m, dict) else str(m)
        if name in ('integration', 'e2e', 'slow', 'network'):
            return True
    return False


def _find_test_source(nodeid: str, root: Path) -> Path | None:
    """Map a pytest nodeid to its source file under modules/."""
    # nodeid like: modules/search/tests/test_aggregator.py::TestX::test_y[param]
    rel = nodeid.split('::', 1)[0]
    candidate = root / rel
    if candidate.exists():
        return candidate
    return None


def generate_report(json_path: Path, out_path: Path, project_root: Path) -> dict:
    report = _load_report(json_path)
    tests = report.get('tests', [])
    summary = report.get('summary', {})

    passed = [t for t in tests if t.get('outcome') == 'passed']
    failed = [t for t in tests if t.get('outcome') == 'failed']
    skipped = [t for t in tests if t.get('outcome') == 'skipped']
    errors = [t for t in tests if t.get('outcome') == 'error']

    # Parametrized vs non-parametrized
    parametrized = [t for t in tests if _is_parametrized(t)]
    # Test functions (unique file::class::function without params)
    seen_functions = set()
    for t in tests:
        nodeid = t.get('nodeid', '')
        # strip parametrize bracket
        base = re.sub(r'\[[^\]]*\]$', '', nodeid)
        seen_functions.add(base)
    function_count = len(seen_functions)

    # Logical assertions across all collected test sources
    assert_count = 0
    scanned_files = set()
    for t in tests:
        src = _find_test_source(t.get('nodeid', ''), project_root)
        if src and str(src) not in scanned_files:
            scanned_files.add(str(src))
            assert_count += _count_asserts_in_file(src)

    # Skip reasons
    skip_reasons = Counter(_classify_skip(t) for t in skipped)

    # External integration tests
    external_tests = [t for t in tests if _is_external_integration(t)]
    external_passed = [t for t in external_tests if t.get('outcome') == 'passed']
    external_skipped = [t for t in external_tests if t.get('outcome') == 'skipped']

    # Per-module breakdown
    module_counts = Counter()
    for t in tests:
        nodeid = t.get('nodeid', '')
        m = re.match(r'modules/([^/]+)/', nodeid)
        if m:
            module_counts[m.group(1)] += 1

    # Duration
    duration_s = report.get('duration', 0)
    duration_str = f"{duration_s:.2f}s" if duration_s else 'unknown'

    # Environment
    env = report.get('environment', {}) or {}
    python_version = env.get('python_version', 'unknown')
    platform = env.get('platform', 'unknown')

    generated_at = datetime.now(timezone.utc).isoformat()

    md = []
    md.append('# TEST_REPORT.generated.md — Auto-Generated Test Report')
    md.append('')
    md.append(f'> Generated: {generated_at}')
    md.append(f'> Source JSON: `{json_path.relative_to(project_root) if json_path.is_relative_to(project_root) else json_path}`')
    md.append(f'> Python: {python_version} on {platform}')
    md.append(f'> Duration: {duration_str}')
    md.append('')
    md.append('This report is regenerated from the pytest JSON report. Do not '
              'hand-edit; rerun `scripts/generate_test_report.py` instead.')
    md.append('')

    md.append('## Summary')
    md.append('')
    md.append('| Metric | Value |')
    md.append('|--------|-------|')
    md.append(f'| Collected | {summary.get("collected", len(tests))} |')
    md.append(f'| Total executed | {summary.get("total", len(tests))} |')
    md.append(f'| Passed | {summary.get("passed", len(passed))} |')
    md.append(f'| Failed | {summary.get("failed", len(failed))} |')
    md.append(f'| Skipped | {summary.get("skipped", len(skipped))} |')
    md.append(f'| Errors | {summary.get("errors", len(errors))} |')
    md.append(f'| Unique test functions | {function_count} |')
    md.append(f'| Parametrized cases | {len(parametrized)} |')
    md.append(f'| Logical assertions (AST `assert` count) | {assert_count} |')
    md.append(f'| Test source files scanned | {len(scanned_files)} |')
    md.append('')

    md.append('## Skipped Reasons')
    md.append('')
    if skip_reasons:
        md.append('| Reason | Count |')
        md.append('|--------|-------|')
        for reason, count in skip_reasons.most_common():
            md.append(f'| {reason} | {count} |')
    else:
        md.append('No skipped tests.')
    md.append('')

    md.append('## External Integration Tests')
    md.append('')
    md.append('Heuristic: tests whose nodeid or markers reference MCP providers, '
              'network calls, E2E/smoke flows, browser, vision, or ChromaDB.')
    md.append('')
    md.append('| Metric | Value |')
    md.append('|--------|-------|')
    md.append(f'| Identified external tests | {len(external_tests)} |')
    md.append(f'| External tests passed | {len(external_passed)} |')
    md.append(f'| External tests skipped | {len(external_skipped)} |')
    if external_tests:
        md.append('')
        md.append('### External Test Nodeids (sample, first 20)')
        md.append('')
        for t in external_tests[:20]:
            md.append(f"- `{t.get('nodeid', '')}` → {t.get('outcome', '')}")
        if len(external_tests) > 20:
            md.append(f'- ... and {len(external_tests) - 20} more')
    md.append('')

    md.append('## Per-Module Test Distribution')
    md.append('')
    md.append('| Module | Test count |')
    md.append('|--------|------------|')
    for mod, count in sorted(module_counts.items(), key=lambda kv: -kv[1]):
        md.append(f'| `modules/{mod}/` | {count} |')
    md.append('')

    md.append('## Reproduction')
    md.append('')
    md.append('```bash')
    md.append('# 1. Run pytest with JSON report')
    md.append('python -m pytest --json-report --json-report-file=_runtime/baseline.json --tb=no -q')
    md.append('')
    md.append('# 2. Generate this report')
    md.append('python -m scripts.generate_test_report \\')
    md.append('    --json _runtime/baseline.json \\')
    md.append('    --out  _docs/TEST_REPORT.generated.md')
    md.append('```')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(md) + '\n', encoding='utf-8')

    return {
        'function_count': function_count,
        'parametrized_count': len(parametrized),
        'assert_count': assert_count,
        'external_count': len(external_tests),
        'skipped_reasons': dict(skip_reasons),
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description='Generate _docs/TEST_REPORT.generated.md from a pytest JSON report.'
    )
    parser.add_argument('--json', type=Path, default=PROJECT_ROOT / '_runtime' / 'baseline.json',
                        help='Path to pytest-json-report JSON file')
    parser.add_argument('--out', type=Path, default=PROJECT_ROOT / '_docs' / 'TEST_REPORT.generated.md',
                        help='Output Markdown path')
    parser.add_argument('--project-root', type=Path, default=PROJECT_ROOT,
                        help='Project root for resolving test source files')
    args = parser.parse_args()

    stats = generate_report(args.json, args.out, args.project_root)
    print(f'Wrote {args.out}')
    print(f'  Functions:        {stats["function_count"]}')
    print(f'  Parametrized:     {stats["parametrized_count"]}')
    print(f'  Asserts (AST):    {stats["assert_count"]}')
    print(f'  External tests:   {stats["external_count"]}')
    print(f'  Skip reasons:     {stats["skipped_reasons"]}')
    return 0


if __name__ == '__main__':
    sys.exit(_cli())
