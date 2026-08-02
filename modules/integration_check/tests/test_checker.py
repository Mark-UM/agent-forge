"""Phase 3B — integration-link-checker tests.

Validates all 5 rules:
1. init_called — error when init() defined but never called in bootstrap
2. event_balance — error when emit('X') without matching on('X')
3. state_cleanup — error when rAF + PAUSED state but no cancelAnimationFrame
4. i18n_usage — error when UI file has hardcoded text without i18n import
5. cross_layer_call — warning when main.ts directly calls render methods

Plus whitelist mechanism, severity downgrade, Markdown report format, JSON output,
and Tower Stack 3D fixture simulation.
"""
import json
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.integration_check import checker
from modules.integration_check.checker import (
    Finding,
    Severity,
    _check_init_called,
    _check_event_balance,
    _check_state_cleanup,
    _check_i18n_usage,
    _check_cross_layer_call,
    format_report_markdown,
    load_whitelist,
    run_checks,
)
from modules.integration_check.reporter import (
    format_report_json,
    format_report_table,
    group_by_rule,
    group_by_severity,
    summarize,
)


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def empty_whitelist():
    return checker._empty_whitelist()


@pytest.fixture
def ts_project(tmp_path):
    """Build a minimal TS project with intentional integration breaks.

    Contains the 4 Tower Stack 3D fatal bugs:
    - SoundManager.init() defined but not called in main.ts
    - emit('game:over') with no matching on()
    - requestAnimationFrame + PAUSED state but no cancelAnimationFrame
    - UI file with hardcoded text and no i18n import
    Plus 1 warning: main.ts directly calling renderer.render()
    """
    src = tmp_path / 'src'
    (src / 'audio').mkdir(parents=True)
    (src / 'core').mkdir(parents=True)
    (src / 'ui').mkdir(parents=True)

    # SoundManager with init() defined but never called
    (src / 'audio' / 'SoundManager.ts').write_text(textwrap.dedent("""
        export class SoundManager {
          private ctx: AudioContext | null = null;

          init() {
            this.ctx = new AudioContext();
          }

          play(sound: string) {
            if (this.ctx) {
              console.log('playing', sound);
            }
          }
        }
    """), encoding='utf-8')

    # StackEngine with emit('game:over') but no matching on
    # Also: rAF + PAUSED state but no cancelAnimationFrame
    (src / 'core' / 'StackEngine.ts').write_text(textwrap.dedent("""
        import { EventBus } from '@/services/EventBus';

        export class StackEngine {
          private rafId: number | null = null;

          start() {
            this.rafId = requestAnimationFrame(() => this.update());
          }

          update() {
            // game loop
          }

          pause(state: string) {
            if (state === 'PAUSED') {
              // BUG: only skip update, no cancelAnimationFrame
              return;
            }
          }

          gameOver() {
            EventBus.emit('game:over', { score: 0 });
          }
        }
    """), encoding='utf-8')

    # UI file with hardcoded text, no i18n import
    (src / 'ui' / 'HUDPanel.ts').write_text(textwrap.dedent("""
        export class HUDPanel {
          private el: HTMLElement;

          constructor(el: HTMLElement) {
            this.el = el;
          }

          render() {
            this.el.innerHTML = 'Score: 0';
            this.el.textContent = 'Game Over';
          }
        }
    """), encoding='utf-8')

    # main.ts directly calls renderer.render() — warning
    (src / 'main.ts').write_text(textwrap.dedent("""
        import { StackEngine } from '@/core/StackEngine';
        import { SoundManager } from '@/audio/SoundManager';

        const engine = new StackEngine();
        const sound = new SoundManager();
        // BUG: sound.init() never called
        // BUG: renderer.render() direct call (bypass EventBus)
        const renderer = { render: () => console.log('rendering') };
        renderer.render();
        engine.start();
    """), encoding='utf-8')

    return tmp_path


