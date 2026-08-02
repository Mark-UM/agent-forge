"""Phase 3C — delivery-checklist tests.

Validates all 6 check categories:
1. engineering — file existence + mandatory markers + forbidden patterns
2. resource — manifest.json icons + no inline onclick
3. integration — delegates to Phase 3B (smoke test)
4. i18n — UIManager imports + setLocale refresh
5. test_quality — soft assertions + boundary tests
6. architecture — no any/default export/window/Core imports three

Plus whitelist mechanism, category summary, Markdown report, JSON output,
CLI, and Tower Stack 3D fixture simulation.
"""
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.delivery.checklist import (
    DeliveryFinding,
    Severity,
    run_delivery_checklist,
    load_whitelist,
    _check_engineering,
    _check_resource,
    _check_i18n,
    _check_test_quality,
    _check_architecture,
    _empty_whitelist,
)
from modules.delivery.reporter import (
    format_report_markdown,
    format_report_json,
    format_summary_table,
    group_by_category,
    group_by_severity,
    summarize,
)


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def empty_whitelist():
    return _empty_whitelist()


@pytest.fixture
def tower_stack_project(tmp_path):
    """Build a TS project with all 6 categories of issues (Tower Stack 3D bugs)."""
    src = tmp_path / 'src'
    (src / 'audio').mkdir(parents=True)
    (src / 'core').mkdir(parents=True)
    (src / 'ui').mkdir(parents=True)

    # SoundManager with init() defined but never called
    (src / 'audio' / 'SoundManager.ts').write_text(textwrap.dedent("""
        export class SoundManager {
          init() { console.log('init'); }
        }
    """), encoding='utf-8')

    # StackEngine: emit('game:over') no subscriber + rAF + PAUSED no cancel + Core imports three
    (src / 'core' / 'StackEngine.ts').write_text(textwrap.dedent("""
        import * as THREE from 'three';
        import { EventBus } from '@/services/EventBus';

        export default class StackEngine {
          private rafId: number | null = null;

          start() {
            this.rafId = requestAnimationFrame(() => this.update());
          }

          update() {}

          pause(state: string) {
            if (state === 'PAUSED') return;
          }

          gameOver() {
            EventBus.emit('game:over', { score: 0 });
          }

          process(data: any): any {
            return data as any;
          }
        }
    """), encoding='utf-8')

    # UIManager without i18n import
    (src / 'ui' / 'UIManager.ts').write_text(textwrap.dedent("""
        export class UIManager {
          render() {
            console.log('Score: 0');
          }
        }
    """), encoding='utf-8')

    # HUDPanel with hardcoded text (no i18n)
    (src / 'ui' / 'HUDPanel.ts').write_text(textwrap.dedent("""
        export class HUDPanel {
          render() {
            this.el.innerHTML = 'Game Over';
          }
        }
    """), encoding='utf-8')

    # main.ts: imports SoundManager but never calls .init() + direct render call
    (src / 'main.ts').write_text(textwrap.dedent("""
        import { StackEngine } from '@/core/StackEngine';
        import { SoundManager } from '@/audio/SoundManager';

        const sound = new SoundManager();
        const engine = new StackEngine();
        const renderer = { render: () => console.log('rendering') };
        renderer.render();
        engine.start();
    """), encoding='utf-8')

    # index.html with inline onclick
    (tmp_path / 'index.html').write_text(
        '<button onclick="startGame()">Start</button>', encoding='utf-8'
    )

    # manifest.json referencing missing icons
    (tmp_path / 'public').mkdir(exist_ok=True)
    (tmp_path / 'public' / 'manifest.json').write_text(json.dumps({
        'icons': [
            {'src': '/icon-192.png', 'sizes': '192x192'},
            {'src': '/icon-512.png', 'sizes': '512x512'},
        ]
    }), encoding='utf-8')

    # vitest.config.ts with low threshold + limited include
    (tmp_path / 'vitest.config.ts').write_text(textwrap.dedent("""
        export default {
          test: {
            coverage: {
              thresholds: { statements: 70, branches: 70, functions: 70, lines: 70 },
              include: ['src/core/**/*.ts']
            }
          }
        }
    """), encoding='utf-8')

    # Test file with soft assertions
    (src / 'core' / 'StackEngine.test.ts').write_text(textwrap.dedent("""
        describe('StackEngine', () => {
          it('returns a result', () => {
            const result = engine.cut();
            expect(['perfect', 'good', 'miss']).toContain(result);
            expect(result).toBeTruthy();
          });
        });
    """), encoding='utf-8')

    return tmp_path


