"""modules.common — shared domain primitives.

Cross-module building blocks used by Search, Scheduler, Prompt, and other
domains.  Importing the package installs the backward-compatible hardened Run
status reducer before callers import ``modules.common.run`` symbols.
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
from modules.common.run_reduction import derive_run_status, install_run_reduction

install_run_reduction()

__all__ = [
    "errors",
    "result",
    "time_utils",
    "AgentForgeError",
    "ConfigurationError",
    "MigrationError",
    "ModelError",
    "ParseError",
    "StorageError",
    "TimeoutError",
    "UnsupportedOperationError",
    "ValidationError",
    "ErrorInfo",
    "OperationResult",
    "StepStatus",
    "WarningInfo",
    "now_utc_iso",
    "parse_iso_with_tz",
    "to_utc_iso",
    "derive_run_status",
]