@pytest.fixture
def clean_project(tmp_path):
    """Build a clean TS project with no integration breaks."""
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

    # StackEngine with emit/on balanced + rAF + cancelAnimationFrame in PAUSED
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
            EventBus.on('game:over', (payload) => {
              console.log('game over', payload);
            });
          }
        }
    """), encoding='utf-8')

    # UI file with i18n import and t() calls
    (src / 'ui' / 'HUDPanel.ts').write_text(textwrap.dedent("""
        import { t } from '@/i18n';

        export class HUDPanel {
          render() {
            const text = t('hud.score');
            console.log(text);
          }
        }
    """), encoding='utf-8')

    # main.ts with SoundManager.init() call, no direct render access
    (src / 'main.ts').write_text(textwrap.dedent("""
        import { StackEngine } from '@/core/StackEngine';
        import { SoundManager } from '@/audio/SoundManager';

        const sound = new SoundManager();
        sound.init();
        const engine = new StackEngine();
        engine.start();
        engine.setup();
    """), encoding='utf-8')

    return tmp_path


# ── Rule 1: init_called ──────────────────────────────────────

class TestInitCalled:
    def test_detects_uncalled_init(self, ts_project, empty_whitelist):
        findings = _check_init_called(ts_project, empty_whitelist['init_called'])
        sound_findings = [f for f in findings if 'SoundManager' in f.finding]
        assert len(sound_findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in sound_findings)

    def test_no_finding_when_init_is_called(self, clean_project, empty_whitelist):
        findings = _check_init_called(clean_project, empty_whitelist['init_called'])
        sound_findings = [f for f in findings if 'SoundManager' in f.finding]
        assert sound_findings == []

    def test_whitelist_downgrades_to_info(self, ts_project):
        whitelist = {'init_called': ['SoundManager.init']}
        findings = _check_init_called(ts_project, whitelist['init_called'])
        sound_findings = [f for f in findings if 'SoundManager' in f.finding]
        assert len(sound_findings) >= 1
        assert all(f.severity == Severity.INFO.value for f in sound_findings)


# ── Rule 2: event_balance ────────────────────────────────────

class TestEventBalance:
    def test_detects_orphan_emit(self, ts_project, empty_whitelist):
        findings = _check_event_balance(ts_project, empty_whitelist['event_balance'])
        game_over_findings = [f for f in findings if 'game:over' in f.finding]
        assert len(game_over_findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in game_over_findings)

    def test_no_finding_when_emit_has_subscriber(self, clean_project, empty_whitelist):
        findings = _check_event_balance(clean_project, empty_whitelist['event_balance'])
        game_over_findings = [f for f in findings if 'game:over' in f.finding]
        assert game_over_findings == []

    def test_whitelist_downgrades_to_info(self, ts_project):
        whitelist = {'event_balance': ['game:over']}
        findings = _check_event_balance(ts_project, whitelist['event_balance'])
        game_over_findings = [f for f in findings if 'game:over' in f.finding]
        assert all(f.severity == Severity.INFO.value for f in game_over_findings)


# ── Rule 3: state_cleanup ────────────────────────────────────

class TestStateCleanup:
    def test_detects_raf_without_cancel_in_paused(self, ts_project, empty_whitelist):
        findings = _check_state_cleanup(ts_project, empty_whitelist['state_cleanup'])
        assert len(findings) >= 1
        assert all(f.severity == Severity.ERROR.value for f in findings)
        assert 'cancelAnimationFrame' in findings[0].finding

    def test_no_finding_when_cancel_exists(self, clean_project, empty_whitelist):
        findings = _check_state_cleanup(clean_project, empty_whitelist['state_cleanup'])
        assert findings == []

    def test_no_finding_without_paused_state(self, tmp_path, empty_whitelist):
        """rAF without PAUSED state is fine — no finding."""
        src = tmp_path / 'src'
        src.mkdir(parents=True)
        (src / 'Engine.ts').write_text(textwrap.dedent("""
            export class Engine {
              start() {
                requestAnimationFrame(() => this.update());
              }
              update() {}
            }
        """), encoding='utf-8')
        findings = _check_state_cleanup(tmp_path, empty_whitelist['state_cleanup'])
        assert findings == []

    def test_no_finding_without_raf(self, tmp_path, empty_whitelist):
        """PAUSED state without rAF is fine — no finding."""
        src = tmp_path / 'src'
        src.mkdir(parents=True)
        (src / 'Engine.ts').write_text(textwrap.dedent("""
            export class Engine {
              pause(state: string) {
                if (state === 'PAUSED') return;
              }
            }
        """), encoding='utf-8')
        findings = _check_state_cleanup(tmp_path, empty_whitelist['state_cleanup'])
        assert findings == []


# ── Rule 4: i18n_usage ───────────────────────────────────────

class TestI18nUsage:
    def test_detects_hardcoded_text_without_import(self, ts_project, empty_whitelist):
        findings = _check_i18n_usage(ts_project, empty_whitelist['i18n_usage'])
        assert len(findings) >= 1
        assert any(f.severity == Severity.ERROR.value for f in findings)
        assert any('no i18n import' in f.finding for f in findings)

    def test_no_finding_when_i18n_used(self, clean_project, empty_whitelist):
        findings = _check_i18n_usage(clean_project, empty_whitelist['i18n_usage'])
        # Clean project's HUDPanel uses t() — no hardcoded text
        # But verify no errors
        errors = [f for f in findings if f.severity == Severity.ERROR.value]
        assert errors == []

    def test_detects_hardcoded_text_with_import(self, tmp_path, empty_whitelist):
        """File imports i18n but still has hardcoded text → flag each instance."""
        src = tmp_path / 'src' / 'ui'
        src.mkdir(parents=True)
        (src / 'Panel.ts').write_text(textwrap.dedent("""
            import { t } from '@/i18n';
            export class Panel {
              render() {
                this.el.innerHTML = 'Hardcoded';
                const x = t('key');
              }
            }
        """), encoding='utf-8')
        findings = _check_i18n_usage(tmp_path, empty_whitelist['i18n_usage'])
        assert len(findings) >= 1
        assert any('Hardcoded' in f.finding for f in findings)


# ── Rule 5: cross_layer_call ─────────────────────────────────

class TestCrossLayerCall:
    def test_detects_direct_render_call(self, ts_project, empty_whitelist):
        findings = _check_cross_layer_call(ts_project, empty_whitelist['cross_layer_call'])
        # main.ts has renderer.render() call
        render_findings = [f for f in findings if 'render' in f.finding.lower()]
        assert len(render_findings) >= 1
        assert all(f.severity == Severity.WARNING.value for f in render_findings)

    def test_no_finding_when_no_render_call(self, clean_project, empty_whitelist):
        findings = _check_cross_layer_call(clean_project, empty_whitelist['cross_layer_call'])
        assert findings == []


# ── run_checks (integration) ─────────────────────────────────

class TestRunChecks:
    def test_tower_stack_fixture_fails(self, ts_project, empty_whitelist):
        """The Tower Stack 3D fixture should FAIL with multiple errors."""
        result = run_checks(ts_project, whitelist=empty_whitelist)
        assert result['summary']['pass'] is False
        assert result['summary']['errors'] >= 4  # at least 4 of the 5 rules fire

    def test_clean_project_passes(self, clean_project, empty_whitelist):
        """Clean project should PASS with zero errors."""
        result = run_checks(clean_project, whitelist=empty_whitelist)
        assert result['summary']['pass'] is True
        assert result['summary']['errors'] == 0

    def test_result_structure(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        assert 'project_root' in result
        assert 'findings' in result
        assert 'summary' in result
        assert isinstance(result['findings'], list)
        s = result['summary']
        assert 'total' in s
        assert 'errors' in s
        assert 'warnings' in s
        assert 'infos' in s
        assert 'pass' in s

    def test_whitelist_reduces_errors(self, ts_project):
        """Whitelisting all findings should reduce errors to 0."""
        whitelist = {
            'init_called': ['SoundManager.init'],
            'event_balance': ['game:over'],
            'state_cleanup': ['src/core/StackEngine.ts'],
            'i18n_usage': ['src/ui/HUDPanel.ts'],
            'cross_layer_call': ['src/main.ts'],
        }
        result = run_checks(ts_project, whitelist=whitelist)
        assert result['summary']['errors'] == 0
        # All findings should be info or warning
        assert all(f['severity'] in ('info', 'warning') for f in result['findings'])


# ── Markdown report ──────────────────────────────────────────

class TestMarkdownReport:
    def test_report_contains_table_header(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        report = format_report_markdown(result)
        assert '| # | Rule | Severity | File:Line | Finding |' in report
        assert '|---|------|----------|-----------|---------|' in report

    def test_report_contains_summary(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        report = format_report_markdown(result)
        assert '## Summary:' in report
        assert 'FAIL' in report

    def test_report_pass_message(self, clean_project, empty_whitelist):
        result = run_checks(clean_project, whitelist=empty_whitelist)
        report = format_report_markdown(result)
        assert 'PASS' in report


# ── Reporter ─────────────────────────────────────────────────

class TestReporter:
    def test_json_output_parseable(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        json_str = format_report_json(result)
        parsed = json.loads(json_str)
        assert parsed == result

    def test_table_alias(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        assert format_report_table(result) == format_report_markdown(result)

    def test_summarize_fail(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        summary = summarize(result)
        assert summary.startswith('FAIL')
        assert 'errors' in summary

    def test_summarize_pass(self, clean_project, empty_whitelist):
        result = run_checks(clean_project, whitelist=empty_whitelist)
        summary = summarize(result)
        assert summary.startswith('PASS')

    def test_group_by_rule(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        grouped = group_by_rule(result)
        assert isinstance(grouped, dict)
        # Should have at least 4 rule groups
        assert len(grouped) >= 4

    def test_group_by_severity(self, ts_project, empty_whitelist):
        result = run_checks(ts_project, whitelist=empty_whitelist)
        grouped = group_by_severity(result)
        assert 'error' in grouped
        assert 'warning' in grouped
        assert len(grouped['error']) >= 4


# ── load_whitelist ───────────────────────────────────────────

class TestLoadWhitelist:
    def test_load_valid_whitelist(self, tmp_path):
        wl_path = tmp_path / 'whitelist.json'
        wl_path.write_text(json.dumps({
            'init_called': ['Foo.init'],
            'event_balance': ['bar:event'],
        }), encoding='utf-8')
        wl = load_whitelist(wl_path)
        assert wl['init_called'] == ['Foo.init']
        assert wl['event_balance'] == ['bar:event']
        # Missing keys should be filled in
        assert wl['state_cleanup'] == []
        assert wl['i18n_usage'] == []
        assert wl['cross_layer_call'] == []

    def test_load_missing_file_returns_empty(self, tmp_path):
        wl = load_whitelist(tmp_path / 'nonexistent.json')
        assert wl == checker._empty_whitelist()

    def test_load_invalid_json_returns_empty(self, tmp_path):
        wl_path = tmp_path / 'whitelist.json'
        wl_path.write_text('not valid json', encoding='utf-8')
        wl = load_whitelist(wl_path)
        assert wl == checker._empty_whitelist()


# ── CLI smoke test ───────────────────────────────────────────

class TestCLI:
    def test_cli_fails_on_tower_stack(self, ts_project):
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'modules.integration_check.checker',
             '--target', str(ts_project)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert 'FAIL' in result.stdout

    def test_cli_json_output(self, ts_project):
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'modules.integration_check.checker',
             '--target', str(ts_project), '--json'],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data['summary']['pass'] is False


# ── End-to-end ───────────────────────────────────────────────

class TestEndToEnd:
    def test_tower_stack_3d_fatal_bugs_caught(self, ts_project, empty_whitelist):
        """Verify the 4 Tower Stack 3D fatal bugs are all caught."""
        result = run_checks(ts_project, whitelist=empty_whitelist)
        findings_by_rule = {f['rule'] for f in result['findings']}
        # Should catch all 4 fatal bug categories
        assert 'init_called' in findings_by_rule
        assert 'event_balance' in findings_by_rule
        assert 'state_cleanup' in findings_by_rule
        assert 'i18n_usage' in findings_by_rule

    def test_clean_project_zero_errors(self, clean_project, empty_whitelist):
        result = run_checks(clean_project, whitelist=empty_whitelist)
        assert result['summary']['errors'] == 0
        assert result['summary']['pass'] is True
