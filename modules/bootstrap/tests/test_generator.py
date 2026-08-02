"""Phase 3A — project-bootstrap generator tests.

Validates:
- list_templates() returns all 9 expected (template_name, dest) pairs
- generate_scaffold() writes all 9 files into a temp project
- Atomic write pattern (tmp + os.replace) — no partial files on failure
- Skipping existing files when force=False
- Overwriting when force=True
- validate_scaffold() catches missing files (form-compliance trap)
- validate_scaffold() catches missing mandatory markers (form-compliance trap)
- pre-commit hook gets executable bit
- Template files on disk match TEMPLATES dict
- Round-trip: generate → validate → success
- Errors propagate when project_root doesn't exist
- CLI smoke test (--list, --validate)
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.bootstrap import generator
from modules.bootstrap.generator import (
    TEMPLATES,
    _atomic_write,
    _TEMPLATES_DIR,
    generate_scaffold,
    list_templates,
    validate_scaffold,
)


# ── Constants ────────────────────────────────────────────────

class TestTemplateRegistry:
    """Template registry must list exactly the 9 mandatory files."""

    def test_list_templates_count(self):
        templates = list_templates()
        # 9 templates (eslintrc, prettierrc, prettierignore, pre-commit,
        # ci.yml, deploy.yml, tsconfig.json, vite.config.ts, vitest.config.ts)
        assert len(templates) == 9

    def test_list_templates_format(self):
        for name, dest in list_templates():
            assert isinstance(name, str)
            assert isinstance(dest, str)
            assert name.endswith('.template')
            assert dest  # non-empty

    def test_templates_dict_keys_match_files_on_disk(self):
        """Every template name in TEMPLATES must have a file on disk."""
        for name in TEMPLATES:
            template_path = _TEMPLATES_DIR / name
            assert template_path.exists(), f'Template missing on disk: {name}'

    def test_templates_have_mandatory_markers(self):
        """Each template must have non-empty mandatory markers list."""
        for name, (dest, markers) in TEMPLATES.items():
            assert markers, f'{name} has empty markers list'

    def test_eslintrc_template_has_no_off_rules_outside_overrides(self):
        """eslintrc.cjs.template must not have rules:'off' outside overrides
        (overrides allow off for script files)."""
        template = (_TEMPLATES_DIR / '.eslintrc.cjs.template').read_text(encoding='utf-8')
        # The main rules block must not contain 'off' as the rule value
        # (overrides are allowed)
        # Simple check: count of ": 'off'" outside overrides must be 0
        # Split on overrides to isolate main rules
        if 'overrides:' in template:
            main_block = template.split('overrides:')[0]
        else:
            main_block = template
        # import/prefer-default-export: 'off' is intentional (paired with no-default-export: error)
        # so we only check for the dangerous patterns
        assert "'@typescript-eslint/no-explicit-any': 'off'" not in main_block
        assert "'import/no-default-export': 'off'" not in main_block


# ── generate_scaffold ────────────────────────────────────────

class TestGenerateScaffold:
    """generate_scaffold should write all files to target project."""

    def test_generate_all_files(self, tmp_path):
        """Generate scaffold into a fresh temp project."""
        result = generate_scaffold(tmp_path)
        assert result['success'] is True
        assert len(result['written']) == 9
        assert result['skipped'] == []
        assert result['errors'] == []
        for rel_dest in result['written']:
            assert (tmp_path / rel_dest).exists()

    def test_skip_existing_when_not_forced(self, tmp_path):
        """When force=False, existing files should be skipped."""
        # First write
        generate_scaffold(tmp_path)
        # Second write should skip all
        result = generate_scaffold(tmp_path, force=False)
        assert result['success'] is True
        assert len(result['written']) == 0
        assert len(result['skipped']) == 9

    def test_overwrite_when_forced(self, tmp_path):
        """When force=True, existing files should be overwritten."""
        generate_scaffold(tmp_path)
        # Corrupt one file
        (tmp_path / '.eslintrc.cjs').write_text('corrupted', encoding='utf-8')
        # Re-generate with force
        result = generate_scaffold(tmp_path, force=True)
        assert result['success'] is True
        assert len(result['written']) == 9
        assert result['skipped'] == []
        # File should be restored
        content = (tmp_path / '.eslintrc.cjs').read_text(encoding='utf-8')
        assert 'corrupted' not in content
        assert '@typescript-eslint/no-explicit-any' in content

    def test_missing_project_root(self, tmp_path):
        """Should fail when project_root doesn't exist."""
        result = generate_scaffold(tmp_path / 'nonexistent')
        assert result['success'] is False
        assert len(result['errors']) == 1
        assert 'does not exist' in result['errors'][0]

    def test_pre_commit_gets_executable_bit(self, tmp_path):
        """The pre-commit hook should be executable (chmod 0o755)."""
        generate_scaffold(tmp_path)
        precommit = tmp_path / '.husky' / 'pre-commit'
        assert precommit.exists()
        # On Windows, executable bit check is tricky; use stat
        # On POSIX, check S_IXUSR
        if os.name == 'posix':
            mode = precommit.stat().st_mode
            assert mode & 0o100, f'pre-commit not executable: {oct(mode)}'

    def test_creates_nested_directories(self, tmp_path):
        """Should create .github/workflows/ and .husky/ directories."""
        generate_scaffold(tmp_path)
        assert (tmp_path / '.github' / 'workflows').is_dir()
        assert (tmp_path / '.husky').is_dir()


