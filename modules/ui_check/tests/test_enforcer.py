"""Phase 3D — ui-component-enforcer tests.

Validates all 5 rules:
1. directories — src/ui/panels/ + src/ui/components/ must exist
2. panel_classes — each Panel must be a class extending BasePanel
3. inline_onclick — no onclick="..." in index.html
4. window_globals — no (window as unknown as ...) in UI files
5. uimanager_i18n — UIManager imports { t, setLocale } from i18n

Plus whitelist mechanism, Markdown report, JSON output, CLI,
and Tower Stack 3D fixture simulation.
"""
import json
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.ui_check.enforcer import (
    UIFinding,
    Severity,
    run_ui_checks,
    load_whitelist,
    _check_directories,
    _check_panel_classes,
    _check_inline_onclick,
    _check_window_globals,
    _check_uimanager_i18n,
    _empty_whitelist,
    format_report_markdown,
)


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def empty_whitelist():
    return _empty_whitelist()


@pytest.fixture
def tower_stack_project(tmp_path):
    """TS project with all UI architecture violations (EX game(ds) style)."""
    src = tmp_path / 'src'
    (src / 'ui').mkdir(parents=True)
    # NOTE: no src/ui/panels/ or src/ui/components/ directories

    # UIManager without i18n + uses getElementById + window globals
    (src / 'ui' / 'UIManager.ts').write_text(textwrap.dedent("""
        export class UIManager {
          render() {
            const el = document.getElementById('app');
            if (el) el.innerHTML = 'Score: 0';
          }

          exposeGlobals() {
            (window as unknown as Record<string, unknown>).startGame = () => {
              console.log('starting');
            };
          }
        }
    """), encoding='utf-8')

    # HUDPanel without BasePanel extension
    (src / 'ui' / 'HUDPanel.ts').write_text(textwrap.dedent("""
        export class HUDPanel {
          render() {
            const el = document.getElementById('hud');
            if (el) el.textContent = 'Game Over';
          }
        }
    """), encoding='utf-8')

    # index.html with inline onclick
    (tmp_path / 'index.html').write_text(
        '<button onclick="startGame()">Start</button>',
        encoding='utf-8'
    )

    # main.ts with window global exposure
    (src / 'main.ts').write_text(textwrap.dedent("""
        (window as unknown as Record<string, unknown>).pauseGame = () => {};
    """), encoding='utf-8')

    return tmp_path


@pytest.fixture
def clean_project(tmp_path):
    """TS project with proper UI architecture."""
    src = tmp_path / 'src'
    (src / 'ui' / 'panels').mkdir(parents=True)
    (src / 'ui' / 'components').mkdir(parents=True)

    # BasePanel
    (src / 'ui' / 'panels' / 'BasePanel.ts').write_text(textwrap.dedent("""
        export abstract class BasePanel {
          constructor(protected el: HTMLElement) {}
          abstract refreshText(): void;
        }
    """), encoding='utf-8')

    # HUDPanel extending BasePanel
    (src / 'ui' / 'panels' / 'HUDPanel.ts').write_text(textwrap.dedent("""
        import { BasePanel } from './BasePanel';
        import { EventBus } from '@/services/EventBus';
        import { t } from '@/i18n';

        export class HUDPanel extends BasePanel {
          constructor(el: HTMLElement) {
            super(el);
            EventBus.on('score:update', (p: { score: number }) => this.onScore(p.score));
          }

          private onScore(score: number) {
            this.el.textContent = t('hud.score', { value: score });
          }

          refreshText(): void {
            this.el.textContent = t('hud.score');
          }
        }
    """), encoding='utf-8')

    # ScoreComponent
    (src / 'ui' / 'components' / 'ScoreComponent.ts').write_text(textwrap.dedent("""
        export class ScoreComponent {
          constructor(private el: HTMLElement) {}
        }
    """), encoding='utf-8')

    # UIManager with i18n imports
    (src / 'ui' / 'UIManager.ts').write_text(textwrap.dedent("""
        import { t, setLocale } from '@/i18n';
        import { HUDPanel } from './panels/HUDPanel';

        export class UIManager {
          private hud: HUDPanel;

          constructor() {
            const el = document.getElementById('hud') as HTMLElement;
            this.hud = new HUDPanel(el);
          }

          setLanguage(lang: string) {
            setLocale(lang);
          }
        }
    """), encoding='utf-8')

    # Clean index.html (no onclick)
    (tmp_path / 'index.html').write_text(
        '<button id="start">Start</button>',
        encoding='utf-8'
    )

    return tmp_path


# ── Rule 1: Directories ─────────────────────────────────────

class TestDirectories:
    def test_missing_directories_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_directories(tower_stack_project, empty_whitelist['directories'])
        assert len(findings) >= 2  # panels/ + components/
        assert all(f.severity == Severity.ERROR.value for f in findings)

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_directories(clean_project, empty_whitelist['directories'])
        assert findings == []

    def test_whitelist_downgrades_to_info(self, tmp_path):
        wl = {'directories': ['dir:src/ui/panels', 'dir:src/ui/components']}
        findings = _check_directories(tmp_path, wl['directories'])
        assert all(f.severity == Severity.INFO.value for f in findings)


