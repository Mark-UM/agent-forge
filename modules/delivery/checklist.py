#!/usr/bin/env python3
"""modules.delivery.checklist — final delivery gate (Phase 3C Direction 8).

Runs 6 check categories against a target project and produces a consolidated
PASS/FAIL report. Stdlib only.

Categories:
1. engineering   — file existence + mandatory content markers
2. resource      — manifest.json references, no inline onclick in index.html
3. integration   — delegates to modules.integration_check.checker
4. i18n          — UIManager imports { t }, setLocale triggers UI refresh
5. test_quality  — vitest coverage thresholds + no soft assertions
6. architecture  — no any / default export / window globals / Core imports three

Usage:
    python -m modules.delivery.checklist --target /path/to/project
    python -m modules.delivery.checklist --target . --json
    python -m modules.delivery.checklist --target . \
        --whitelist .opencode/delivery-whitelist.json \
        --integration-whitelist .opencode/integration-whitelist.json
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# Import integration_check (Phase 3B) for category 3 delegation
_MODULE_ROOT = Path(__file__).resolve().parent.parent
if str(_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_MODULE_ROOT))

try:
    from modules.integration_check.checker import (
        run_checks as run_integration_checks,
        load_whitelist as load_integration_whitelist,
        _empty_whitelist as _empty_integration_whitelist,
    )
    _HAS_INTEGRATION_CHECK = True
except ImportError:
    _HAS_INTEGRATION_CHECK = False


class Severity(str, Enum):
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


@dataclass
class DeliveryFinding:
    category: str
    severity: str
    file: str
    line: int
    finding: str
    detail: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


# ── Whitelist ────────────────────────────────────────────────

def _empty_whitelist() -> dict:
    return {
        'engineering': [],
        'resource': [],
        'integration': [],  # used for reference; actual integration whitelist is separate
        'i18n': [],
        'test_quality': [],
        'architecture': [],
    }


def load_whitelist(path: Path) -> dict:
    """Load delivery-whitelist.json. Returns empty dict on missing/invalid."""
    if not path.exists():
        return _empty_whitelist()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        for key in _empty_whitelist():
            if key not in data:
                data[key] = []
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_whitelist()


# ── Helpers ──────────────────────────────────────────────────

def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''


def _find_files(project_root: Path, glob_pattern: str) -> list:
    return sorted(project_root.glob(glob_pattern))


def _line_of(content: str, char_pos: int) -> int:
    return content.count('\n', 0, char_pos) + 1


def _normalize_rel_path(rel_path: str) -> str:
    """Normalize to forward slashes for cross-platform whitelist matching."""
    return rel_path.replace('\\', '/')


def _strip_ts_comments(content: str) -> str:
    """Remove // and /* */ comments. Preserves newlines."""
    content = re.sub(r'/\*[\s\S]*?\*/', '', content)
    content = re.sub(r'//[^\n]*', '', content)
    return content


def _severity_for(key: str, whitelist: list, default: str = Severity.ERROR.value) -> str:
    """Return INFO if key is whitelisted, else default."""
    return Severity.INFO.value if key in whitelist else default


# ── Category 1: Engineering ──────────────────────────────────

# Each entry: (relative_path, mandatory_markers, forbidden_patterns, description)
# mandatory_markers: list of substrings; file must contain ALL of them
# forbidden_patterns: list of regex patterns; file must contain NONE of them
ENGINEERING_CHECKS = [
    {
        'path': '.eslintrc.cjs',
        'markers': ['no-explicit-any', 'no-default-export'],
        'forbidden': [r":\s*'off'(?!\s*//\s*TODO)"],  # 'off' without TODO comment
        'description': 'ESLint config with mandatory rules',
    },
    {
        'path': '.prettierrc',
        'markers': ['printWidth'],
        'forbidden': [],
        'description': 'Prettier config',
    },
    {
        'path': '.husky/pre-commit',
        'markers': [],  # existence + executable bit checked separately
        'forbidden': [],
        'description': 'Husky pre-commit hook',
    },
    {
        'path': '.github/workflows/ci.yml',
        'markers': ['test', 'build'],
        'forbidden': [],
        'description': 'CI workflow',
    },
    {
        'path': '.github/workflows/deploy.yml',
        'markers': [],
        'forbidden': [],
        'description': 'Deploy workflow',
    },
    {
        'path': 'vite.config.ts',
        'markers': ['manualChunks', 'drop_console'],
        'forbidden': [],
        'description': 'Vite config with chunk splitting + console stripping',
    },
    {
        'path': 'vitest.config.ts',
        'markers': ['thresholds'],
        'forbidden': [],
        'description': 'Vitest config with coverage thresholds',
    },
    {
        'path': 'tsconfig.json',
        'markers': ['"strict"'],
        'forbidden': [],
        'description': 'TypeScript config with strict mode',
    },
]