@pytest.fixture
def clean_project(tmp_path):
    """Build a TS project that passes all 6 categories."""
    src = tmp_path / 'src'
    (src / 'audio').mkdir(parents=True)
    (src / 'core').mkdir(parents=True)
    (src / 'ui').mkdir(parents=True)
    (src / 'i18n').mkdir(parents=True)

    # SoundManager with init() called
    (src / 'audio' / 'SoundManager.ts').write_text(textwrap.dedent("""
        export class SoundManager {
          init() { console.log('init'); }
        }
    """), encoding='utf-8')

    # StackEngine: balanced emit/on + cancelAnimationFrame + no any/default export/three
    (src / 'core' / 'StackEngine.ts').write_text(textwrap.dedent("""
        import { EventBus } from '@/services/EventBus';

        export class StackEngine {
          private rafId: number | null = null;

          start() {
            this.rafId = requestAnimationFrame(() => this.update());
          }

          pause(state: string) {
            if (state === 'PAUSED') {
              if (this.rafId !== null) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
              }
              return;
            }
          }

          gameOver() {
            EventBus.emit('game:over', { score: 100 });
          }

          setup() {
            EventBus.on('game:over', (payload: { score: number }) => {
              console.log('game over', payload.score);
            });
          }
        }
    """), encoding='utf-8')

    # UIManager with i18n import
    (src / 'ui' / 'UIManager.ts').write_text(textwrap.dedent("""
        import { t } from '@/i18n';

        export class UIManager {
          render() {
            const text = t('hud.score');
            console.log(text);
          }
        }
    """), encoding='utf-8')

    # HUDPanel with i18n
    (src / 'ui' / 'HUDPanel.ts').write_text(textwrap.dedent("""
        import { t } from '@/i18n';

        export class HUDPanel {
          render() {
            const text = t('hud.gameOver');
            console.log(text);
          }
        }
    """), encoding='utf-8')

    # main.ts: calls sound.init() + no direct render call
    (src / 'main.ts').write_text(textwrap.dedent("""
        import { StackEngine } from '@/core/StackEngine';
        import { SoundManager } from '@/audio/SoundManager';

        const sound = new SoundManager();
        sound.init();
        const engine = new StackEngine();
        engine.start();
        engine.setup();
    """), encoding='utf-8')

    # Engineering files
    (tmp_path / '.eslintrc.cjs').write_text(textwrap.dedent("""
        module.exports = {
          rules: {
            '@typescript-eslint/no-explicit-any': 'error',
            'import/no-default-export': 'error'
          }
        };
    """), encoding='utf-8')

    (tmp_path / '.prettierrc').write_text('{"printWidth": 100}', encoding='utf-8')

    (tmp_path / '.husky').mkdir(exist_ok=True)
    pre_commit = tmp_path / '.husky' / 'pre-commit'
    pre_commit.write_text('npx lint-staged', encoding='utf-8')
    try:
        pre_commit.chmod(0o755)
    except (OSError, NotImplementedError):
        # chmod may not work on Windows; tests should still pass via content checks
        pass

    (tmp_path / '.github').mkdir(parents=True)
    (tmp_path / '.github' / 'workflows').mkdir(exist_ok=True)
    (tmp_path / '.github' / 'workflows' / 'ci.yml').write_text(
        'name: CI\njobs:\n  test:\n    steps:\n      - run: npm test\n      - run: npm run build\n',
        encoding='utf-8'
    )
    (tmp_path / '.github' / 'workflows' / 'deploy.yml').write_text(
        'name: Deploy\n', encoding='utf-8'
    )

    (tmp_path / 'vite.config.ts').write_text(textwrap.dedent("""
        export default {
          build: {
            minify: 'terser',
            terserOptions: { compress: { drop_console: true, drop_debugger: true } },
            rollupOptions: {
              output: {
                manualChunks: { three: ['three'] }
              }
            }
          }
        };
    """), encoding='utf-8')

    (tmp_path / 'vitest.config.ts').write_text(textwrap.dedent("""
        export default {
          test: {
            coverage: {
              thresholds: { statements: 85, branches: 85, functions: 85, lines: 85 },
              include: ['src/**/*.ts']
            }
          }
        };
    """), encoding='utf-8')

    (tmp_path / 'tsconfig.json').write_text(
        '{"compilerOptions": {"strict": true}}', encoding='utf-8'
    )

    # Clean index.html (no onclick)
    (tmp_path / 'index.html').write_text(
        '<button id="start">Start</button>', encoding='utf-8'
    )

    # manifest.json with existing icons
    (tmp_path / 'public').mkdir(exist_ok=True)
    (tmp_path / 'public' / 'manifest.json').write_text(json.dumps({
        'icons': [{'src': '/icon-192.png', 'sizes': '192x192'}]
    }), encoding='utf-8')
    (tmp_path / 'public' / 'icon-192.png').write_bytes(b'fake png')

    # Test file with proper assertions + boundary tests
    (src / 'core' / 'StackEngine.spec.ts').write_text(textwrap.dedent("""
        describe('StackEngine boundary', () => {
          it('4.9% threshold', () => {
            expect(engine.cut(0.049)).toBe('perfect');
          });
          it('5.0% threshold', () => {
            expect(engine.cut(0.05)).toBe('perfect');
          });
          it('5.1% threshold', () => {
            expect(engine.cut(0.051)).toBe('good');
          });
          it('overlap=0 triggers game over', () => {
            expect(engine.cut(0)).toBe('gameover');
          });
        });
    """), encoding='utf-8')

    return tmp_path


