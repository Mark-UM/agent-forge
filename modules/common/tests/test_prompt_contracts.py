"""Contract tests for modules.prompt.contracts (Round 2 Phase 1)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from modules.prompt.contracts import (
    CONTEXT_TRIGGERS, ContextSignal, ContextTriggerType,
    PromptCompositionRequest, PromptCompositionResult, PromptLayer,
    get_context_triggers,
)


# ── PromptLayer ──────────────────────────────────────────────

class TestPromptLayer:
    def test_six_layers_present(self):
        expected = {'base', 'task', 'context', 'profile', 'example', 'extra'}
        actual = {l.value for l in PromptLayer}
        assert actual == expected


# ── ContextSignal ────────────────────────────────────────────

class TestContextSignal:
    def test_signal_carries_trigger_type(self):
        sig = ContextSignal(
            context_name='threejs-game-loop',
            trigger_type=ContextTriggerType.PACKAGE_JSON_DEP,
            trigger_value='three',
            confidence=0.95,
        )
        d = sig.to_dict()
        assert d['context_name'] == 'threejs-game-loop'
        assert d['trigger_type'] == 'package_json_dep'
        assert d['trigger_value'] == 'three'
        assert d['confidence'] == 0.95

    def test_real_signals_only(self):
        """R2-3.2: triggers must be real signals, not 'any code language'."""
        # All trigger types in the enum are real signals
        for trigger in ContextTriggerType:
            assert trigger.value in {
                'package_json_dep', 'python_requirement', 'import_statement',
                'project_marker', 'file_path', 'config_file', 'always_on',
            }


# ── Context trigger registry ─────────────────────────────────

class TestContextTriggers:
    def test_threejs_game_loop_triggers_defined(self):
        """R2-3.2: threejs-game-loop must be detectable via real signals."""
        triggers = get_context_triggers('threejs-game-loop')
        assert len(triggers) >= 1
        trigger_types = {t['trigger_type'] for t in triggers}
        assert ContextTriggerType.PACKAGE_JSON_DEP in trigger_types

    def test_tower_stack_project_triggers_defined(self):
        """R2-3.2: tower-stack-project must be detectable via real signals."""
        triggers = get_context_triggers('tower-stack-project')
        assert len(triggers) >= 1
        # Must include project_marker (highest confidence)
        marker_triggers = [t for t in triggers
                           if t['trigger_type'] == ContextTriggerType.PROJECT_MARKER]
        assert len(marker_triggers) >= 1
        assert marker_triggers[0]['confidence'] == 1.0

    def test_unknown_context_returns_empty(self):
        assert get_context_triggers('nonexistent-context') == []

    def test_language_contexts_use_file_path_signals(self):
        """Language contexts must trigger on file paths, not language names."""
        for lang in ['python', 'typescript', 'haskell', 'java', 'cpp']:
            triggers = get_context_triggers(lang)
            assert len(triggers) >= 1
            for t in triggers:
                assert t['trigger_type'] == ContextTriggerType.FILE_PATH


# ── PromptCompositionRequest / Result ────────────────────────

class TestPromptCompositionShapes:
    def test_request_defaults(self):
        req = PromptCompositionRequest()
        assert req.profile == 'default'
        assert req.contexts == []
        assert req.signals == []
        assert req.task is None

    def test_request_with_signals(self):
        sig = ContextSignal(
            context_name='python',
            trigger_type=ContextTriggerType.FILE_PATH,
            trigger_value='.py',
        )
        req = PromptCompositionRequest(task='coding', signals=[sig])
        d = req.to_dict()
        assert d['task'] == 'coding'
        assert len(d['signals']) == 1
        assert d['signals'][0]['context_name'] == 'python'

    def test_result_carries_layers_used(self):
        result = PromptCompositionResult(
            composed_prompt='# Prompt\n...',
            layers_used=[PromptLayer.BASE, PromptLayer.TASK, PromptLayer.CONTEXT],
            contexts_loaded=['python', 'anti-patterns'],
        )
        d = result.to_dict()
        assert d['composed_prompt'].startswith('# Prompt')
        assert 'base' in d['layers_used']
        assert 'python' in d['contexts_loaded']