def _check_engineering(project_root: Path, whitelist: list) -> list:
    findings = []
    for spec in ENGINEERING_CHECKS:
        rel = spec['path']
        target = project_root / rel
        wl_key = rel  # whitelist by path

        if not target.exists():
            findings.append(DeliveryFinding(
                category='engineering',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=0,
                finding=f'Missing file: {rel}',
                detail=f'Required: {spec["description"]}',
            ))
            continue

        content = _read_file_safe(target)

        # Check mandatory markers
        for marker in spec['markers']:
            if marker not in content:
                findings.append(DeliveryFinding(
                    category='engineering',
                    severity=_severity_for(wl_key, whitelist),
                    file=rel,
                    line=0,
                    finding=f'{rel} missing mandatory marker: "{marker}"',
                    detail=f'Form compliance trap: file exists but lacks required content',
                ))

        # Check forbidden patterns
        for pat in spec['forbidden']:
            m = re.search(pat, content)
            if m:
                line = _line_of(content, m.start())
                findings.append(DeliveryFinding(
                    category='engineering',
                    severity=_severity_for(wl_key, whitelist),
                    file=rel,
                    line=line,
                    finding=f'{rel} contains forbidden pattern: {m.group(0)!r}',
                    detail=f'Forbidden by: {spec["description"]}',
                ))

        # Check executable bit for husky pre-commit
        if rel == '.husky/pre-commit':
            import os
            if not os.access(target, os.X_OK):
                findings.append(DeliveryFinding(
                    category='engineering',
                    severity=_severity_for(wl_key, whitelist),
                    file=rel,
                    line=0,
                    finding=f'{rel} not executable',
                    detail='Run: chmod +x .husky/pre-commit',
                ))

    # vitest.config.ts: verify thresholds ≥ 85%
    vitest_path = project_root / 'vitest.config.ts'
    if vitest_path.exists():
        content = _read_file_safe(vitest_path)
        # Look for numbers in threshold context
        threshold_matches = re.findall(r'(statements|branches|functions|lines)\s*:\s*(\d+)', content)
        for kind, val in threshold_matches:
            if int(val) < 85:
                wl_key = f'vitest.threshold.{kind}'
                findings.append(DeliveryFinding(
                    category='engineering',
                    severity=_severity_for(wl_key, whitelist),
                    file='vitest.config.ts',
                    line=0,
                    finding=f'coverage threshold {kind}={val}% < 85%',
                    detail='All thresholds must be ≥ 85%',
                ))
        # Check coverage.include is full src/**, not just src/core/**
        if 'coverage' in content and 'include' in content:
            include_match = re.search(r"include\s*:\s*\[([^\]]+)\]", content)
            if include_match:
                include_content = include_match.group(1)
                if 'src/core' in include_content and 'src/**' not in include_content:
                    wl_key = 'vitest.coverage.include'
                    findings.append(DeliveryFinding(
                        category='engineering',
                        severity=_severity_for(wl_key, whitelist),
                        file='vitest.config.ts',
                        line=0,
                        finding='coverage.include limited to src/core/** (should be src/**)',
                        detail='Full src/ coverage required, not just Core layer',
                    ))

    return findings