# ── Category 1: Engineering ──────────────────────────────────

class TestEngineering:
    def test_missing_files_detected(self, tmp_path, empty_whitelist):
        """Project with no engineering files → all 8 flagged as missing."""
        findings = _check_engineering(tmp_path, empty_whitelist['engineering'])
        missing = [f for f in findings if 'Missing file' in f.finding]
        assert len(missing) >= 8  # 8 engineering files

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_engineering(clean_project, empty_whitelist['engineering'])
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_eslintrc_with_off_rule_detected(self, tmp_path, empty_whitelist):
        (tmp_path / '.eslintrc.cjs').write_text(textwrap.dedent("""
            module.exports = {
              rules: {
                '@typescript-eslint/no-explicit-any': 'error',
                'import/no-default-export': 'off'
              }
            };
        """), encoding='utf-8')
        findings = _check_engineering(tmp_path, empty_whitelist['engineering'])
        off_findings = [f for f in findings if "forbidden pattern" in f.finding and '.eslintrc' in f.file]
        assert len(off_findings) >= 1

    def test_vitest_low_threshold_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_engineering(tower_stack_project, empty_whitelist['engineering'])
        threshold_findings = [f for f in findings if 'threshold' in f.finding and '< 85%' in f.finding]
        assert len(threshold_findings) >= 4  # 4 categories below 85

    def test_vitest_limited_include_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_engineering(tower_stack_project, empty_whitelist['engineering'])
        include_findings = [f for f in findings if 'coverage.include' in f.finding]
        assert len(include_findings) >= 1

    def test_whitelist_downgrades_to_info(self, tmp_path):
        wl = {'engineering': ['.eslintrc.cjs']}
        (tmp_path / '.eslintrc.cjs').write_text('{}', encoding='utf-8')
        findings = _check_engineering(tmp_path, wl['engineering'])
        eslintrc_findings = [f for f in findings if '.eslintrc' in f.file]
        assert all(f.severity == Severity.INFO.value for f in eslintrc_findings)


# ── Category 2: Resource ─────────────────────────────────────

class TestResource:
    def test_missing_manifest_icons_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_resource(tower_stack_project, empty_whitelist['resource'])
        icon_findings = [f for f in findings if 'missing icon' in f.finding]
        assert len(icon_findings) >= 2  # icon-192.png + icon-512.png

    def test_inline_onclick_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_resource(tower_stack_project, empty_whitelist['resource'])
        onclick_findings = [f for f in findings if 'onclick' in f.finding]
        assert len(onclick_findings) >= 1
        assert all(f.severity == Severity.WARNING.value for f in onclick_findings)

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_resource(clean_project, empty_whitelist['resource'])
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_no_manifest_no_error(self, tmp_path, empty_whitelist):
        """Project without manifest.json → no manifest-related findings."""
        findings = _check_resource(tmp_path, empty_whitelist['resource'])
        manifest_findings = [f for f in findings if 'manifest' in f.finding]
        assert manifest_findings == []


# ── Category 3: Integration (smoke test — Phase 3B has full coverage) ──

class TestIntegration:
    def test_tower_stack_fails_integration(self, tower_stack_project, empty_whitelist):
        from modules.delivery.checklist import _check_integration
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        findings = _check_integration(tower_stack_project, _empty_int())
        assert len(findings) > 0
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert len(errors) >= 4  # init_called + event_balance + state_cleanup + i18n_usage


# ── Category 4: i18n ─────────────────────────────────────────

