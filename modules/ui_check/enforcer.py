#!/usr/bin/env python3
"""modules.ui_check.enforcer — UI component architecture enforcer (Phase 3D Direction 7).

Runs 5 check categories against a target project's UI layer:
1. directories   — src/ui/panels/ + src/ui/components/ must exist
2. panel_classes — each Panel must be a class extending BasePanel
3. inline_onclick — no onclick="..." in index.html
4. window_globals — no (window as unknown as ...) in UI files
5. uimanager_i18n — UIManager imports { t, setLocale } from i18n

Stdlib only.

Usage:
    python -m modules.ui_check.enforcer --target /path/to/project
    python -m modules.ui_check.enforcer --target . --json
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


class Severity(str, Enum):
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


@dataclass
class UIFinding:
    rule: str
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
        'directories': [],
        'panel_classes': [],
        'inline_onclick': [],
        'window_globals': [],
        'uimanager_i18n': [],
    }


def load_whitelist(path: Path) -> dict:
    """Load ui-whitelist.json. Returns empty dict on missing/invalid."""
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
    return rel_path.replace('\\', '/')


def _strip_ts_comments(content: str) -> str:
    content = re.sub(r'/\*[\s\S]*?\*/', '', content)
    content = re.sub(r'//[^\n]*', '', content)
    return content


def _severity_for(key: str, whitelist: list, default: str = Severity.ERROR.value) -> str:
    return Severity.INFO.value if key in whitelist else default


# ── Rule 1: Directories ──────────────────────────────────────

REQUIRED_UI_DIRS = [
    'src/ui/panels',
    'src/ui/components',
]


def _check_directories(project_root: Path, whitelist: list) -> list:
    findings = []
    for rel in REQUIRED_UI_DIRS:
        dir_path = project_root / rel
        wl_key = f'dir:{rel}'
        if not dir_path.exists():
            findings.append(UIFinding(
                rule='directories',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=0,
                finding=f'Missing required UI directory: {rel}/',
                detail='Component-based architecture requires panels/ and components/ directories',
            ))
        elif not dir_path.is_dir():
            findings.append(UIFinding(
                rule='directories',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=0,
                finding=f'{rel} exists but is not a directory',
                detail='Expected a directory for Panel/Component class files',
            ))
    return findings


# ── Rule 2: Panel Classes ────────────────────────────────────

# Pattern: export class XxxPanel extends BasePanel
PANEL_CLASS_RE = re.compile(
    r'export\s+class\s+(\w+Panel)\s+extends\s+(BasePanel|Panel)\b'
)
# Pattern: any class with "Panel" in name
ANY_PANEL_CLASS_RE = re.compile(
    r'export\s+class\s+(\w*Panel\w*)\b'
)
# Pattern: getElementById in UI files (forbidden in Panel classes)
GET_ELEMENT_BY_ID_RE = re.compile(r'document\.getElementById\s*\(')


def _check_panel_classes(project_root: Path, whitelist: list) -> list:
    findings = []

    # Files that ARE the base class — skip them (they define the contract,
    # they don't consume it).
    BASE_CLASS_FILENAMES = {'BasePanel.ts', 'Panel.ts', 'BaseComponent.ts'}

    # Scan src/ui/panels/**/*.ts
    panel_files = _find_files(project_root, 'src/ui/panels/**/*.ts')
    for ts_file in panel_files:
        if ts_file.name in BASE_CLASS_FILENAMES:
            continue  # Base class definitions are exempt
        content = _read_file_safe(ts_file)
        stripped = _strip_ts_comments(content)
        try:
            rel = _normalize_rel_path(str(ts_file.relative_to(project_root)))
        except ValueError:
            rel = _normalize_rel_path(str(ts_file))

        # Check: file should contain `export class XxxPanel extends BasePanel`
        if not PANEL_CLASS_RE.search(stripped):
            # Maybe it has a panel-like class but doesn't extend BasePanel
            any_panel = ANY_PANEL_CLASS_RE.search(stripped)
            if any_panel:
                class_name = any_panel.group(1)
                wl_key = f'panel_class:{rel}'
                findings.append(UIFinding(
                    rule='panel_classes',
                    severity=_severity_for(wl_key, whitelist),
                    file=rel,
                    line=0,
                    finding=f'{class_name} does not extend BasePanel',
                    detail='Each Panel must extend BasePanel for consistent lifecycle',
                ))
            else:
                wl_key = f'panel_class:{rel}'
                findings.append(UIFinding(
                    rule='panel_classes',
                    severity=_severity_for(wl_key, whitelist),
                    file=rel,
                    line=0,
                    finding=f'{rel} in panels/ directory but contains no Panel class',
                    detail='Each .ts file in panels/ must export a class extending BasePanel',
                ))

        # Check: no document.getElementById in Panel classes (use constructor injection)
        for m in GET_ELEMENT_BY_ID_RE.finditer(stripped):
            line = _line_of(stripped, m.start())
            wl_key = f'panel_get_element:{rel}:{line}'
            findings.append(UIFinding(
                rule='panel_classes',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=line,
                finding='Panel class uses document.getElementById (use constructor injection)',
                detail='HTMLElement reference should be passed via constructor, not queried',
            ))

    # Also scan src/ui/components/**/*.ts (less strict — components may not extend BasePanel)
    component_files = _find_files(project_root, 'src/ui/components/**/*.ts')
    for ts_file in component_files:
        if ts_file.name in BASE_CLASS_FILENAMES:
            continue
        content = _read_file_safe(ts_file)
        stripped = _strip_ts_comments(content)
        try:
            rel = _normalize_rel_path(str(ts_file.relative_to(project_root)))
        except ValueError:
            rel = _normalize_rel_path(str(ts_file))

        # Components must at least export a class
        if not re.search(r'export\s+class\s+\w+', stripped):
            wl_key = f'component_class:{rel}'
            findings.append(UIFinding(
                rule='panel_classes',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=0,
                finding=f'{rel} in components/ directory but exports no class',
                detail='Each .ts file in components/ must export a class',
            ))

    return findings


# ── Rule 3: Inline onclick ───────────────────────────────────

INLINE_ONCLICK_RE = re.compile(r'\bonclick\s*=\s*["\']([^"\']+)["\']')


def _check_inline_onclick(project_root: Path, whitelist: list) -> list:
    findings = []
    for html_rel in ['index.html', 'public/index.html']:
        html_path = project_root / html_rel
        if not html_path.exists():
            continue
        content = _read_file_safe(html_path)
        for m in INLINE_ONCLICK_RE.finditer(content):
            line = _line_of(content, m.start())
            handler = m.group(1)
            wl_key = f'onclick:{handler}'
            findings.append(UIFinding(
                rule='inline_onclick',
                severity=_severity_for(wl_key, whitelist, default=Severity.WARNING.value),
                file=html_rel,
                line=line,
                finding=f'inline onclick="{handler}" in {html_rel}',
                detail='Use addEventListener in a Panel/Component class instead',
            ))
    return findings


# ── Rule 4: Window globals ───────────────────────────────────

WINDOW_GLOBAL_RE = re.compile(r'\(window\s+as\s+unknown\s+as\s+Record<[^>]*>\)')


def _check_window_globals(project_root: Path, whitelist: list) -> list:
    findings = []
    # Scan all UI .ts files
    ui_files = []
    for pattern in ['src/ui/**/*.ts', 'src/components/**/*.ts', 'src/panels/**/*.ts']:
        ui_files.extend(_find_files(project_root, pattern))
    ui_files = list(dict.fromkeys(ui_files))

    for ts_file in ui_files:
        content = _read_file_safe(ts_file)
        stripped = _strip_ts_comments(content)
        try:
            rel = _normalize_rel_path(str(ts_file.relative_to(project_root)))
        except ValueError:
            rel = _normalize_rel_path(str(ts_file))
        for m in WINDOW_GLOBAL_RE.finditer(stripped):
            line = _line_of(stripped, m.start())
            wl_key = f'window_global:{rel}:{line}'
            findings.append(UIFinding(
                rule='window_globals',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=line,
                finding='(window as unknown as ...) global exposure (use EventBus)',
                detail=m.group(0),
            ))
    return findings


# ── Rule 5: UIManager i18n ───────────────────────────────────

I18N_IMPORT_RE = re.compile(
    r"import\s+\{[^}]*\bt\b[^}]*\}\s+from\s+['\"][^'\"]*i18n['\"]"
)
I18N_SETLOCALE_IMPORT_RE = re.compile(
    r"import\s+\{[^}]*\bsetLocale\b[^}]*\}\s+from\s+['\"][^'\"]*i18n['\"]"
)


def _check_uimanager_i18n(project_root: Path, whitelist: list) -> list:
    findings = []
    ui_manager_files = []
    for pattern in ['src/**/UIManager.ts', 'src/**/UiManager.ts']:
        ui_manager_files.extend(_find_files(project_root, pattern))

    for ui_file in ui_manager_files:
        content = _read_file_safe(ui_file)
        stripped = _strip_ts_comments(content)
        try:
            rel = _normalize_rel_path(str(ui_file.relative_to(project_root)))
        except ValueError:
            rel = _normalize_rel_path(str(ui_file))

        if not I18N_IMPORT_RE.search(stripped):
            wl_key = f'uimanager_i18n:{rel}'
            findings.append(UIFinding(
                rule='uimanager_i18n',
                severity=_severity_for(wl_key, whitelist),
                file=rel,
                line=0,
                finding=f'{ui_file.name} does not import {{ t }} from i18n',
                detail='UIManager must import the t() function to wrap all user-visible text',
            ))

        if not I18N_SETLOCALE_IMPORT_RE.search(stripped):
            wl_key = f'uimanager_setlocale:{rel}'
            findings.append(UIFinding(
                rule='uimanager_i18n',
                severity=_severity_for(wl_key, whitelist,
                                       default=Severity.WARNING.value),
                file=rel,
                line=0,
                finding=f'{ui_file.name} does not import {{ setLocale }} from i18n',
                detail='UIManager should expose setLocale for runtime language switching',
            ))

    return findings


# ── Main Orchestrator ────────────────────────────────────────

def run_ui_checks(project_root: Path, whitelist: Optional[dict] = None) -> dict:
    """Run all 5 UI check rules. Returns structured result dict.

    Args:
        project_root: Target project root (must exist).
        whitelist: Optional whitelist dict (from load_whitelist).

    Returns:
        dict with 'findings', 'rules', 'summary', 'project_root'.
    """
    project_root = Path(project_root).resolve()
    if whitelist is None:
        whitelist = _empty_whitelist()

    all_findings = []
    all_findings.extend(_check_directories(project_root, whitelist.get('directories', [])))
    all_findings.extend(_check_panel_classes(project_root, whitelist.get('panel_classes', [])))
    all_findings.extend(_check_inline_onclick(project_root, whitelist.get('inline_onclick', [])))
    all_findings.extend(_check_window_globals(project_root, whitelist.get('window_globals', [])))
    all_findings.extend(_check_uimanager_i18n(project_root, whitelist.get('uimanager_i18n', [])))

    # Build per-rule summary
    rule_names = ['directories', 'panel_classes', 'inline_onclick',
                  'window_globals', 'uimanager_i18n']
    rules = {}
    for rule in rule_names:
        rule_findings = [f for f in all_findings if f.rule == rule]
        errors = sum(1 for f in rule_findings if f.severity == Severity.ERROR.value)
        warnings = sum(1 for f in rule_findings if f.severity == Severity.WARNING.value)
        infos = sum(1 for f in rule_findings if f.severity == Severity.INFO.value)
        rules[rule] = {
            'errors': errors,
            'warnings': warnings,
            'infos': infos,
            'total': len(rule_findings),
            'pass': errors == 0,
        }

    total_errors = sum(r['errors'] for r in rules.values())
    total_warnings = sum(r['warnings'] for r in rules.values())
    total_infos = sum(r['infos'] for r in rules.values())

    return {
        'project_root': str(project_root),
        'findings': [f.to_dict() for f in all_findings],
        'rules': rules,
        'summary': {
            'total': len(all_findings),
            'errors': total_errors,
            'warnings': total_warnings,
            'infos': total_infos,
            'pass': total_errors == 0,
        },
    }


def format_report_markdown(result: dict) -> str:
    lines = []
    lines.append('## UI Component Enforcer Report')
    lines.append('')
    lines.append(f'**Project**: `{result.get("project_root", "")}`')
    lines.append('')

    # Per-rule table
    lines.append('| Rule | Errors | Warnings | Info | Status |')
    lines.append('|------|--------|----------|------|--------|')
    for rule, stats in result.get('rules', {}).items():
        status = 'PASS' if stats['pass'] else 'FAIL'
        lines.append(
            f"| {rule} | {stats['errors']} | {stats['warnings']} | "
            f"{stats['infos']} | {status} |"
        )
    lines.append('')

    findings = result.get('findings', [])
    if not findings:
        lines.append('✅ No UI architecture issues found.')
    else:
        lines.append('### Findings')
        lines.append('')
        lines.append('| # | Rule | Severity | File:Line | Finding |')
        lines.append('|---|------|----------|-----------|---------|')
        for i, f in enumerate(findings, 1):
            sev = f.get('severity', 'error').upper()
            location = f"{f.get('file', '')}:{f.get('line', 0)}"
            finding_text = f.get('finding', '').replace('|', '\\|')
            lines.append(
                f"| {i} | {f.get('rule', '')} | {sev} | "
                f"{location} | {finding_text} |"
            )
    lines.append('')

    s = result.get('summary', {})
    overall = 'PASS' if s.get('pass') else 'FAIL'
    lines.append(
        f'## Overall: {overall} '
        f'({s.get("errors", 0)} errors, {s.get("warnings", 0)} warnings, '
        f'{s.get("infos", 0)} info)'
    )
    return '\n'.join(lines)


# ── CLI ──────────────────────────────────────────────────────

def _cli() -> int:
    parser = argparse.ArgumentParser(
        description='ui-component-enforcer (Phase 3D Direction 7)'
    )
    parser.add_argument('--target', type=Path, required=True,
                        help='Target project root')
    parser.add_argument('--whitelist', type=Path, default=None,
                        help='Path to ui-whitelist.json')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    args = parser.parse_args()

    whitelist = load_whitelist(args.whitelist) if args.whitelist else _empty_whitelist()
    result = run_ui_checks(args.target, whitelist=whitelist)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_report_markdown(result))

    return 0 if result['summary']['pass'] else 1


if __name__ == '__main__':
    sys.exit(_cli())