# ── Category 2: Resource Integrity ───────────────────────────

def _check_resource(project_root: Path, whitelist: list) -> list:
    findings = []

    # 2a: manifest.json referenced icons must exist
    manifest_path = project_root / 'public' / 'manifest.json'
    if not manifest_path.exists():
        manifest_path = project_root / 'manifest.json'
    if manifest_path.exists():
        content = _read_file_safe(manifest_path)
        try:
            manifest = json.loads(content)
        except json.JSONDecodeError:
            manifest = {}
        icons = manifest.get('icons', []) if isinstance(manifest, dict) else []
        for icon in icons:
            src = icon.get('src', '') if isinstance(icon, dict) else ''
            if not src:
                continue
            # Strip leading / for path resolution
            src_clean = src.lstrip('/')
            icon_path = project_root / 'public' / src_clean
            if not icon_path.exists():
                icon_path = project_root / src_clean
            if not icon_path.exists():
                wl_key = f'manifest.icon:{src}'
                findings.append(DeliveryFinding(
                    category='resource',
                    severity=_severity_for(wl_key, whitelist),
                    file=str(manifest_path.relative_to(project_root)).replace('\\', '/'),
                    line=0,
                    finding=f'manifest.json references missing icon: {src}',
                    detail=f'Icon file not found: {src_clean}',
                ))

    # 2b: index.html no inline onclick="..."
    for html_rel in ['index.html', 'public/index.html']:
        html_path = project_root / html_rel
        if not html_path.exists():
            continue
        content = _read_file_safe(html_path)
        # Match onclick="..." or onclick='...'
        onclick_re = re.compile(r'\bonclick\s*=\s*["\']([^"\']+)["\']')
        for m in onclick_re.finditer(content):
            line = _line_of(content, m.start())
            handler = m.group(1)
            wl_key = f'onclick:{handler}'
            findings.append(DeliveryFinding(
                category='resource',
                severity=_severity_for(wl_key, whitelist, default=Severity.WARNING.value),
                file=html_rel,
                line=line,
                finding=f'inline onclick="{handler}" in {html_rel}',
                detail='Use addEventListener in JS instead of inline onclick',
            ))

    return findings


# ── Category 3: Integration (delegates to Phase 3B) ──────────

def _check_integration(project_root: Path, integration_whitelist: dict) -> list:
    if not _HAS_INTEGRATION_CHECK:
        return [DeliveryFinding(
            category='integration',
            severity=Severity.WARNING.value,
            file='',
            line=0,
            finding='modules.integration_check not available — skipping integration checks',
            detail='Phase 3B module not importable',
        )]

    result = run_integration_checks(project_root, whitelist=integration_whitelist)
    findings = []
    for f in result.get('findings', []):
        findings.append(DeliveryFinding(
            category='integration',
            severity=f['severity'],
            file=f['file'],
            line=f['line'],
            finding=f['finding'],
            detail=f.get('detail', ''),
        ))
    return findings


# ── Category 4: i18n Wiring ──────────────────────────────────

