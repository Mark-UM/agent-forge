"""modules.prompt.contracts — typed prompt assembly contracts (Round 2 Phase 1).

Replaces loose Dicts in composer.py with typed dataclasses. The composer
still emits the final Markdown string; these contracts describe the inputs
and intermediate shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PromptLayer(str, Enum):
    """Prompt assembly layers, highest to lowest priority.

    Lower-priority content MUST NOT override higher-priority content.
    """

    BASE = 'base'            # constitutional invariants (always loaded)
    TASK = 'task'            # current task workflow
    CONTEXT = 'context'      # project / framework / language
    PROFILE = 'profile'      # output style (terse / detailed / socratic)
    EXAMPLE = 'example'      # few-shot
    EXTRA = 'extra'          # local user profile (markconfig/profile.md)


class ContextTriggerType(str, Enum):
    """How a context layer is activated. Must be a real signal."""

    PACKAGE_JSON_DEP = 'package_json_dep'      # e.g. "three" in dependencies
    PYTHON_REQUIREMENT = 'python_requirement'  # e.g. "fastapi" in requirements.txt
    IMPORT_STATEMENT = 'import_statement'      # e.g. import three
    PROJECT_MARKER = 'project_marker'          # e.g. .tower-stack file
    FILE_PATH = 'file_path'                    # e.g. src/game/Tower*
    CONFIG_FILE = 'config_file'                # e.g. tsconfig.json present
    ALWAYS_ON = 'always_on'                    # e.g. anti-patterns for any code


@dataclass
class ContextSignal:
    """A detected context signal, written to
    `_runtime/prompt/context-signals.json` at session start.

    Composer reads the signals file to decide which contexts to load.
    """

    context_name: str                    # e.g. 'threejs-game-loop'
    trigger_type: ContextTriggerType
    trigger_value: str                   # e.g. 'three' (the dep name)
    confidence: float = 1.0              # 0.0–1.0; marker/file_path = 1.0
    detected_at: str = ''
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'context_name': self.context_name,
            'trigger_type': self.trigger_type.value,
            'trigger_value': self.trigger_value,
            'confidence': self.confidence,
            'detected_at': self.detected_at,
            'details': self.details,
        }


@dataclass
class PromptCompositionRequest:
    """Inputs to the composer."""

    task: Optional[str] = None           # e.g. 'coding', 'review'
    profile: str = 'default'             # default|terse|detailed|socratic
    contexts: list[str] = field(default_factory=list)  # explicit override
    signals: list[ContextSignal] = field(default_factory=list)
    extra_instructions: Optional[str] = None
    project_root: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'task': self.task,
            'profile': self.profile,
            'contexts': self.contexts,
            'signals': [s.to_dict() for s in self.signals],
            'extra_instructions': self.extra_instructions,
            'project_root': self.project_root,
        }


@dataclass
class PromptCompositionResult:
    """Output of the composer.

    `composed_prompt` is the final Markdown string. `layers_used` records
    which layers contributed (for the priority header). `signals_detected`
    records which ContextSignals fired (for observability).
    """

    composed_prompt: str
    layers_used: list[PromptLayer] = field(default_factory=list)
    contexts_loaded: list[str] = field(default_factory=list)
    signals_detected: list[ContextSignal] = field(default_factory=list)
    priority_header: str = ''
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'composed_prompt': self.composed_prompt,
            'layers_used': [l.value for l in self.layers_used],
            'contexts_loaded': self.contexts_loaded,
            'signals_detected': [s.to_dict() for s in self.signals_detected],
            'priority_header': self.priority_header,
            'warnings': self.warnings,
        }


# ── Context trigger definitions ──────────────────────────────

# Static registry of known context → trigger rules.
# Project Context Detector (modules/prompt/context.py) reads this to know
# what signals to look for.
CONTEXT_TRIGGERS: dict[str, list[dict[str, Any]]] = {
    'threejs-game-loop': [
        {
            'trigger_type': ContextTriggerType.PACKAGE_JSON_DEP,
            'trigger_value': 'three',
            'confidence': 0.95,
        },
        {
            'trigger_type': ContextTriggerType.IMPORT_STATEMENT,
            'trigger_value': 'from "three"',
            'confidence': 0.9,
        },
        {
            'trigger_type': ContextTriggerType.IMPORT_STATEMENT,
            'trigger_value': "from 'three'",
            'confidence': 0.9,
        },
    ],
    'tower-stack-project': [
        {
            'trigger_type': ContextTriggerType.PROJECT_MARKER,
            'trigger_value': '.tower-stack',
            'confidence': 1.0,
        },
        {
            'trigger_type': ContextTriggerType.PACKAGE_JSON_DEP,
            'trigger_value': 'three',
            'confidence': 0.5,  # necessary but not sufficient
        },
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': 'src/game/Tower',
            'confidence': 0.9,
        },
    ],
    'python': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.py',
            'confidence': 1.0,
        },
    ],
    'typescript': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.ts',
            'confidence': 0.95,
        },
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.tsx',
            'confidence': 0.95,
        },
    ],
    'haskell': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.hs',
            'confidence': 1.0,
        },
    ],
    'java': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.java',
            'confidence': 1.0,
        },
    ],
    'cpp': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.cpp',
            'confidence': 1.0,
        },
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '.h',
            'confidence': 0.7,  # .h could be C
        },
    ],
    'secrets': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': 'markconfig/',
            'confidence': 1.0,
        },
    ],
    'memory': [
        {
            'trigger_type': ContextTriggerType.FILE_PATH,
            'trigger_value': '_data/memory/',
            'confidence': 1.0,
        },
    ],
    'long-session': [
        {
            'trigger_type': ContextTriggerType.ALWAYS_ON,
            'trigger_value': 'turn_count>=50',
            'confidence': 1.0,
        },
    ],
    'anti-patterns': [
        {
            'trigger_type': ContextTriggerType.ALWAYS_ON,
            'trigger_value': 'any_code_language',
            'confidence': 1.0,
        },
    ],
}


def get_context_triggers(context_name: str) -> list[dict[str, Any]]:
    """Return the trigger rules for a context, or empty list if unknown."""
    return CONTEXT_TRIGGERS.get(context_name, [])