class TestI18n:
    def test_uimanager_no_i18n_import(self, tower_stack_project, empty_whitelist):
        findings = _check_i18n(tower_stack_project, empty_whitelist['i18n'])
        ui_findings = [f for f in findings if 'UIManager' in f.finding]
        assert len(ui_findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in ui_findings)

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_i18n(clean_project, empty_whitelist['i18n'])
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_setlocale_without_refresh(self, tmp_path, empty_whitelist):
        src = tmp_path / 'src'
        src.mkdir(parents=True)
        (src / 'i18n.ts').write_text(textwrap.dedent("""
            export function setLocale(lang: string) {
                console.log('set', lang);
            }
        """), encoding='utf-8')
        (src / 'main.ts').write_text(textwrap.dedent("""
            import { setLocale } from './i18n';
            setLocale('en');
        """), encoding='utf-8')
        findings = _check_i18n(tmp_path, empty_whitelist['i18n'])
        refresh_findings = [f for f in findings if 'UI refresh' in f.finding]
        assert len(refresh_findings) >= 1


# ── Category 5: Test Quality ─────────────────────────────────

class TestTestQuality:
    def test_soft_assert_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_test_quality(tower_stack_project, empty_whitelist['test_quality'])
        soft = [f for f in findings if 'soft assertion' in f.finding]
        assert len(soft) >= 2  # toContain on string + toBeTruthy

    def test_no_boundary_tests_detected(self, tmp_path, empty_whitelist):
        src = tmp_path / 'src'
        src.mkdir(parents=True)
        (src / 'Foo.test.ts').write_text(textwrap.dedent("""
            it('works', () => { expect(1).toBe(1); });
        """), encoding='utf-8')
        findings = _check_test_quality(tmp_path, empty_whitelist['test_quality'])
        boundary = [f for f in findings if 'boundary' in f.finding]
        assert len(boundary) >= 1

    def test_clean_project_with_boundary_passes(self, clean_project, empty_whitelist):
        findings = _check_test_quality(clean_project, empty_whitelist['test_quality'])
        boundary = [f for f in findings if 'boundary' in f.finding]
        assert boundary == []


# ── Category 6: Architecture ─────────────────────────────────

class TestArchitecture:
    def test_core_imports_three_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_architecture(tower_stack_project, empty_whitelist['architecture'])
        three_findings = [f for f in findings if 'three' in f.finding]
        assert len(three_findings) >= 1

    def test_any_usage_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_architecture(tower_stack_project, empty_whitelist['architecture'])
        any_findings = [f for f in findings if 'any' in f.finding.lower()]
        assert len(any_findings) >= 1

    def test_default_export_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_architecture(tower_stack_project, empty_whitelist['architecture'])
        default_findings = [f for f in findings if 'default export' in f.finding]
        assert len(default_findings) >= 1

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_architecture(clean_project, empty_whitelist['architecture'])
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_ts_ignore_detected(self, tmp_path, empty_whitelist):
        src = tmp_path / 'src'
        src.mkdir(parents=True)
        (src / 'Foo.ts').write_text(textwrap.dedent("""
            export class Foo {
              // @ts-ignore
              bad(): number { return 'oops'; }
            }
        """), encoding='utf-8')
        findings = _check_architecture(tmp_path, empty_whitelist['architecture'])
        ts_ignore_findings = [f for f in findings if '@ts-ignore' in f.finding]
        assert len(ts_ignore_findings) >= 1


# ── Full Pipeline ────────────────────────────────────────────