# ── validate_scaffold ────────────────────────────────────────

class TestValidateScaffold:
    """validate_scaffold should catch both missing files and missing markers."""

    def test_validate_passes_after_generate(self, tmp_path):
        """Generate then validate should pass."""
        generate_scaffold(tmp_path)
        result = validate_scaffold(tmp_path)
        assert result['success'] is True
        assert result['missing_files'] == []
        assert result['marker_failures'] == []

    def test_validate_catches_missing_files(self, tmp_path):
        """Empty project should report all 9 missing files."""
        result = validate_scaffold(tmp_path)
        assert result['success'] is False
        assert len(result['missing_files']) == 9

    def test_validate_catches_corrupted_eslintrc(self, tmp_path):
        """eslintrc missing mandatory rules should fail validation."""
        generate_scaffold(tmp_path)
        # Corrupt: remove mandatory rules
        (tmp_path / '.eslintrc.cjs').write_text(
            "module.exports = { rules: {} };",
            encoding='utf-8',
        )
        result = validate_scaffold(tmp_path)
        assert result['success'] is False
        # Should have marker failures for eslintrc
        failures_by_file = {f['file']: f for f in result['marker_failures']}
        assert '.eslintrc.cjs' in failures_by_file
        assert len(failures_by_file['.eslintrc.cjs']['missing_markers']) > 0

    def test_validate_catches_tsconfig_strict_disabled(self, tmp_path):
        """tsconfig without strict:true should fail (form-compliance trap)."""
        generate_scaffold(tmp_path)
        # Write a tsconfig that exists but lacks strict flags
        (tmp_path / 'tsconfig.json').write_text(
            '{"compilerOptions": {"strict": false}}',
            encoding='utf-8',
        )
        result = validate_scaffold(tmp_path)
        assert result['success'] is False
        failures_by_file = {f['file']: f for f in result['marker_failures']}
        assert 'tsconfig.json' in failures_by_file
        missing = failures_by_file['tsconfig.json']['missing_markers']
        assert '"strict": true' in missing
        assert '"noImplicitAny": true' in missing

    def test_validate_catches_vite_missing_drop_console(self, tmp_path):
        """vite.config.ts missing drop_console should fail."""
        generate_scaffold(tmp_path)
        (tmp_path / 'vite.config.ts').write_text(
            "export default { build: { minify: 'terser' } };",
            encoding='utf-8',
        )
        result = validate_scaffold(tmp_path)
        assert result['success'] is False
        failures_by_file = {f['file']: f for f in result['marker_failures']}
        assert 'vite.config.ts' in failures_by_file

    def test_validate_catches_vitest_threshold_below_85(self, tmp_path):
        """vitest.config.ts with thresholds <85 should fail."""
        generate_scaffold(tmp_path)
        (tmp_path / 'vitest.config.ts').write_text(
            "export default { test: { coverage: { thresholds: { statements: 70 } } } };",
            encoding='utf-8',
        )
        result = validate_scaffold(tmp_path)
        assert result['success'] is False
        failures_by_file = {f['file']: f for f in result['marker_failures']}
        assert 'vitest.config.ts' in failures_by_file
        missing = failures_by_file['vitest.config.ts']['missing_markers']
        assert 'statements: 85' in missing

    def test_validate_returns_per_file_check_details(self, tmp_path):
        """Each check should have expected structure."""
        generate_scaffold(tmp_path)
        result = validate_scaffold(tmp_path)
        assert len(result['checks']) == 9
        for check in result['checks']:
            assert 'file' in check
            assert 'exists' in check
            assert 'markers_expected' in check
            assert 'markers_found' in check
            assert 'missing_markers' in check