def _check_i18n(project_root: Path, whitelist: list) -> list:
    findings = []
    # Find UIManager.ts or any UI manager file
    ui_manager_files = []
    for pattern in ['src/**/UIManager.ts', 'src/**/UiManager.ts', 'src/ui/**/*.ts']:
        ui_manager_files.extend(_find_files(project_root, pattern))
    # Deduplicate
    ui_manager_files = list(dict.fromkeys(ui_manager_files))

    i18n_import_re = re.compile(r"import\s+\{[^}]*\bt\b[^}]*\}\s+from\s+['\"][^'\"]*i18n['\"]")
    setlocale_call_re = re.compile(r'\bsetLocale\s*\(')
    # UI refresh after setLocale: subscribe to locale-change event or call render
    ui_refresh_re = re.compile(r"(subscribe|on)\s*\(\s*['\"]locale['\"]|EventBus\.(on|subscribe)\s*\(\s*['\"]locale|setLocale.*\n.*render", re.IGNORECASE)

    any_ui_manager_found = False
    for ui_file in ui_manager_files:
        content = _read_file_safe(ui_file)
        stripped = _strip_ts_comments(content)
        if not i18n_import_re.search(stripped):
            try:
                rel = _normalize_rel_path(str(ui_file.relative_to(project_root)))
            except ValueError:
                rel = _normalize_rel_path(str(ui_file))
            # Only flag actual UIManager files (not all UI files)
            if ui_file.name in ('UIManager.ts', 'UiManager.ts'):
                any_ui_manager_found = True
                findings.append(DeliveryFinding(
                    category='i18n',
                    severity=_severity_for(rel, whitelist),
                    file=rel,
                    line=0,
                    finding=f'{ui_file.name} does not import {{ t }} from i18n',
                    detail='UIManager must import the t() function and wrap all text in t("key")',
                ))

    # Check setLocale triggers UI refresh (search all src/ files)
    has_setlocale_call = False
    has_ui_refresh = False
    for ts_file in _find_files(project_root, 'src/**/*.ts'):
        content = _read_file_safe(ts_file)
        stripped = _strip_ts_comments(content)
        if setlocale_call_re.search(stripped):
            has_setlocale_call = True
        if ui_refresh_re.search(stripped):
            has_ui_refresh = True

    if has_setlocale_call and not has_ui_refresh:
        wl_key = 'i18n.setLocale_refresh'
        findings.append(DeliveryFinding(
            category='i18n',
            severity=_severity_for(wl_key, whitelist),
            file='',
            line=0,
            finding='setLocale() called but no UI refresh handler found',
            detail='After setLocale, UI should re-render (subscribe to locale-change event)',
        ))

    return findings


# ── Category 5: Test Quality ─────────────────────────────────

# Soft assertion patterns — flagged when used on KNOWN values
SOFT_ASSERT_PATTERNS = [
    # expect(['a','b','c']).toContain(x) — array contains all valid values, always true
    (re.compile(r'expect\s*\(\s*\[[^\]]*\]\s*\)\s*\.\s*toContain\s*\('),
     'toContain on array of known values (soft assert — always true)'),
    # toContain(knownValue) where knownValue is a string literal or number
    (re.compile(r'\.toContain\s*\(\s*["\'][^"\']+["\']'), 'toContain on string literal (soft assert)'),
    (re.compile(r'\.toContain\s*\(\s*\d+\s*\)'), 'toContain on number literal (soft assert)'),
    (re.compile(r'\.toBeTruthy\s*\(\s*\)'), 'toBeTruthy (too lenient)'),
    (re.compile(r'\.toBeDefined\s*\(\s*\)'), 'toBeDefined (too lenient)'),
]


def _check_test_quality(project_root: Path, whitelist: list) -> list:
    findings = []

    # Find test files
    test_files = []
    for pattern in ['**/*.test.ts', '**/*.spec.ts', '**/*.test.tsx', '**/*.spec.tsx']:
        test_files.extend(_find_files(project_root, pattern))
    test_files = list(dict.fromkeys(test_files))

    for test_file in test_files:
        content = _read_file_safe(test_file)
        stripped = _strip_ts_comments(content)
        try:
            rel = _normalize_rel_path(str(test_file.relative_to(project_root)))
        except ValueError:
            rel = _normalize_rel_path(str(test_file))

        for pattern, description in SOFT_ASSERT_PATTERNS:
            for m in pattern.finditer(stripped):
                line = _line_of(stripped, m.start())
                wl_key = f'soft_assert:{rel}:{line}'
                findings.append(DeliveryFinding(
                    category='test_quality',
                    severity=_severity_for(wl_key, whitelist, default=Severity.WARNING.value),
                    file=rel,
                    line=line,
                    finding=f'soft assertion: {description}',
                    detail=m.group(0),
                ))

    # Check vitest.config.ts coverage.include (already checked in engineering)
    # Here we check for boundary test presence — heuristic: test names contain
    # '4.9', '5.0', '5.1', 'boundary', 'edge', 'overlap', 'saturation'
    boundary_keywords = ['4.9', '5.0', '5.1', 'boundary', 'edge', 'overlap', 'saturation', 'limit']
    has_boundary_test = False
    for test_file in test_files:
        content = _read_file_safe(test_file)
        content_lower = content.lower()
        for kw in boundary_keywords:
            if kw in content_lower:
                has_boundary_test = True
                break
        if has_boundary_test:
            break

    if test_files and not has_boundary_test:
        wl_key = 'test_quality.boundary'
        findings.append(DeliveryFinding(
            category='test_quality',
            severity=_severity_for(wl_key, whitelist, default=Severity.WARNING.value),
            file='',
            line=0,
            finding='no boundary test cases found (4.9%/5.0%/5.1% / overlap=0 / saturation)',
            detail='Threshold tests should cover -0.1% / exact / +0.1% triple',
        ))

    return findings


