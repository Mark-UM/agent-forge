#!/usr/bin/env python3
"""integration_check.checker — main checker (Phase 3B Direction 3).

Runs 5 rule categories against the target project:
1. init_called — error
2. event_balance — error
3. state_cleanup — error
4. i18n_usage — error
5. cross_layer_call — warning

Stdlib only (re + pathlib). No TypeScript compiler dependency.

Usage:
    python -m modules.integration_check.checker --target /path/to/project
    python -m modules.integration_check.checker --target . --json
    python -m modules.integration_check.checker --target . --whitelist .opencode/integration-whitelist.json
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

_MODULE_DIR = Path(__file__).resolve().parent
_RULES_DIR = _MODULE_DIR / 'rules'


class Severity(str, Enum):
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


@dataclass
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    finding: str
    detail: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


def load_whitelist(path: Path) -> dict:
    """Load integration-whitelist.json. Returns empty dict on missing/invalid."""
    if not path.exists():
        return _empty_whitelist()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        # Normalize: ensure all 5 rule keys exist
        for key in ('init_called', 'event_balance', 'state_cleanup',
                    'i18n_usage', 'cross_layer_call'):
            if key not in data:
                data[key] = []
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_whitelist()


def _empty_whitelist() -> dict:
    return {
        'init_called': [],
        'event_balance': [],
        'state_cleanup': [],
        'i18n_usage': [],
        'cross_layer_call': [],
    }


def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return ''


def _find_files(project_root: Path, glob_pattern: str) -> list:
    """Find files matching glob pattern (recursive)."""
    return sorted(project_root.glob(glob_pattern))


def _line_of(content: str, char_pos: int) -> int:
    """Convert char position to 1-indexed line number."""
    return content.count('\n', 0, char_pos) + 1


def _normalize_rel_path(rel_path: str) -> str:
    """Normalize relative path to forward slashes (POSIX style).

    Whitelists and rule configs use POSIX paths (e.g. 'src/core/Engine.ts').
    On Windows, Path.relative_to() produces backslashes, which would never
    match whitelist entries. Normalize before comparison and before storing
    in Finding.file for consistent cross-platform output.
    """
    return rel_path.replace('\\', '/')


def _strip_ts_comments(content: str) -> str:
    """Remove // line comments and /* */ block comments from TS content.

    Preserves newlines so line numbers stay accurate.
    Does NOT strip string literals (strings rarely contain .init() patterns
    that would cause false positives in our heuristics).

    Args:
        content: TypeScript source content.

    Returns:
        Content with comments replaced by empty strings (newlines preserved).
    """
    # Remove /* ... */ block comments (non-greedy, multiline)
    content = re.sub(r'/\*[\s\S]*?\*/', '', content)
    # Remove // line comments (everything from // to end of line, keep the \n)
    content = re.sub(r'//[^\n]*', '', content)
    return content


# ── Rule 1: init_called ──────────────────────────────────────

def _check_init_called(project_root: Path, whitelist: list) -> list:
    """init() defined but never called in bootstrap files.

    Heuristic (stdlib-only, no TS compiler):
    1. Find init() definitions in manager classes (SoundManager, etc.).
    2. For each, check if bootstrap files (main.ts/index.ts/bootstrap.ts)
       import the class AND have at least one real `.init(` call.
    3. Comments are stripped before matching (avoids false positives from
       `// sound.init() never called` style annotations).
    4. If class is imported but no `.init(` call exists → flag as error.
    5. If class is not imported in any bootstrap file → flag as error.
    """
    findings = []
    rule_file = _RULES_DIR / 'init_called.json'
    rule = json.loads(rule_file.read_text(encoding='utf-8'))
    patterns = rule['detection']['patterns']
    bootstrap_files = rule['detection']['bootstrap_files']
    manager_classes = rule['detection']['manager_classes']

    init_def_re = re.compile(patterns['init_definition'])
    init_call_re = re.compile(patterns['init_call'])
    # Named import: `import { SoundManager } from '...'`
    # Default import: `import SoundManager from '...'`
    # NOTE: use .replace() instead of .format() to avoid brace-escaping hell
    # (regex needs \{ / \} for literal braces, format needs {{ / }} — they
    # conflict). The {CLASS} placeholder is replaced via str.replace.
    import_named_re_template = r"import\s+\{[^}]*\b{CLASS}\b[^}]*\}\s+from"
    import_default_re_template = r"import\s+{CLASS}\s+from"

    # Step 1: find all init() definitions across project
    init_defs = []  # (file, line, class_name)
    for ts_file in _find_files(project_root, 'src/**/*.ts'):
        content = _read_file_safe(ts_file)
        for m in init_def_re.finditer(content):
            line = _line_of(content, m.start())
            # Try to find class name from preceding context (last "class X" before this)
            preceding = content[:m.start()]
            class_match = re.search(r'class\s+(\w+)', preceding)
            class_name = class_match.group(1) if class_match else 'Unknown'
            init_defs.append((ts_file, line, class_name))

    # Step 2: for each manager class init def, check bootstrap wiring
    for ts_file, line, class_name in init_defs:
        if class_name not in manager_classes:
            continue

        # Build import-detection patterns for this class.
        # re.escape handles any regex metachars in the class name.
        escaped = re.escape(class_name)
        named_re = re.compile(
            import_named_re_template.replace('{CLASS}', escaped)
        )
        default_re = re.compile(
            import_default_re_template.replace('{CLASS}', escaped)
        )

        # Check each bootstrap file for: (a) import of class, (b) any .init( call
        called = False
        imported_anywhere = False
        for bs_rel in bootstrap_files:
            bs_path = project_root / bs_rel
            if not bs_path.exists():
                continue
            bs_content_raw = _read_file_safe(bs_path)
            # Strip comments so `// sound.init() never called` doesn't
            # register as a real call.
            bs_content = _strip_ts_comments(bs_content_raw)

            # Is the class imported in this bootstrap file?
            imported = bool(named_re.search(bs_content) or
                            default_re.search(bs_content))
            if not imported:
                continue
            imported_anywhere = True

            # Class is imported. Now check if any .init( call exists
            # (in comment-stripped content).
            if init_call_re.search(bs_content):
                called = True
                break

        if not called:
            finding_key = f'{class_name}.init'
            if finding_key in whitelist:
                severity = Severity.INFO.value
            else:
                severity = Severity.ERROR.value
            try:
                rel_path = _normalize_rel_path(str(ts_file.relative_to(project_root)))
            except ValueError:
                rel_path = _normalize_rel_path(str(ts_file))
            if imported_anywhere:
                detail = (f'{class_name} imported in bootstrap but .init() '
                          f'never called. Files checked: '
                          f'{", ".join(bootstrap_files)}')
            else:
                detail = (f'{class_name} not imported in any bootstrap file. '
                          f'Files checked: {", ".join(bootstrap_files)}')
            findings.append(Finding(
                rule='init_called',
                severity=severity,
                file=rel_path,
                line=line,
                finding=f'{class_name}.init() defined but never called in bootstrap',
                detail=detail,
            ))

    return findings


# ── Rule 2: event_balance ────────────────────────────────────

def _check_event_balance(project_root: Path, whitelist: list) -> list:
    """emit('X') without matching on('X')."""
    findings = []
    rule_file = _RULES_DIR / 'event_balance.json'
    rule = json.loads(rule_file.read_text(encoding='utf-8'))
    patterns = rule['detection']['patterns']
    group = rule['detection']['event_name_group']

    emit_re = re.compile(patterns['emit'])
    subscribe_re = re.compile(patterns['subscribe'])

    emits = []   # (file, line, event_name)
    subscribes = set()  # event names

    for ts_file in _find_files(project_root, 'src/**/*.ts'):
        content = _read_file_safe(ts_file)
        for m in emit_re.finditer(content):
            event = m.group(group) if m.lastindex and group <= m.lastindex else m.group(0)
            line = _line_of(content, m.start())
            emits.append((ts_file, line, event))
        for m in subscribe_re.finditer(content):
            event = m.group(group) if m.lastindex and group <= m.lastindex else m.group(0)
            subscribes.add(event)

    for ts_file, line, event in emits:
        if event in subscribes:
            continue
        if event in whitelist:
            severity = Severity.INFO.value
        else:
            severity = Severity.ERROR.value
        try:
            rel_path = _normalize_rel_path(str(ts_file.relative_to(project_root)))
        except ValueError:
            rel_path = _normalize_rel_path(str(ts_file))
        findings.append(Finding(
            rule='event_balance',
            severity=severity,
            file=rel_path,
            line=line,
            finding=f"emit('{event}') has no matching on('{event}', ...) anywhere",
            detail=f'Subscribed events: {sorted(subscribes) if subscribes else "none"}',
        ))

    return findings


# ── Rule 3: state_cleanup ────────────────────────────────────

def _check_state_cleanup(project_root: Path, whitelist: list) -> list:
    """requestAnimationFrame without cancelAnimationFrame in PAUSED-state code."""
    findings = []
    rule_file = _RULES_DIR / 'state_cleanup.json'
    rule = json.loads(rule_file.read_text(encoding='utf-8'))
    patterns = rule['detection']['patterns']

    raf_re = re.compile(patterns['raf_call'])
    cancel_re = re.compile(patterns['raf_cancel'])
    paused_re = re.compile(patterns['paused_state'])

    for ts_file in _find_files(project_root, 'src/**/*.ts'):
        content = _read_file_safe(ts_file)
        has_raf = bool(raf_re.search(content))
        has_paused = bool(paused_re.search(content))
        has_cancel = bool(cancel_re.search(content))

        if has_raf and has_paused and not has_cancel:
            # Whitelist check: file path (normalized to forward slashes)
            try:
                rel_path = _normalize_rel_path(str(ts_file.relative_to(project_root)))
            except ValueError:
                rel_path = _normalize_rel_path(str(ts_file))
            if rel_path in whitelist:
                severity = Severity.INFO.value
            else:
                severity = Severity.ERROR.value
            # Find first rAF line
            m = raf_re.search(content)
            line = _line_of(content, m.start()) if m else 1
            findings.append(Finding(
                rule='state_cleanup',
                severity=severity,
                file=rel_path,
                line=line,
                finding='requestAnimationFrame used with PAUSED state but no cancelAnimationFrame',
                detail='PAUSED branch should call cancelAnimationFrame to stop the loop',
            ))

    return findings


# ── Rule 4: i18n_usage ───────────────────────────────────────

def _check_i18n_usage(project_root: Path, whitelist: list) -> list:
    """UI files with hardcoded text not wrapped in t()."""
    findings = []
    rule_file = _RULES_DIR / 'i18n_usage.json'
    rule = json.loads(rule_file.read_text(encoding='utf-8'))
    patterns = rule['detection']['patterns']
    file_scope = rule['detection']['file_scope']

    i18n_import_re = re.compile(patterns['i18n_import'])
    i18n_call_re = re.compile(patterns['i18n_call'])
    hardcoded_re = re.compile(patterns['hardcoded_text'])

    ui_files = _find_files(project_root, file_scope)
    for ts_file in ui_files:
        content = _read_file_safe(ts_file)
        try:
            rel_path = _normalize_rel_path(str(ts_file.relative_to(project_root)))
        except ValueError:
            rel_path = _normalize_rel_path(str(ts_file))

        # Skip if file imports i18n AND all hardcoded text is wrapped — too loose.
        # Real check: any hardcoded text pattern → flag.
        has_i18n_import = bool(i18n_import_re.search(content))
        hardcoded_matches = list(hardcoded_re.finditer(content))
        if not hardcoded_matches:
            continue

        # If file has no i18n import at all, that's the primary finding
        if not has_i18n_import:
            if rel_path in whitelist:
                severity = Severity.INFO.value
            else:
                severity = Severity.ERROR.value
            first = hardcoded_matches[0]
            line = _line_of(content, first.start())
            findings.append(Finding(
                rule='i18n_usage',
                severity=severity,
                file=rel_path,
                line=line,
                finding='UI file has hardcoded text but no i18n import',
                detail='Add: import { t } from "@/i18n" and wrap text in t("key")',
            ))
            continue

        # File has i18n import — flag individual hardcoded text
        for m in hardcoded_matches:
            # Extract the text (one of the groups will be non-None)
            text = next((g for g in m.groups() if g), '')
            if not text:
                continue
            # Skip if text is in whitelist
            if text in whitelist or rel_path in whitelist:
                severity = Severity.INFO.value
            else:
                severity = Severity.ERROR.value
            line = _line_of(content, m.start())
            findings.append(Finding(
                rule='i18n_usage',
                severity=severity,
                file=rel_path,
                line=line,
                finding=f'Hardcoded UI text: "{text[:50]}"',
                detail='Wrap in t("key") and add the key to locales',
            ))

    return findings


# ── Rule 5: cross_layer_call ─────────────────────────────────

def _check_cross_layer_call(project_root: Path, whitelist: list) -> list:
    """main.ts directly calling Render layer methods (bypass EventBus).

    Config is loaded from rules/cross_layer_call.json (externalized in
    Phase 3E audit fix — previously hardcoded regex).
    """
    findings = []
    rule_file = _RULES_DIR / 'cross_layer_call.json'
    rule = json.loads(rule_file.read_text(encoding='utf-8'))
    detection = rule['detection']
    main_files = detection['main_files']
    render_call_re = re.compile(detection['patterns']['render_call'])

    for rel in main_files:
        main_path = project_root / rel
        if not main_path.exists():
            continue
        content = _read_file_safe(main_path)
        for m in render_call_re.finditer(content):
            if rel in whitelist:
                severity = Severity.INFO.value
            else:
                severity = Severity.WARNING.value
            line = _line_of(content, m.start())
            findings.append(Finding(
                rule='cross_layer_call',
                severity=severity,
                file=rel,
                line=line,
                finding=f'main.ts directly calls render method: {m.group(0)}',
                detail='Use EventBus.dispatch() instead of direct render layer access',
            ))

    return findings


# ── Main entry ───────────────────────────────────────────────

def run_checks(project_root: Path, whitelist: Optional[dict] = None) -> dict:
    """Run all 5 rules. Returns structured result dict.

    Args:
        project_root: Target project root (must exist).
        whitelist: Optional whitelist dict (from load_whitelist). If None,
                   all findings are errors/warnings (no info downgrade).

    Returns:
        dict with 'findings' (list of Finding dicts), 'summary', 'project_root'.
    """
    project_root = Path(project_root).resolve()
    if whitelist is None:
        whitelist = _empty_whitelist()

    all_findings = []
    all_findings.extend(_check_init_called(project_root, whitelist.get('init_called', [])))
    all_findings.extend(_check_event_balance(project_root, whitelist.get('event_balance', [])))
    all_findings.extend(_check_state_cleanup(project_root, whitelist.get('state_cleanup', [])))
    all_findings.extend(_check_i18n_usage(project_root, whitelist.get('i18n_usage', [])))
    all_findings.extend(_check_cross_layer_call(project_root, whitelist.get('cross_layer_call', [])))

    errors = sum(1 for f in all_findings if f.severity == Severity.ERROR.value)
    warnings = sum(1 for f in all_findings if f.severity == Severity.WARNING.value)
    infos = sum(1 for f in all_findings if f.severity == Severity.INFO.value)

    return {
        'project_root': str(project_root),
        'findings': [f.to_dict() for f in all_findings],
        'summary': {
            'total': len(all_findings),
            'errors': errors,
            'warnings': warnings,
            'infos': infos,
            'pass': errors == 0,
        },
    }


def format_report_markdown(result: dict) -> str:
    """Format result as Markdown table."""
    lines = []
    lines.append('## Integration Check Report')
    lines.append('')
    lines.append(f'**Project**: `{result["project_root"]}`')
    lines.append('')
    if not result['findings']:
        lines.append('✅ No integration issues found.')
        lines.append('')
        s = result['summary']
        lines.append(f'## Summary: PASS (0 errors, {s["warnings"]} warnings, {s["infos"]} info)')
        return '\n'.join(lines)

    lines.append('| # | Rule | Severity | File:Line | Finding |')
    lines.append('|---|------|----------|-----------|---------|')
    for i, f in enumerate(result['findings'], 1):
        sev_upper = f['severity'].upper()
        location = f"{f['file']}:{f['line']}"
        # Escape pipes in finding text
        finding = f['finding'].replace('|', '\\|')
        lines.append(
            f"| {i} | {f['rule']} | {sev_upper} | {location} | {finding} |"
        )

    lines.append('')
    s = result['summary']
    status = 'PASS' if s['pass'] else 'FAIL'
    lines.append(
        f'## Summary: {status} ({s["errors"]} errors, {s["warnings"]} warnings, {s["infos"]} info)'
    )
    return '\n'.join(lines)


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description='integration-link-checker (Phase 3B Direction 3)'
    )
    parser.add_argument('--target', type=Path, required=True,
                        help='Target project root')
    parser.add_argument('--whitelist', type=Path, default=None,
                        help='Path to integration-whitelist.json')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    args = parser.parse_args()

    whitelist = load_whitelist(args.whitelist) if args.whitelist else _empty_whitelist()
    result = run_checks(args.target, whitelist=whitelist)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(format_report_markdown(result))

    return 0 if result['summary']['pass'] else 1


if __name__ == '__main__':
    sys.exit(_cli())
