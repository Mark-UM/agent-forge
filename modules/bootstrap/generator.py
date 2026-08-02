#!/usr/bin/env python3
"""project-bootstrap generator (Phase 3A, Direction 1).

Writes 9 mandatory scaffold files from templates into the target project.

Design:
- Python stdlib only (no Jinja2 dependency)
- Template files live in `modules/bootstrap/templates/`
- Each template is the literal final file content; only the destination path differs
- Atomic write: tmp + os.replace (same pattern as Phase 0 B2 fix)
- validate_scaffold() inspects generated files for the mandatory markers
  (catches "form compliance trap" where file exists but content is wrong)

Usage:
    python -m modules.bootstrap.generator --target /path/to/project
    python -m modules.bootstrap.generator --target . --force
    python -m modules.bootstrap.generator --list
    python -m modules.bootstrap.generator --validate --target .
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── Paths ────────────────────────────────────────────────────
_MODULE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _MODULE_DIR / 'templates'

# Template name → (destination relative path, mandatory markers for validation)
# Mandatory markers are substrings that MUST exist in the generated file
# (catches "form compliance trap" where file exists but content is wrong).
TEMPLATES = {
    '.eslintrc.cjs.template': (
        '.eslintrc.cjs',
        [
            "'@typescript-eslint/no-explicit-any': 'error'",
            "'import/no-default-export': 'error'",
            'no-restricted-imports',
            'no-restricted-globals',
        ],
    ),
    '.prettierrc.template': (
        '.prettierrc',
        ['"printWidth": 100', '"singleQuote": true', '"trailingComma": "all"'],
    ),
    '.prettierignore.template': (
        '.prettierignore',
        ['dist/', 'node_modules/', 'coverage/'],
    ),
    'pre-commit.template': (
        '.husky/pre-commit',
        ['lint-staged', 'tsc --noEmit', 'vitest run'],
    ),
    'ci.yml.template': (
        '.github/workflows/ci.yml',
        ['npx tsc --noEmit', 'npx eslint .', 'npx vitest run --coverage'],
    ),
    'deploy.yml.template': (
        '.github/workflows/deploy.yml',
        ['vercel deploy', 'netlify deploy', 'VERCEL_TOKEN', 'NETLIFY_AUTH_TOKEN'],
    ),
    'tsconfig.json.template': (
        'tsconfig.json',
        [
            '"strict": true',
            '"noImplicitAny": true',
            '"strictNullChecks": true',
            '"strictFunctionTypes": true',
            '"strictBindCallApply": true',
            '"strictPropertyInitialization": true',
            '"noImplicitThis": true',
            '"alwaysStrict": true',
            '"useUnknownInCatchVariables": true',
        ],
    ),
    'vite.config.ts.template': (
        'vite.config.ts',
        [
            "minify: 'terser'",
            'drop_console: true',
            'drop_debugger: true',
            'three: [',
        ],
    ),
    'vitest.config.ts.template': (
        'vitest.config.ts',
        [
            'statements: 85',
            'branches: 85',
            'functions: 85',
            'lines: 85',
            "include: ['src/**/*.ts']",
        ],
    ),
}


def list_templates() -> list:
    """Return list of (template_name, destination_path) tuples."""
    return [(name, dest) for name, (dest, _) in TEMPLATES.items()]


def _atomic_write(target: Path, content: str) -> None:
    """Atomic write: tmp file in same dir + os.replace.

    Same pattern as Phase 0 B2 fix in modules/search/search.py.
    Prevents partial-write corruption if process is killed mid-write.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w',
        encoding='utf-8',
        dir=str(target.parent),
        prefix=f'.{target.name}.tmp-',
        delete=False,
    ) as f:
        f.write(content)
        tmp_path = f.name
    try:
        os.replace(tmp_path, target)
    except Exception:
        # Cleanup tmp on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def generate_scaffold(project_root: Path, force: bool = False) -> dict:
    """Generate all 8 scaffold files in target project.

    Args:
        project_root: Target project root (must exist).
        force: If True, overwrite existing files. If False, skip existing.

    Returns:
        dict: {
            'success': bool,
            'project_root': str,
            'written': list[str],        # relative paths written
            'skipped': list[str],        # relative paths skipped (already existed)
            'missing_templates': list[str],  # templates that don't exist on disk
            'errors': list[str],         # per-file errors
        }
    """
    project_root = Path(project_root).resolve()
    if not project_root.is_dir():
        return {
            'success': False,
            'project_root': str(project_root),
            'written': [],
            'skipped': [],
            'missing_templates': [],
            'errors': [f'project_root does not exist: {project_root}'],
        }

    written = []
    skipped = []
    missing_templates = []
    errors = []

    for template_name, (rel_dest, _markers) in TEMPLATES.items():
        template_path = _TEMPLATES_DIR / template_name
        if not template_path.exists():
            missing_templates.append(template_name)
            continue

        dest_path = project_root / rel_dest
        if dest_path.exists() and not force:
            skipped.append(rel_dest)
            continue

        try:
            content = template_path.read_text(encoding='utf-8')
            _atomic_write(dest_path, content)
            written.append(rel_dest)

            # Set executable bit for pre-commit hook
            if rel_dest == '.husky/pre-commit':
                try:
                    os.chmod(dest_path, 0o755)
                except OSError:
                    pass
        except Exception as e:
            errors.append(f'{rel_dest}: {type(e).__name__}: {e}')

    return {
        'success': len(errors) == 0 and len(missing_templates) == 0,
        'project_root': str(project_root),
        'written': sorted(written),
        'skipped': sorted(skipped),
        'missing_templates': sorted(missing_templates),
        'errors': errors,
    }


