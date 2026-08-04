"""modules.common — shared domain primitives (Round 2 Phase 1).

Cross-module building blocks used by search, scheduler, prompt, and other
domains. Stdlib only; no third-party dependencies.

Public surface:
    result    — OperationResult, ErrorInfo, WarningInfo, StepStatus
    errors    — typed exception hierarchy
    time_utils — UTC normalization helpers
"""
from modules.common import errors, result, time_utils  # noqa: F401
from modules.common.errors import (
    AgentForgeError,
    ConfigurationError,
    MigrationError,
    ModelError,
    ParseError,
    StorageError,
    TimeoutError,
    UnsupportedOperationError,
    ValidationError,
)
from modules.common.result import (
    ErrorInfo,
    OperationResult,
    StepStatus,
    WarningInfo,
)
from modules.common.time_utils import now_utc_iso, parse_iso_with_tz, to_utc_iso

__all__ = [
    'errors', 'result', 'time_utils',
    'AgentForgeError', 'ConfigurationError', 'MigrationError', 'ModelError',
    'ParseError', 'StorageError', 'TimeoutError', 'UnsupportedOperationError',
    'ValidationError',
    'ErrorInfo', 'OperationResult', 'StepStatus', 'WarningInfo',
    'now_utc_iso', 'parse_iso_with_tz', 'to_utc_iso',
]