# ── Rule 2: Panel Classes ───────────────────────────────────

class TestPanelClasses:
    def test_panel_without_basepanel_detected(self, tower_stack_project, empty_whitelist):
        """tower_stack_project has HUDPanel.ts in src/ui/ but not in panels/."""
        # Move HUDPanel.ts into panels/ for this test
        panels_dir = tower_stack_project / 'src' / 'ui' / 'panels'
        panels_dir.mkdir(parents=True, exist_ok=True)
        hud = tower_stack_project / 'src' / 'ui' / 'HUDPanel.ts'
        hud.rename(panels_dir / 'HUDPanel.ts')
        findings = _check_panel_classes(tower_stack_project, empty_whitelist['panel_classes'])
        basepanel_findings = [f for f in findings if 'BasePanel' in f.finding]
        assert len(basepanel_findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in basepanel_findings)

    def test_panel_with_get_element_by_id_detected(self, tmp_path, empty_whitelist):
        panels = tmp_path / 'src' / 'ui' / 'panels'
        panels.mkdir(parents=True)
        (panels / 'BadPanel.ts').write_text(textwrap.dedent("""
            import { BasePanel } from './BasePanel';
            export class BadPanel extends BasePanel {
              render() {
                const el = document.getElementById('bad');
              }
              refreshText(): void {}
            }
        """), encoding='utf-8')
        findings = _check_panel_classes(tmp_path, empty_whitelist['panel_classes'])
        get_el_findings = [f for f in findings if 'getElementById' in f.finding]
        assert len(get_el_findings) >= 1

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_panel_classes(clean_project, empty_whitelist['panel_classes'])
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_component_without_class_detected(self, tmp_path, empty_whitelist):
        components = tmp_path / 'src' / 'ui' / 'components'
        components.mkdir(parents=True)
        (components / 'bad.ts').write_text('const x = 1;', encoding='utf-8')
        findings = _check_panel_classes(tmp_path, empty_whitelist['panel_classes'])
        class_findings = [f for f in findings if 'no class' in f.finding]
        assert len(class_findings) >= 1


# ── Rule 3: Inline onclick ──────────────────────────────────

class TestInlineOnclick:
    def test_inline_onclick_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_inline_onclick(tower_stack_project, empty_whitelist['inline_onclick'])
        assert len(findings) >= 1
        assert all(f.severity == Severity.WARNING.value for f in findings)

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_inline_onclick(clean_project, empty_whitelist['inline_onclick'])
        assert findings == []

    def test_no_index_html_no_error(self, tmp_path, empty_whitelist):
        findings = _check_inline_onclick(tmp_path, empty_whitelist['inline_onclick'])
        assert findings == []


# ── Rule 4: Window Globals ──────────────────────────────────

class TestWindowGlobals:
    def test_window_global_in_uimanager_detected(self, tower_stack_project, empty_whitelist):
        findings = _check_window_globals(tower_stack_project, empty_whitelist['window_globals'])
        assert len(findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in findings)

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_window_globals(clean_project, empty_whitelist['window_globals'])
        assert findings == []

    def test_whitelist_downgrades(self, tmp_path):
        src = tmp_path / 'src' / 'ui'
        src.mkdir(parents=True)
        # Put window global on line 1 (no leading newline) so line number is predictable
        (src / 'Foo.ts').write_text(
            '(window as unknown as Record<string, unknown>).foo = () => {};',
            encoding='utf-8'
        )
        rel = 'src/ui/Foo.ts'
        wl = {'window_globals': [f'window_global:{rel}:1']}
        findings = _check_window_globals(tmp_path, wl['window_globals'])
        assert all(f.severity == Severity.INFO.value for f in findings)


# ── Rule 5: UIManager i18n ──────────────────────────────────

class TestUIManagerI18n:
    def test_uimanager_no_i18n_import(self, tower_stack_project, empty_whitelist):
        findings = _check_uimanager_i18n(tower_stack_project, empty_whitelist['uimanager_i18n'])
        i18n_findings = [f for f in findings if 't }' in f.finding]
        assert len(i18n_findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in i18n_findings)

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        findings = _check_uimanager_i18n(clean_project, empty_whitelist['uimanager_i18n'])
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_uimanager_no_setlocale_warning(self, tmp_path, empty_whitelist):
        src = tmp_path / 'src'
        src.mkdir(parents=True)
        (src / 'UIManager.ts').write_text(textwrap.dedent("""
            import { t } from '@/i18n';
            export class UIManager { render() { return t('hello'); } }
        """), encoding='utf-8')
        findings = _check_uimanager_i18n(tmp_path, empty_whitelist['uimanager_i18n'])
        setlocale_findings = [f for f in findings if 'setLocale' in f.finding]
        assert len(setlocale_findings) >= 1
        assert all(f.severity == Severity.WARNING.value for f in setlocale_findings)