# ── _atomic_write ────────────────────────────────────────────

class TestAtomicWrite:
    """_atomic_write should not leave partial files on failure."""

    def test_atomic_write_creates_file(self, tmp_path):
        target = tmp_path / 'output.txt'
        _atomic_write(target, 'hello world')
        assert target.read_text(encoding='utf-8') == 'hello world'

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        target = tmp_path / 'a' / 'b' / 'c' / 'output.txt'
        _atomic_write(target, 'nested')
        assert target.read_text(encoding='utf-8') == 'nested'

    def test_atomic_write_overwrites_existing(self, tmp_path):
        target = tmp_path / 'output.txt'
        target.write_text('old', encoding='utf-8')
        _atomic_write(target, 'new')
        assert target.read_text(encoding='utf-8') == 'new'

    def test_no_tmp_file_left_after_success(self, tmp_path):
        """After successful write, no .tmp- files should remain in parent."""
        target = tmp_path / 'output.txt'
        _atomic_write(target, 'content')
        tmp_files = list(tmp_path.glob('.output.txt.tmp-*'))
        assert tmp_files == []


# ── CLI smoke test ───────────────────────────────────────────

class TestCLI:
    """CLI should work end-to-end."""

    def test_cli_list_templates(self):
        """`python -m modules.bootstrap.generator --list` should exit 0."""
        result = subprocess.run(
            [sys.executable, '-m', 'modules.bootstrap.generator', '--list'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )
        assert result.returncode == 0
        assert 'eslintrc.cjs.template' in result.stdout
        assert 'tsconfig.json.template' in result.stdout

    def test_cli_validate_empty_project(self, tmp_path):
        """`--validate` on empty project should exit 1 with FAIL."""
        result = subprocess.run(
            [sys.executable, '-m', 'modules.bootstrap.generator',
             '--validate', '--target', str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )
        assert result.returncode == 1
        assert 'FAIL' in result.stdout

    def test_cli_generate_then_validate(self, tmp_path):
        """Generate then validate via CLI should both succeed."""
        # Generate
        gen_result = subprocess.run(
            [sys.executable, '-m', 'modules.bootstrap.generator',
             '--target', str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )
        assert gen_result.returncode == 0
        # Validate
        val_result = subprocess.run(
            [sys.executable, '-m', 'modules.bootstrap.generator',
             '--validate', '--target', str(tmp_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )
        assert val_result.returncode == 0
        assert 'PASS' in val_result.stdout

    def test_cli_json_output(self, tmp_path):
        """`--json` output should be valid JSON."""
        result = subprocess.run(
            [sys.executable, '-m', 'modules.bootstrap.generator',
             '--target', str(tmp_path), '--json'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
        )
        assert result.returncode == 0
        import json
        data = json.loads(result.stdout)
        assert data['success'] is True
        assert len(data['written']) == 9


# ── End-to-end ───────────────────────────────────────────────

class TestEndToEnd:
    """End-to-end: generate → validate → tamper → validate fails."""

    def test_round_trip(self, tmp_path):
        """Generate → validate → success."""
        gen = generate_scaffold(tmp_path)
        assert gen['success']
        val = validate_scaffold(tmp_path)
        assert val['success']

    def test_tamper_detection(self, tmp_path):
        """Tampering with any file should break validation."""
        generate_scaffold(tmp_path)
        # Tamper with each file one at a time and verify validation fails
        for name, (dest, markers) in TEMPLATES.items():
            target = tmp_path / dest
            if not target.exists():
                continue
            original = target.read_text(encoding='utf-8')
            try:
                # Truncate to 3 chars — definitely missing all markers
                target.write_text(original[:3], encoding='utf-8')
                val = validate_scaffold(tmp_path)
                assert not val['success'], f'Tampered {dest} not detected'
            finally:
                # Restore
                target.write_text(original, encoding='utf-8')

    def test_all_mandatory_markers_actually_exist_in_templates(self):
        """Every marker listed in TEMPLATES must exist in the corresponding
        template file. (Catches typos in marker definitions.)"""
        for name, (dest, markers) in TEMPLATES.items():
            template_path = _TEMPLATES_DIR / name
            content = template_path.read_text(encoding='utf-8')
            for marker in markers:
                assert marker in content, (
                    f'Marker {marker!r} not found in template {name}. '
                    f'This is a bug in the TEMPLATES dict — fix the marker.'
                )
