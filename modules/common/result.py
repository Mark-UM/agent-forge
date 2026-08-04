"""modules.common.result — operation result primitives (Round 2 Phase 1).

Replaces ad-hoc `{"enabled": true}` step-success markers with a typed
OperationResult carrying status, errors, warnings, and metadata.

StepStatus is the single source of truth for step / pipeline state. It has
9 states per the Round 2 directive:

    PENDING       — not yet started
    RUNNING       — in progress
    SUCCEEDED     — completed successfully
    DEGRADED      — completed with quality loss (e.g. fallback path used)
    SKIPPED       — intentionally not executed
    UNSUPPORTED   — capability not implemented in this build
    FAILED        — attempted and failed
    TIMED_OUT     — exceeded deadline
    ABANDONED     — cancelled / superseded before completion

Forbidden patterns (enforced by tests):
    {"enabled": True}                      → use StepStatus.SUCCEEDED
    {"ok": 1}                              → use StepStatus.SUCCEEDED
    returning True/None to signal success → use OperationResult(success=True)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
    """Canonical step / pipeline status. 9 states per Round 2 contract."""

    PENDING = 'pending'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    DEGRADED = 'degraded'
    SKIPPED = 'skipped'
    UNSUPPORTED = 'unsupported'
    FAILED = 'failed'
    TIMED_OUT = 'timed_out'
    ABANDONED = 'abandoned'

    @classmethod
    def is_terminal(cls, status: 'StepStatus') -> bool:
        return status in (
            cls.SUCCEEDED, cls.DEGRADED, cls.SKIPPED, cls.UNSUPPORTED,
            cls.FAILED, cls.TIMED_OUT, cls.ABANDONED,
        )

    @classmethod
    def is_success_like(cls, status: 'StepStatus') -> bool:
        """SUCCEEDED and DEGRADED both indicate the step produced output."""
        return status in (cls.SUCCEEDED, cls.DEGRADED)


@dataclass
class ErrorInfo:
    """Structured error carried inside OperationResult.errors."""

    code: str
    message: str
    details: Optional[dict[str, Any]] = None
    exception_type: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code,
            'message': self.message,
            'details': self.details or {},
            'exception_type': self.exception_type,
            'timestamp': self.timestamp,
        }

    @classmethod
    def from_exception(cls, exc: BaseException, code: str = 'exception',
                       message: Optional[str] = None) -> 'ErrorInfo':
        return cls(
            code=code,
            message=message or str(exc) or exc.__class__.__name__,
            exception_type=exc.__class__.__name__,
        )


@dataclass
class WarningInfo:
    """Non-fatal issue carried inside OperationResult.warnings."""

    code: str
    message: str
    details: Optional[dict[str, Any]] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code,
            'message': self.message,
            'details': self.details or {},
            'timestamp': self.timestamp,
        }


@dataclass
class OperationResult:
    """Typed result for any operation / step / pipeline.

    Replaces loose `{"enabled": true}` dicts. `success` is the canonical
    success flag; `status` carries the granular StepStatus. Callers MUST
    inspect `success` (or `status.is_success_like()`) and MUST NOT infer
    success from the absence of errors.

    Attributes:
        success: True iff the operation produced usable output.
        status: Granular StepStatus (defaults SUCCEEDED when success=True).
        data: Operation payload (typed by caller).
        errors: List of ErrorInfo; non-empty implies success=False.
        warnings: Non-fatal issues; does not affect success.
        metadata: Step timing, provider name, etc.
    """

    success: bool
    status: StepStatus = StepStatus.SUCCEEDED
    data: Any = None
    errors: list[ErrorInfo] = field(default_factory=list)
    warnings: list[WarningInfo] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Invariant: errors present ⇒ success=False
        if self.errors and self.success:
            object.__setattr__(self, 'success', False)
            if self.status == StepStatus.SUCCEEDED:
                object.__setattr__(self, 'status', StepStatus.FAILED)
        # Invariant: success=True ⇒ status is success-like
        if self.success and not StepStatus.is_success_like(self.status):
            object.__setattr__(self, 'status', StepStatus.SUCCEEDED)

    def add_error(self, error: ErrorInfo) -> None:
        self.errors.append(error)
        self.success = False
        if StepStatus.is_success_like(self.status):
            self.status = StepStatus.FAILED

    def add_warning(self, warning: WarningInfo) -> None:
        self.warnings.append(warning)

    def to_dict(self) -> dict[str, Any]:
        return {
            'success': self.success,
            'status': self.status.value,
            'data': self.data,
            'errors': [e.to_dict() for e in self.errors],
            'warnings': [w.to_dict() for w in self.warnings],
            'metadata': self.metadata,
        }

    @classmethod
    def success_with(cls, data: Any = None, **metadata: Any) -> 'OperationResult':
        return cls(success=True, status=StepStatus.SUCCEEDED, data=data,
                   metadata=dict(metadata))

    @classmethod
    def degraded(cls, data: Any = None, **metadata: Any) -> 'OperationResult':
        return cls(success=True, status=StepStatus.DEGRADED, data=data,
                   metadata=dict(metadata))

    @classmethod
    def skipped(cls, reason: str = '', **metadata: Any) -> 'OperationResult':
        return cls(success=False, status=StepStatus.SKIPPED,
                   metadata={'reason': reason, **metadata})

    @classmethod
    def unsupported(cls, reason: str = '', **metadata: Any) -> 'OperationResult':
        return cls(success=False, status=StepStatus.UNSUPPORTED,
                   metadata={'reason': reason, **metadata})

    @classmethod
    def failed(cls, code: str = 'failed', message: str = '',
               details: Optional[dict[str, Any]] = None,
               exception: Optional[BaseException] = None,
               **metadata: Any) -> 'OperationResult':
        err = ErrorInfo(
            code=code,
            message=message or (str(exception) if exception else 'failed'),
            details=details,
            exception_type=exception.__class__.__name__ if exception else None,
        )
        return cls(success=False, status=StepStatus.FAILED,
                   errors=[err], metadata=dict(metadata))

    @classmethod
    def timed_out(cls, message: str = 'deadline exceeded',
                  **metadata: Any) -> 'OperationResult':
        err = ErrorInfo(code='timed_out', message=message)
        return cls(success=False, status=StepStatus.TIMED_OUT,
                   errors=[err], metadata=dict(metadata))