# ── Full Pipeline ───────────────────────────────────────────

class TestRunUIChecks:
    def test_tower_stack_fails(self, tower_stack_project, empty_whitelist):
        result = run_ui_checks(tower_stack_project, whitelist=empty_whitelist)
        assert result['summary']['pass'] is False
        assert result['summary']['errors'] >= 3

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        result = run_ui_checks(clean_project, whitelist=empty_whitelist)
        assert result['summary']['pass'] is True
        assert result['summary']['errors'] == 0

    def test_result_structure(self, tower_stack_project, empty_whitelist):
        result = run_ui_checks(tower_stack_project, whitelist=empty_whitelist)
        assert 'findings' in result
        assert 'rules' in result
        assert 'summary' in result
        assert 'project_root' in result
        assert set(result['rules'].keys()) == {
            'directories', 'panel_classes', 'inline_onclick',
            'window_globals', 'uimanager_i18n'
        }

    def test_whitelist_reduces_errors(self, tower_stack_project):
        wl = {
            'directories': ['dir:src/ui/panels', 'dir:src/ui/components'],
            'panel_classes': [],
            'inline_onclick': ['onclick:startGame()'],
            'window_globals': [],
            'uimanager_i18n': ['uimanager_i18n:src/ui/UIManager.ts'],
        }
        result = run_ui_checks(tower_stack_project, whitelist=wl)
        assert result['summary']['errors'] < 5  # reduced from initial errors


# ── Reporter ────────────────────────────────────────────────

class TestReporter:
    def test_markdown_report_fail(self, tower_stack_project, empty_whitelist):
        result = run_ui_checks(tower_stack_project, whitelist=empty_whitelist)
        md = format_report_markdown(result)
        assert '## UI Component Enforcer Report' in md
        assert 'FAIL' in md
        assert 'directories' in md

    def test_markdown_report_pass(self, clean_project, empty_whitelist):
        result = run_ui_checks(clean_project, whitelist=empty_whitelist)
        md = format_report_markdown(result)
        assert 'PASS' in md

    def test_markdown_report_no_findings_message(self, clean_project, empty_whitelist):
        result = run_ui_checks(clean_project, whitelist=empty_whitelist)
        md = format_report_markdown(result)
        assert 'No UI architecture issues found' in md or 'PASS' in md


# ── Whitelist Loading ───────────────────────────────────────

class TestLoadWhitelist:
    def test_valid_whitelist(self, tmp_path):
        wl_file = tmp_path / 'wl.json'
        wl_file.write_text(json.dumps({
            'directories': ['dir:src/ui/panels'],
            'window_globals': ['window_global:src/ui/Foo.ts:1']
        }), encoding='utf-8')
        wl = load_whitelist(wl_file)
        assert wl['directories'] == ['dir:src/ui/panels']
        assert wl['window_globals'] == ['window_global:src/ui/Foo.ts:1']
        assert wl['panel_classes'] == []
        assert wl['inline_onclick'] == []

    def test_missing_file_returns_empty(self, tmp_path):
        wl = load_whitelist(tmp_path / 'nonexistent.json')
        assert wl == _empty_whitelist()

    def test_invalid_json_returns_empty(self, tmp_path):
        wl_file = tmp_path / 'wl.json'
        wl_file.write_text('{invalid', encoding='utf-8')
        wl = load_whitelist(wl_file)
        assert wl == _empty_whitelist()


# ── CLI ─────────────────────────────────────────────────────

class TestCLI:
    def test_cli_fails_on_tower_stack(self, tower_stack_project):
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'modules.ui_check.enforcer',
             '--target', str(tower_stack_project)],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )
        assert result.returncode == 1
        assert 'FAIL' in result.stdout

    def test_cli_json_output(self, tower_stack_project):
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'modules.ui_check.enforcer',
             '--target', str(tower_stack_project), '--json'],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )
        parsed = json.loads(result.stdout)
        assert 'findings' in parsed
        assert 'rules' in parsed


# ── End-to-End ──────────────────────────────────────────────

class TestEndToEnd:
    def test_tower_stack_3d_ui_bugs_caught(self, tower_stack_project, empty_whitelist):
        """Verify all 5 UI architecture issues from EX game(ds) are caught."""
        result = run_ui_checks(tower_stack_project, whitelist=empty_whitelist)
        rules_with_errors = [
            rule for rule, stats in result['rules'].items()
            if stats['errors'] > 0
        ]
        # Should have errors in directories, window_globals, uimanager_i18n
        assert 'directories' in rules_with_errors
        assert 'window_globals' in rules_with_errors
        assert 'uimanager_i18n' in rules_with_errors

    def test_clean_project_zero_errors(self, clean_project, empty_whitelist):
        result = run_ui_checks(clean_project, whitelist=empty_whitelist)
        assert result['summary']['errors'] == 0
        assert result['summary']['pass'] is True