# ── Category 6: Architecture ─────────────────────────────────

def _check_architecture(project_root: Path, whitelist: list) -> list:
    findings = []

    # Patterns to flag
    any_re = re.compile(r'\bany\b(?!\s*\[)')  # 'any' not followed by [
    default_export_re = re.compile(r'\bexport\s+default\s+')
    window_global_re = re.compile(r'\(window\s+as\s+unknown\s+as')
    ts_ignore_re = re.compile(r'@ts-ignore')
    core_imports_three_re = re.compile(r'^\s*import\s+.*\bthree\b', re.MULTILINE)

    # Scan all .ts files
    for ts_file in _find_files(project_root, 'src/**/*.ts'):
        content = _read_file_safe(ts_file)
        stripped = _strip_ts_comments(content)
        try:
            rel = _normalize_rel_path(str(ts_file.relative_to(project_root)))
        except ValueError:
            rel = _normalize_rel_path(str(ts_file))
        rel_parts = rel.split('/')

        # Core layer files must not import three
        is_core = len(rel_parts) >= 2 and rel_parts[1] == 'core'
        if is_core:
            for m in core_imports_three_re.finditer(stripped):
                line = _line_of(stripped, m.start())
                wl_key = f'core_imports_three:{rel}'
                findings.append(DeliveryFinding(
                    category='architecture',
                    severity=_severity_for(wl_key, whitelist),
                    file=rel,
                    line=line,
                    finding='Core layer file imports three (must be environment-agnostic)',
                    detail=m.group(0).strip(),
                ))

        # Any usage (excluding comments already stripped, excluding type annotations like any[])
        # Be conservative: only flag `: any` and `as any` and `<any>`
        explicit_any_re = re.compile(r':\s*any\b|as\s+any\b|<any>')
        for m in explicit_any_re.finditer(stripped):
            line = _line_of(stripped, m.start())
            wl_key = f'any:{rel}:{line}'
            findings.append(DeliveryFinding(
                category='architecture',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=line,
                finding='uses `any` type (use `unknown` + type narrowing instead)',
                detail=m.group(0),
            ))

        # Default export
        for m in default_export_re.finditer(stripped):
            line = _line_of(stripped, m.start())
            wl_key = f'default_export:{rel}'
            findings.append(DeliveryFinding(
                category='architecture',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=line,
                finding='default export (use named exports for tree-shaking)',
                detail=m.group(0).strip(),
            ))

        # (window as unknown as ...) global exposure
        for m in window_global_re.finditer(stripped):
            line = _line_of(stripped, m.start())
            wl_key = f'window_global:{rel}'
            findings.append(DeliveryFinding(
                category='architecture',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=line,
                finding='(window as unknown as ...) global exposure (use EventBus instead)',
                detail=m.group(0),
            ))

        # @ts-ignore
        for m in ts_ignore_re.finditer(content):  # original content — @ts-ignore is in comments
            line = _line_of(content, m.start())
            wl_key = f'ts_ignore:{rel}'
            findings.append(DeliveryFinding(
                category='architecture',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=line,
                finding='@ts-ignore (use @ts-expect-error with issue link if needed)',
                detail=m.group(0),
            ))

    return findings