def validate_scaffold(project_root: Path) -> dict:
    """Validate that all 8 scaffold files exist AND contain mandatory markers.

    This catches the "form compliance trap": file exists but content is wrong
    (e.g., tsconfig.json exists but `strict: true` was disabled).

    Args:
        project_root: Target project root.

    Returns:
        dict: {
            'success': bool,           # all files exist + all markers present
            'project_root': str,
            'checks': list[dict],      # one per file
            'missing_files': list[str],
            'marker_failures': list[dict],
        }
    """
    project_root = Path(project_root).resolve()
    checks = []
    missing_files = []
    marker_failures = []

    for template_name, (rel_dest, markers) in TEMPLATES.items():
        dest_path = project_root / rel_dest
        check = {
            'file': rel_dest,
            'exists': dest_path.exists(),
            'markers_expected': len(markers),
            'markers_found': 0,
            'missing_markers': [],
        }
        if not dest_path.exists():
            missing_files.append(rel_dest)
            checks.append(check)
            continue

        try:
            content = dest_path.read_text(encoding='utf-8')
        except Exception as e:
            check['error'] = f'read failed: {e}'
            checks.append(check)
            missing_files.append(rel_dest)
            continue

        for marker in markers:
            if marker in content:
                check['markers_found'] += 1
            else:
                check['missing_markers'].append(marker)

        if check['missing_markers']:
            marker_failures.append({
                'file': rel_dest,
                'missing_markers': check['missing_markers'],
            })
        checks.append(check)

    success = (not missing_files) and (not marker_failures)
    return {
        'success': success,
        'project_root': str(project_root),
        'checks': checks,
        'missing_files': sorted(missing_files),
        'marker_failures': marker_failures,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description='project-bootstrap generator (Phase 3A Direction 1)'
    )
    parser.add_argument('--target', type=Path, help='Target project root')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite existing files')
    parser.add_argument('--list', action='store_true',
                        help='List templates and exit')
    parser.add_argument('--validate', action='store_true',
                        help='Validate existing scaffold (do not write)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    args = parser.parse_args()

    if args.list:
        for name, dest in list_templates():
            print(f'{name:35s} -> {dest}')
        return 0

    if not args.target:
        parser.error('--target is required (unless using --list)')

    if args.validate:
        result = validate_scaffold(args.target)
    else:
        result = generate_scaffold(args.target, force=args.force)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if args.validate:
            status = 'PASS' if result['success'] else 'FAIL'
            print(f'[{status}] scaffold validation: {result["project_root"]}')
            for check in result['checks']:
                exists = '✓' if check['exists'] else '✗'
                markers = f"{check['markers_found']}/{check['markers_expected']}"
                print(f'  {exists} {check["file"]:35s} markers: {markers}')
                for missing in check.get('missing_markers', []):
                    print(f'      missing: {missing}')
        else:
            print(f'written:   {len(result["written"])} files')
            for f in result['written']:
                print(f'  ✓ {f}')
            if result['skipped']:
                print(f'skipped:   {len(result["skipped"])} files (use --force to overwrite)')
                for f in result['skipped']:
                    print(f'  - {f}')
            if result['missing_templates']:
                print(f'missing templates: {result["missing_templates"]}')
            if result['errors']:
                print(f'errors:    {len(result["errors"])}')
                for e in result['errors']:
                    print(f'  ✗ {e}')

    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(_cli())
