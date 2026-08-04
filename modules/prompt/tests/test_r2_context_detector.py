"""Contract tests for R2-3.2 Project Context Detector.

Verifies:
    * Tower Stack project detection via marker file / package.json name / path
    * Three.js context detection via package.json deps / import patterns
    * Python requirements detection (pytest, chromadb, apscheduler, etc.)
    * `_runtime/prompt/context-signals.json` is written with the right shape
    * detect_full_context_signals augments base signals with project_contexts
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.prompt.context import (
    CONTEXT_SIGNALS_PATH, THREEJS_DEPS, TOWER_STACK_MARKER_FILES,
    TOWER_STACK_PACKAGE_NAMES, detect_full_context_signals,
    detect_project_contexts, detect_python_requirements_context,
    detect_threejs_context, detect_tower_stack_project, write_context_signals,
)


# ── Tower Stack detection ────────────────────────────────────

class TestTowerStackDetection:
    def test_marker_file_triggers(self, tmp_path: Path):
        """R2-3.2: `.tower-stack` marker file triggers tower-stack-project."""
        (tmp_path / '.tower-stack').write_text('marker')
        assert detect_tower_stack_project(project_root=tmp_path) is True

    def test_package_json_name_triggers(self, tmp_path: Path):
        """R2-3.2: package.json name = tower-stack-3d triggers context."""
        (tmp_path / 'package.json').write_text(
            json.dumps({'name': 'tower-stack-3d', 'version': '1.0.0'}))
        assert detect_tower_stack_project(project_root=tmp_path) is True

    def test_package_json_alt_name_triggers(self, tmp_path: Path):
        (tmp_path / 'package.json').write_text(
            json.dumps({'name': 'tower-stack'}))
        assert detect_tower_stack_project(project_root=tmp_path) is True

    def test_tower_path_pattern_triggers(self, tmp_path: Path):
        """R2-3.2: src/game/Tower* path triggers context."""
        # No marker, no package.json — only path pattern
        file_paths = [str(tmp_path / 'src' / 'game' / 'TowerStack.ts')]
        assert detect_tower_stack_project(project_root=tmp_path,
                                          file_paths=file_paths) is True

    def test_no_triggers_returns_false(self, tmp_path: Path):
        """R2-3.2: when no marker / name / path matches, returns False."""
        (tmp_path / 'package.json').write_text(
            json.dumps({'name': 'unrelated-app'}))
        assert detect_tower_stack_project(project_root=tmp_path) is False

    def test_tower_stack_package_names_constant(self):
        """R2-3.2: TOWER_STACK_PACKAGE_NAMES must contain tower-stack-3d."""
        assert 'tower-stack-3d' in TOWER_STACK_PACKAGE_NAMES


# ── Three.js detection ───────────────────────────────────────

class TestThreejsDetection:
    def test_package_json_three_dep_triggers(self, tmp_path: Path):
        """R2-3.2: 'three' in dependencies triggers threejs-game-loop."""
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'my-game',
            'dependencies': {'three': '^0.160.0'},
        }))
        assert detect_threejs_context(project_root=tmp_path) is True

    def test_package_json_react_three_fiber_triggers(self, tmp_path: Path):
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'app',
            'dependencies': {'@react-three/fiber': '^8.0.0'},
        }))
        assert detect_threejs_context(project_root=tmp_path) is True

    def test_dev_dependency_triggers(self, tmp_path: Path):
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'app',
            'devDependencies': {'@types/three': '^0.160.0'},
        }))
        assert detect_threejs_context(project_root=tmp_path) is True

    def test_import_pattern_triggers(self, tmp_path: Path):
        """R2-3.2: import from 'three' in a source file triggers context."""
        src = tmp_path / 'App.tsx'
        src.write_text("import * as THREE from 'three';\nexport default THREE;")
        assert detect_threejs_context(project_root=tmp_path,
                                      file_paths=[src]) is True

    def test_no_three_dep_returns_false(self, tmp_path: Path):
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'app',
            'dependencies': {'react': '^18.0.0'},
        }))
        assert detect_threejs_context(project_root=tmp_path) is False

    def test_threejs_deps_constant_includes_three(self):
        assert 'three' in THREEJS_DEPS
        assert '@react-three/fiber' in THREEJS_DEPS


# ── Python requirements detection ────────────────────────────

class TestPythonRequirementsDetection:
    def test_pytest_detected(self, tmp_path: Path):
        (tmp_path / 'requirements.txt').write_text('pytest>=7.0\n')
        result = detect_python_requirements_context(project_root=tmp_path)
        assert 'pytest' in result

    def test_chromadb_detected(self, tmp_path: Path):
        (tmp_path / 'requirements.lock.txt').write_text('chromadb==0.4.0\n')
        result = detect_python_requirements_context(project_root=tmp_path)
        assert 'chromadb' in result

    def test_apscheduler_detected(self, tmp_path: Path):
        (tmp_path / 'requirements.txt').write_text('apscheduler==3.10.4\n')
        result = detect_python_requirements_context(project_root=tmp_path)
        assert 'apscheduler' in result

    def test_empty_returns_empty(self, tmp_path: Path):
        assert detect_python_requirements_context(project_root=tmp_path) == []

    def test_no_false_positive_for_pytest_in_comment(self, tmp_path: Path):
        """A comment line containing only pytest_plugins (with underscore)
        should NOT match as pytest. The regex requires pytest to be followed
        by [=<>.~!] or whitespace/EOL, not by '_'."""
        # Note: the underscore in pytest_plugins means it's a different token.
        # The regex word-boundary check ensures 'pytest_' doesn't match 'pytest'.
        (tmp_path / 'requirements.txt').write_text('# pytest_plugins documentation reference\n')
        result = detect_python_requirements_context(project_root=tmp_path)
        # pytest_ has '_' after pytest (a word char), so no match
        assert 'pytest' not in result, \
            f"pytest_ should not match as pytest; got {result}"


# ── Project contexts aggregation ─────────────────────────────

class TestProjectContextsAggregation:
    def test_both_contexts_returned_in_order(self, tmp_path: Path):
        """R2-3.2: tower-stack-project listed before threejs-game-loop."""
        (tmp_path / '.tower-stack').write_text('marker')
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'tower-stack-3d',
            'dependencies': {'three': '^0.160.0'},
        }))
        contexts = detect_project_contexts(project_root=tmp_path)
        assert 'tower-stack-project' in contexts
        assert 'threejs-game-loop' in contexts
        # Tower Stack is more specific → listed first
        assert contexts.index('tower-stack-project') < contexts.index('threejs-game-loop')

    def test_only_threejs_when_no_tower_marker(self, tmp_path: Path):
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'three-app',
            'dependencies': {'three': '^0.160.0'},
        }))
        contexts = detect_project_contexts(project_root=tmp_path)
        assert contexts == ['threejs-game-loop']


# ── context-signals.json writer ──────────────────────────────

class TestContextSignalsWriter:
    def test_write_creates_file(self, tmp_path: Path):
        out = tmp_path / 'context-signals.json'
        signals = {'project_contexts': ['threejs-game-loop'], 'has_threejs': True}
        result_path = write_context_signals(signals, path=out)
        assert result_path.exists()
        data = json.loads(out.read_text(encoding='utf-8'))
        assert data['version'] == 1
        assert data['signals'] == signals

    def test_write_is_atomic(self, tmp_path: Path):
        """R2-3.2: writes to .tmp then renames — no partial file on failure."""
        out = tmp_path / 'context-signals.json'
        signals = {'project_contexts': []}
        write_context_signals(signals, path=out)
        # No .tmp file should linger
        assert not (tmp_path / 'context-signals.json.tmp').exists()


# ── detect_full_context_signals integration ─────────────────

class TestFullContextSignals:
    def test_full_signals_include_project_contexts(self, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch):
        """R2-3.2: detect_full_context_signals augments base signals with
        project_contexts, has_threejs, has_tower_stack, python_packages."""
        (tmp_path / 'package.json').write_text(json.dumps({
            'name': 'three-game',
            'dependencies': {'three': '^0.160.0'},
        }))
        (tmp_path / 'requirements.txt').write_text('pytest>=7\n')

        # Redirect CONTEXT_SIGNALS_PATH write to tmp
        out = tmp_path / 'context-signals.json'
        monkeypatch.setattr('modules.prompt.context.CONTEXT_SIGNALS_PATH', out)

        signals = detect_full_context_signals(
            message='write code', file_paths=[], project_root=tmp_path)
        assert 'project_contexts' in signals
        assert 'threejs-game-loop' in signals['project_contexts']
        assert signals['has_threejs'] is True
        assert signals['has_tower_stack'] is False
        assert 'pytest' in signals['python_packages']
        # File written
        assert out.exists()
        data = json.loads(out.read_text(encoding='utf-8'))
        assert data['signals']['has_threejs'] is True


# ── R2-3.1: Base prompt no longer conflicts ──────────────────

class TestBasePromptSlimmed:
    """R2-3.1: base.md no longer simultaneously requires 'no questioning'
    and 'wait for confirmation'. Both moved to task prompts."""

    def test_base_no_longer_has_top_priority_directive(self):
        base = Path(__file__).resolve().parent.parent.parent.parent / \
               '.opencode' / 'prompts' / 'base.md'
        content = base.read_text(encoding='utf-8')
        # The "no questioning" directive must be removed
        assert 'Top Priority Directive' not in content
        assert 'No delays, no questioning' not in content
        assert 'execute X directly' not in content

    def test_base_no_longer_has_wait_for_confirmation_rule(self):
        base = Path(__file__).resolve().parent.parent.parent.parent / \
               '.opencode' / 'prompts' / 'base.md'
        content = base.read_text(encoding='utf-8')
        # Rule 3 "Plan and WAIT for explicit confirmation" must be removed
        assert 'Plan and WAIT for explicit confirmation' not in content
        assert 'Rule 3' not in content

    def test_base_keeps_system_invariants(self):
        """R2-3.1: base keeps Response Style, Vision Iron Rule, Web Search
        Permission, Context Management, Repository Paths."""
        base = Path(__file__).resolve().parent.parent.parent.parent / \
               '.opencode' / 'prompts' / 'base.md'
        content = base.read_text(encoding='utf-8')
        assert 'Response Style' in content
        assert 'Vision and PDF Recognition' in content
        assert 'Web Search Permission' in content
        assert 'Repository Paths' in content