class TestRunDeliveryChecklist:
    def test_tower_stack_fails(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        assert result['summary']['pass'] is False
        assert result['summary']['errors'] >= 5

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            clean_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        assert result['summary']['pass'] is True
        assert result['summary']['errors'] == 0

    def test_result_structure(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        assert 'findings' in result
        assert 'categories' in result
        assert 'summary' in result
        assert 'project_root' in result
        assert set(result['categories'].keys()) == {
            'engineering', 'resource', 'integration', 'i18n', 'test_quality', 'architecture'
        }
        for cat, stats in result['categories'].items():
            assert 'errors' in stats
            assert 'warnings' in stats
            assert 'infos' in stats
            assert 'pass' in stats

    def test_whitelist_reduces_errors(self, tower_stack_project):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        # Whitelist everything in delivery categories
        wl = {
            'engineering': ['.eslintrc.cjs', '.prettierrc', '.husky/pre-commit',
                            '.github/workflows/ci.yml', '.github/workflows/deploy.yml',
                            'vite.config.ts', 'vitest.config.ts', 'tsconfig.json',
                            'vitest.threshold.statements', 'vitest.threshold.branches',
                            'vitest.threshold.functions', 'vitest.threshold.lines',
                            'vitest.coverage.include'],
            'resource': ['manifest.icon:/icon-192.png', 'manifest.icon:/icon-512.png',
                         'onclick:startGame()'],
            'integration': [],
            'i18n': ['src/ui/UIManager.ts'],
            'test_quality': [],
            'architecture': ['core_imports_three:src/core/StackEngine.ts',
                             'any:src/core/StackEngine.ts:14',
                             'default_export:src/core/StackEngine.ts'],
        }
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=wl,
            integration_whitelist=_empty_int(),
        )
        # After whitelist: errors should be reduced (integration still produces errors
        # since we passed empty integration whitelist)
        assert result['summary']['errors'] < 20  # significantly reduced


# ── Reporter ─────────────────────────────────────────────────

class TestReporter:
    def test_markdown_report_has_summary(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        md = format_report_markdown(result)
        assert '## Delivery Checklist Report' in md
        assert 'Per-Category Summary' in md
        assert 'OVERALL' in md
        assert 'FAIL' in md

    def test_markdown_report_pass(self, clean_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            clean_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        md = format_report_markdown(result)
        assert 'PASS' in md

    def test_json_output_parseable(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        json_str = format_report_json(result)
        parsed = json.loads(json_str)
        assert parsed == result

    def test_summary_table_format(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        table = format_summary_table(result)
        assert '| Category |' in table
        assert '| engineering |' in table
        assert 'OVERALL' in table

    def test_group_by_category(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        grouped = group_by_category(result)
        assert 'engineering' in grouped
        assert 'architecture' in grouped

    def test_group_by_severity(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        grouped = group_by_severity(result)
        assert 'error' in grouped

    def test_summarize_fail(self, tower_stack_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        summary = summarize(result)
        assert summary.startswith('FAIL:')
        assert 'errors' in summary

    def test_summarize_pass(self, clean_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            clean_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        summary = summarize(result)
        assert summary.startswith('PASS:')


# ── Whitelist Loading ────────────────────────────────────────

class TestLoadWhitelist:
    def test_valid_whitelist(self, tmp_path):
        wl_file = tmp_path / 'wl.json'
        wl_file.write_text(json.dumps({
            'engineering': ['.eslintrc.cjs'],
            'architecture': ['src/core/Foo.ts']
        }), encoding='utf-8')
        wl = load_whitelist(wl_file)
        assert wl['engineering'] == ['.eslintrc.cjs']
        assert wl['architecture'] == ['src/core/Foo.ts']
        # Other keys default to []
        assert wl['resource'] == []
        assert wl['i18n'] == []

    def test_missing_file_returns_empty(self, tmp_path):
        wl = load_whitelist(tmp_path / 'nonexistent.json')
        assert wl == _empty_whitelist()

    def test_invalid_json_returns_empty(self, tmp_path):
        wl_file = tmp_path / 'wl.json'
        wl_file.write_text('{invalid json', encoding='utf-8')
        wl = load_whitelist(wl_file)
        assert wl == _empty_whitelist()


# ── CLI ──────────────────────────────────────────────────────

class TestCLI:
    def test_cli_fails_on_tower_stack(self, tower_stack_project):
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'modules.delivery.checklist',
             '--target', str(tower_stack_project)],
            capture_output=True, text=True, encoding='utf-8', cwd=str(PROJECT_ROOT)
        )
        assert result.returncode == 1  # FAIL
        assert 'FAIL' in result.stdout

    def test_cli_json_output(self, tower_stack_project):
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'modules.delivery.checklist',
             '--target', str(tower_stack_project), '--json'],
            capture_output=True, text=True, encoding='utf-8', cwd=str(PROJECT_ROOT)
        )
        parsed = json.loads(result.stdout)
        assert 'findings' in parsed
        assert 'categories' in parsed


# ── End-to-End ───────────────────────────────────────────────

class TestEndToEnd:
    def test_tower_stack_3d_fatal_bugs_caught(self, tower_stack_project, empty_whitelist):
        """Verify the 4 Tower Stack 3D fatal bug categories are caught + more."""
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            tower_stack_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        categories_with_errors = [
            cat for cat, stats in result['categories'].items()
            if stats['errors'] > 0
        ]
        # Should have errors in engineering, integration, i18n, architecture at minimum
        assert 'engineering' in categories_with_errors
        assert 'integration' in categories_with_errors
        assert 'i18n' in categories_with_errors
        assert 'architecture' in categories_with_errors

    def test_clean_project_zero_errors(self, clean_project, empty_whitelist):
        from modules.integration_check.checker import _empty_whitelist as _empty_int
        result = run_delivery_checklist(
            clean_project,
            whitelist=empty_whitelist,
            integration_whitelist=_empty_int(),
        )
        assert result['summary']['errors'] == 0
        assert result['summary']['pass'] is True