# ── Main Orchestrator ────────────────────────────────────────

def run_delivery_checklist(
    project_root: Path,
    whitelist: Optional[dict] = None,
    integration_whitelist: Optional[dict] = None,
) -> dict:
    """Run all 6 check categories. Returns structured result dict.

    Args:
        project_root: Target project root (must exist).
        whitelist: Optional delivery whitelist dict (from load_whitelist).
        integration_whitelist: Optional integration whitelist dict
                                (from integration_check.load_whitelist).
                                If None, uses empty whitelist.

    Returns:
        dict with 'findings', 'categories', 'summary', 'project_root'.
    """
    project_root = Path(project_root).resolve()
    if whitelist is None:
        whitelist = _empty_whitelist()
    if integration_whitelist is None:
        integration_whitelist = _empty_integration_whitelist() if _HAS_INTEGRATION_CHECK else {}

    all_findings = []
    # Category 1: Engineering
    all_findings.extend(_check_engineering(project_root, whitelist.get('engineering', [])))
    # Category 2: Resource
    all_findings.extend(_check_resource(project_root, whitelist.get('resource', [])))
    # Category 3: Integration (delegates to Phase 3B)
    all_findings.extend(_check_integration(project_root, integration_whitelist))
    # Category 4: i18n
    all_findings.extend(_check_i18n(project_root, whitelist.get('i18n', [])))
    # Category 5: Test Quality
    all_findings.extend(_check_test_quality(project_root, whitelist.get('test_quality', [])))
    # Category 6: Architecture
    all_findings.extend(_check_architecture(project_root, whitelist.get('architecture', [])))

    # Build per-category summary
    category_names = ['engineering', 'resource', 'integration', 'i18n',
                      'test_quality', 'architecture']
    categories = {}
    for cat in category_names:
        cat_findings = [f for f in all_findings if f.category == cat]
        errors = sum(1 for f in cat_findings if f.severity == Severity.ERROR.value)
        warnings = sum(1 for f in cat_findings if f.severity == Severity.WARNING.value)
        infos = sum(1 for f in cat_findings if f.severity == Severity.INFO.value)
        categories[cat] = {
            'errors': errors,
            'warnings': warnings,
            'infos': infos,
            'total': len(cat_findings),
            'pass': errors == 0,
        }

    total_errors = sum(c['errors'] for c in categories.values())
    total_warnings = sum(c['warnings'] for c in categories.values())
    total_infos = sum(c['infos'] for c in categories.values())

    return {
        'project_root': str(project_root),
        'findings': [f.to_dict() for f in all_findings],
        'categories': categories,
        'summary': {
            'total': len(all_findings),
            'errors': total_errors,
            'warnings': total_warnings,
            'infos': total_infos,
            'pass': total_errors == 0,
        },
    }


# ── CLI ──────────────────────────────────────────────────────

def _cli() -> int:
    parser = argparse.ArgumentParser(
        description='delivery-checklist (Phase 3C Direction 8) — final delivery gate'
    )
    parser.add_argument('--target', type=Path, required=True,
                        help='Target project root')
    parser.add_argument('--whitelist', type=Path, default=None,
                        help='Path to delivery-whitelist.json')
    parser.add_argument('--integration-whitelist', type=Path, default=None,
                        help='Path to integration-whitelist.json (Phase 3B)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    args = parser.parse_args()

    whitelist = load_whitelist(args.whitelist) if args.whitelist else _empty_whitelist()
    if _HAS_INTEGRATION_CHECK:
        int_wl = load_integration_whitelist(args.integration_whitelist) if args.integration_whitelist \
            else _empty_integration_whitelist()
    else:
        int_wl = {}

    result = run_delivery_checklist(args.target, whitelist=whitelist,
                                    integration_whitelist=int_wl)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        from modules.delivery.reporter import format_report_markdown
        print(format_report_markdown(result))

    return 0 if result['summary']['pass'] else 1


if __name__ == '__main__':
    sys.exit(_cli())
