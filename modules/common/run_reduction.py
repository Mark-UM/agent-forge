"""Deterministic Run status reduction and compatibility installation.

The original Run/Step/Event model predates a complete state table.  This module
provides one pure reducer and installs it into the existing recorder/adapter
without changing their public constructors or serialized shapes.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from modules.common.result import OperationResult, StepStatus
from modules.common import run as run_module


_SUCCESS = run_module.RunStatus.SUCCEEDED
_DEGRADED = run_module.RunStatus.DEGRADED
_FAILED = run_module.RunStatus.FAILED
_TIMED_OUT = run_module.RunStatus.TIMED_OUT
_ABANDONED = run_module.RunStatus.ABANDONED
_RUNNING = run_module.RunStatus.RUNNING
_PENDING = run_module.RunStatus.PENDING

_NON_EXECUTED = frozenset(
    {
        StepStatus.SKIPPED.value,
        StepStatus.UNSUPPORTED.value,
        StepStatus.ABANDONED.value,
        _ABANDONED,
    }
)


def _normalise_status(value) -> str:  # noqa: ANN001
    return value.value if hasattr(value, "value") else str(value)


def derive_run_status(
    statuses: Iterable[str],
    *,
    empty_status: str = _SUCCESS,
    force_degraded: bool = False,
) -> str:
    """Reduce step statuses into a single deterministic Run status.

    Precedence is failure > timeout > active > all-not-executed > degraded >
    mixed execution > success.  Empty runs remain successful for backward
    compatibility with the established recorder contract.
    """

    values = [_normalise_status(value) for value in statuses]
    if not values:
        return empty_status
    if _FAILED in values:
        return _FAILED
    if _TIMED_OUT in values:
        return _TIMED_OUT
    if _RUNNING in values or _PENDING in values:
        return _RUNNING

    non_executed = [value for value in values if value in _NON_EXECUTED]
    if len(non_executed) == len(values):
        return _ABANDONED

    if _DEGRADED in values or force_degraded:
        return _DEGRADED

    # A run that produced useful output but skipped/abandoned another step is
    # partial, not fully successful.
    if non_executed:
        return _DEGRADED
    return _SUCCESS


def _finalize(self, exc: Optional[BaseException]) -> None:  # noqa: ANN001
    if self._run.ended_at is not None:
        if self._result is None:
            self._result = run_module.RunResult.from_run(self._run)
        return

    self._run.ended_at = run_module._utc_now_iso()
    if self._start_monotonic:
        self._run.duration_ms = int(
            (time.monotonic() - self._start_monotonic) * 1000
        )

    if not getattr(self, "_explicit_run_terminal", False):
        if exc is not None:
            self._run.status = _FAILED
            self._run.error = f"{exc.__class__.__name__}: {exc}"
        else:
            self._run.status = derive_run_status(
                [step.status for step in self._run.steps]
            )
            if self._run.status == _ABANDONED:
                self._run.metadata.setdefault(
                    "terminal_reason", "all steps were skipped, unsupported, or abandoned"
                )

    if self._run.status == _FAILED:
        self.event(
            run_module.EventType.RUN_FAILED,
            level=run_module.EventLevel.ERROR,
            payload={"error": self._run.error or ""},
        )
    else:
        self.event(
            run_module.EventType.RUN_COMPLETED,
            payload={"status": self._run.status},
        )
    self._result = run_module.RunResult.from_run(self._run)


def run_from_step_reports(
    run_type: str,
    step_reports: dict[str, OperationResult],
    *,
    run_id: Optional[str] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    duration_ms: int = 0,
    degraded_mode: bool = False,
    metadata: Optional[dict] = None,
    error: Optional[str] = None,
):
    run = run_module.Run(
        run_id=run_id or run_module._new_id("run"),
        run_type=run_type,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        metadata=dict(metadata) if metadata else {},
        error=error,
    )
    for order, (name, result) in enumerate(step_reports.items()):
        step = run_module.Step(
            step_id=run_module._new_id("step"),
            run_id=run.run_id,
            name=name,
            order=order,
            status=_normalise_status(result.status),
            result=result,
        )
        run.steps.append(step)

    run.status = derive_run_status(
        [step.status for step in run.steps],
        empty_status=_PENDING,
        force_degraded=degraded_mode,
    )
    if run.status == _ABANDONED:
        run.metadata.setdefault(
            "terminal_reason", "all steps were skipped, unsupported, or abandoned"
        )
    return run


def install_run_reduction() -> None:
    """Install the reducer into the existing public module exactly once."""

    if getattr(run_module, "_HARDENED_REDUCTION_INSTALLED", False):
        return
    run_module.RunRecorder._finalize = _finalize
    run_module.derive_run_status = derive_run_status
    run_module.run_from_step_reports = run_from_step_reports
    run_module._HARDENED_REDUCTION_INSTALLED = True


__all__ = ["derive_run_status", "install_run_reduction", "run_from_step_reports"]
