"""modules.common.run — unified Run/Step/Event execution model (Phase C).

Generalizes the step-level `OperationResult`/`StepStatus` (from result.py)
into a three-tier execution telemetry model:

    Run   — one top-level execution (a search, a scheduled job, a vision call)
      └─ Step   — a named stage within the run (validate, plan, execute, ...)
           └─ Event — an observable occurrence (started, cache_hit, degraded, ...)

This module is module-agnostic. SearchPipeline, Scheduler JobRun, and
SearchService all map onto it via `.to_run()` adapters or by recording through
`RunRecorder`. Existing typed contracts (SearchPipelineResult, JobRun) are NOT
replaced — they gain an optional adapter. No existing tests break.

Design invariants:
    * Run.status and Step.status use the existing StepStatus enum (9 states).
      RunStatus is a thin subset alias, not a competing taxonomy.
    * Every Step carries an OperationResult (the existing typed result).
    * Events are append-only; they never mutate Run/Step status.
    * RunRecorder is a context manager that produces a RunResult on exit.
    * All timestamps are UTC ISO 8601 (per R2-1.3 / R2-5.1).

Usage (recording a run):

    with RunRecorder(run_type='search', run_id='run-001') as rec:
        rec.event(EventType.RUN_STARTED)
        with rec.step('validate_request') as s:
            s.event(EventType.STEP_STARTED)
            # ... do work ...
            s.succeed(data={'normalized': 'q'})
        with rec.step('provider_execute') as s:
            s.event(EventType.STEP_STARTED)
            s.degrade(reason='no planner')
        rec.event(EventType.RUN_COMPLETED)
    run_result = rec.result  # RunResult

Usage (adapter — converting existing typed results):

    run = SearchPipelineResult(...).to_run(run_type='search')
    run = job_run.to_run(run_type='scheduler.job')
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from modules.common.result import (
    ErrorInfo, OperationResult, StepStatus, WarningInfo,
)


# ── Enums ───────────────────────────────────────────────────

class RunStatus(str):
    """Run-level status string. Uses the same values as StepStatus so that
    Run.status and Step.status share one taxonomy.

    Valid run statuses (a subset of StepStatus values):
        pending, running, succeeded, degraded, failed, timed_out, abandoned

    `skipped` and `unsupported` are step-level only; a Run that is entirely
    skipped should be `abandoned` with a metadata reason.
    """

    PENDING = 'pending'
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    DEGRADED = 'degraded'
    FAILED = 'failed'
    TIMED_OUT = 'timed_out'
    ABANDONED = 'abandoned'

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        return status in (
            cls.SUCCEEDED, cls.DEGRADED, cls.FAILED,
            cls.TIMED_OUT, cls.ABANDONED,
        )

    @classmethod
    def is_success_like(cls, status: str) -> bool:
        return status in (cls.SUCCEEDED, cls.DEGRADED)


class EventType(str):
    """Canonical event types emitted during a run.

    Custom events use EventType.CUSTOM with a `payload['custom_type']` field.
    """

    RUN_STARTED = 'run.started'
    RUN_COMPLETED = 'run.completed'
    RUN_FAILED = 'run.failed'

    STEP_STARTED = 'step.started'
    STEP_COMPLETED = 'step.completed'
    STEP_SKIPPED = 'step.skipped'
    STEP_FAILED = 'step.failed'

    CACHE_HIT = 'cache.hit'
    CACHE_MISS = 'cache.miss'
    CACHE_STORE = 'cache.store'

    PROVIDER_CALLED = 'provider.called'
    PROVIDER_RESULT = 'provider.result'
    PROVIDER_ERROR = 'provider.error'

    DEGRADED_ENTERED = 'degraded.entered'
    VERIFICATION_COMPLETED = 'verification.completed'

    WARNING = 'warning'
    ERROR = 'error'
    CUSTOM = 'custom'


class EventLevel(str):
    """Severity level for events."""

    DEBUG = 'debug'
    INFO = 'info'
    WARN = 'warn'
    ERROR = 'error'


# ── Event ───────────────────────────────────────────────────

@dataclass
class Event:
    """An observable occurrence during a run.

    Events are append-only telemetry. They do NOT mutate Run/Step status —
    status transitions are explicit method calls on RunRecorder/StepRecorder.
    """

    event_id: str
    run_id: str
    timestamp: str                         # ISO 8601 UTC
    type: str                              # EventType value or custom string
    level: str = EventLevel.INFO
    step_id: Optional[str] = None          # None = run-level event
    step_name: Optional[str] = None        # convenience for step-level events
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'event_id': self.event_id,
            'run_id': self.run_id,
            'timestamp': self.timestamp,
            'type': self.type,
            'level': self.level,
            'step_id': self.step_id,
            'step_name': self.step_name,
            'payload': self.payload,
        }


# ── Step ────────────────────────────────────────────────────

@dataclass
class Step:
    """A named stage within a Run.

    Each Step wraps an OperationResult (the existing typed result from
    result.py). The Step adds run linkage, ordering, and timing.
    """

    step_id: str
    run_id: str
    name: str                              # canonical step name (e.g. 'validate_request')
    order: int                             # 0-based execution order
    status: str = RunStatus.PENDING        # RunStatus value
    started_at: Optional[str] = None       # ISO 8601 UTC
    ended_at: Optional[str] = None
    duration_ms: int = 0
    result: OperationResult = field(
        default_factory=lambda: OperationResult(success=False,
                                                status=StepStatus.PENDING))

    def to_dict(self) -> dict[str, Any]:
        return {
            'step_id': self.step_id,
            'run_id': self.run_id,
            'name': self.name,
            'order': self.order,
            'status': self.status,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'duration_ms': self.duration_ms,
            'result': self.result.to_dict(),
        }


# ── Run ─────────────────────────────────────────────────────

@dataclass
class Run:
    """A top-level execution container.

    A Run owns Steps and Events. Its status is derived from the terminal
    states of its steps (if all succeed → SUCCEEDED; if any degrades →
    DEGRADED; if any fails → FAILED) unless explicitly set.

    Run is intentionally module-agnostic. SearchPipeline, Scheduler, and
    SearchService all produce Runs via adapters or RunRecorder.
    """

    run_id: str
    run_type: str                          # 'search' | 'scheduler.job' | 'vision' | ...
    status: str = RunStatus.PENDING
    started_at: Optional[str] = None       # ISO 8601 UTC
    ended_at: Optional[str] = None
    duration_ms: int = 0
    steps: list[Step] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """True iff status is success-like (SUCCEEDED or DEGRADED)."""
        return RunStatus.is_success_like(self.status)

    @property
    def degraded_mode(self) -> bool:
        """True iff any step is DEGRADED or status is DEGRADED."""
        return (self.status == RunStatus.DEGRADED
                or any(s.status == RunStatus.DEGRADED for s in self.steps))

    def step_by_name(self, name: str) -> Optional[Step]:
        """Return the first step with the given name, or None."""
        for s in self.steps:
            if s.name == name:
                return s
        return None

    def events_for_step(self, step_id: str) -> list[Event]:
        """Return all events associated with a step."""
        return [e for e in self.events if e.step_id == step_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'run_type': self.run_type,
            'status': self.status,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'duration_ms': self.duration_ms,
            'steps': [s.to_dict() for s in self.steps],
            'events': [e.to_dict() for e in self.events],
            'metadata': self.metadata,
            'error': self.error,
            'success': self.success,
            'degraded_mode': self.degraded_mode,
        }


# ── RunResult ───────────────────────────────────────────────

@dataclass
class RunResult:
    """Serialized final result of a run, produced by RunRecorder.

    This is the flat DTO returned to callers. It generalizes
    SearchPipelineResult and JobRun into a module-agnostic shape.
    """

    run_id: str
    run_type: str
    status: str
    success: bool
    degraded_mode: bool
    started_at: str
    ended_at: str
    duration_ms: int
    step_count: int
    event_count: int
    steps: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'run_type': self.run_type,
            'status': self.status,
            'success': self.success,
            'degraded_mode': self.degraded_mode,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'duration_ms': self.duration_ms,
            'step_count': self.step_count,
            'event_count': self.event_count,
            'steps': self.steps,
            'events': self.events,
            'metadata': self.metadata,
            'error': self.error,
        }

    @classmethod
    def from_run(cls, run: Run) -> 'RunResult':
        return cls(
            run_id=run.run_id,
            run_type=run.run_type,
            status=run.status,
            success=run.success,
            degraded_mode=run.degraded_mode,
            started_at=run.started_at or '',
            ended_at=run.ended_at or '',
            duration_ms=run.duration_ms,
            step_count=len(run.steps),
            event_count=len(run.events),
            steps=[s.to_dict() for s in run.steps],
            events=[e.to_dict() for e in run.events],
            metadata=dict(run.metadata),
            error=run.error,
        )


# ── Helpers ─────────────────────────────────────────────────

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── StepRecorder ────────────────────────────────────────────

class StepRecorder:
    """Records a single step within a RunRecorder context.

    Usage:

        with rec.step('validate_request') as s:
            # ... do work ...
            s.succeed(data={'normalized': q})

    On exit without an explicit terminal call, the step is auto-completed:
    SUCCESS if no exception, FAILED if an exception propagated.
    """

    def __init__(
        self,
        run_recorder: 'RunRecorder',
        name: str,
        order: int,
    ) -> None:
        self._recorder = run_recorder
        self._step = Step(
            step_id=_new_id('step'),
            run_id=run_recorder._run.run_id,
            name=name,
            order=order,
        )
        self._start_monotonic: float = 0.0
        self._explicit_terminal = False

    @property
    def step(self) -> Step:
        return self._step

    @property
    def step_id(self) -> str:
        return self._step.step_id

    # ── Event emission ─────────────────────────────────────

    def event(
        self,
        type: str,
        *,
        level: str = EventLevel.INFO,
        payload: Optional[dict[str, Any]] = None,
    ) -> Event:
        """Emit a step-level event."""
        return self._recorder._emit_event(
            type=type, level=level, payload=payload,
            step_id=self._step.step_id, step_name=self._step.name,
        )

    # ── Terminal transitions ───────────────────────────────

    def succeed(self, data: Any = None, **metadata: Any) -> None:
        """Mark this step SUCCEEDED with an OperationResult."""
        self._step.result = OperationResult.success_with(data=data, **metadata)
        self._step.status = RunStatus.SUCCEEDED
        self._explicit_terminal = True
        self._finish()

    def degrade(self, data: Any = None, reason: str = '', **metadata: Any) -> None:
        """Mark this step DEGRADED (completed with quality loss)."""
        meta = {'reason': reason, **metadata} if reason else metadata
        self._step.result = OperationResult.degraded(data=data, **meta)
        self._step.status = RunStatus.DEGRADED
        self._explicit_terminal = True
        self._finish()

    def skip(self, reason: str = '', **metadata: Any) -> None:
        """Mark this step SKIPPED."""
        self._step.result = OperationResult.skipped(reason=reason, **metadata)
        self._step.status = RunStatus.ABANDONED  # skipped step → abandoned run-level
        self._explicit_terminal = True
        self._finish()

    def fail(self, code: str = 'failed', message: str = '',
             exception: Optional[BaseException] = None,
             **metadata: Any) -> None:
        """Mark this step FAILED."""
        self._step.result = OperationResult.failed(
            code=code, message=message, exception=exception, **metadata)
        self._step.status = RunStatus.FAILED
        self._explicit_terminal = True
        self._finish()

    def time_out(self, message: str = 'deadline exceeded',
                 **metadata: Any) -> None:
        """Mark this step TIMED_OUT."""
        self._step.result = OperationResult.timed_out(message=message, **metadata)
        self._step.status = RunStatus.TIMED_OUT
        self._explicit_terminal = True
        self._finish()

    # ── Internal ───────────────────────────────────────────

    def _start(self) -> None:
        self._step.status = RunStatus.RUNNING
        self._step.started_at = _utc_now_iso()
        self._start_monotonic = time.monotonic()
        self.event(EventType.STEP_STARTED)

    def _finish(self) -> None:
        self._step.ended_at = _utc_now_iso()
        self._step.duration_ms = int(
            (time.monotonic() - self._start_monotonic) * 1000)
        # Emit completion event
        if self._step.status == RunStatus.SUCCEEDED:
            self.event(EventType.STEP_COMPLETED, payload={'status': self._step.status})
        elif self._step.status == RunStatus.FAILED:
            self.event(EventType.STEP_FAILED, payload={'status': self._step.status})
        elif self._step.status == RunStatus.ABANDONED:
            self.event(EventType.STEP_SKIPPED, payload={'status': self._step.status})
        self._recorder._run.steps.append(self._step)

    def _auto_complete(self, exc: Optional[BaseException]) -> None:
        """Called on context exit if no explicit terminal was set."""
        if self._explicit_terminal:
            return
        if exc is not None:
            self.fail(code='exception', message=str(exc) or exc.__class__.__name__,
                      exception=exc)
        else:
            self.succeed()


# ── RunRecorder ─────────────────────────────────────────────

class RunRecorder:
    """Records a Run and its Steps/Events.

    Used as a context manager:

        with RunRecorder(run_type='search') as rec:
            with rec.step('validate') as s:
                s.succeed()
        result = rec.result  # RunResult

    Events can be emitted at run level (rec.event) or step level (s.event).
    Optional listeners receive every event in real time.
    """

    def __init__(
        self,
        run_type: str,
        run_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        listeners: Optional[list[Callable[[Event], None]]] = None,
    ) -> None:
        self._run = Run(
            run_id=run_id or _new_id('run'),
            run_type=run_type,
            metadata=dict(metadata) if metadata else {},
        )
        self._listeners = list(listeners) if listeners else []
        self._step_counter = 0
        self._start_monotonic: float = 0.0
        self._result: Optional[RunResult] = None
        self._entered = False

    @property
    def run(self) -> Run:
        return self._run

    @property
    def run_id(self) -> str:
        return self._run.run_id

    @property
    def result(self) -> RunResult:
        """The RunResult. Available after the context exits."""
        if self._result is None:
            # Auto-build from current state (e.g. if not used as context manager)
            self._finalize(exc=None)
        assert self._result is not None
        return self._result

    # ── Event emission ─────────────────────────────────────

    def event(
        self,
        type: str,
        *,
        level: str = EventLevel.INFO,
        payload: Optional[dict[str, Any]] = None,
        step_id: Optional[str] = None,
        step_name: Optional[str] = None,
    ) -> Event:
        """Emit a run-level (or step-level) event."""
        return self._emit_event(
            type=type, level=level, payload=payload,
            step_id=step_id, step_name=step_name,
        )

    def _emit_event(
        self,
        type: str,
        level: str,
        payload: Optional[dict[str, Any]],
        step_id: Optional[str],
        step_name: Optional[str],
    ) -> Event:
        evt = Event(
            event_id=_new_id('evt'),
            run_id=self._run.run_id,
            timestamp=_utc_now_iso(),
            type=type,
            level=level,
            step_id=step_id,
            step_name=step_name,
            payload=payload or {},
        )
        self._run.events.append(evt)
        for listener in self._listeners:
            try:
                listener(evt)
            except Exception:
                pass  # listener errors are non-fatal
        return evt

    # ── Step recording ─────────────────────────────────────

    @contextmanager
    def step(self, name: str) -> Iterator[StepRecorder]:
        """Record a named step within this run.

        Yields a StepRecorder. On exit, the step is auto-completed if no
        explicit terminal method (succeed/degrade/fail/...) was called.
        """
        self._step_counter += 1
        sr = StepRecorder(self, name=name, order=self._step_counter - 1)
        sr._start()
        exc: Optional[BaseException] = None
        try:
            yield sr
        except BaseException as e:
            exc = e
            raise
        finally:
            sr._auto_complete(exc)

    def add_step_result(self, name: str, result: OperationResult,
                        *, order: Optional[int] = None,
                        started_at: Optional[str] = None,
                        ended_at: Optional[str] = None,
                        duration_ms: int = 0) -> Step:
        """Add a pre-computed step (e.g. from an existing SearchPipeline step_report).

        This is the adapter path for modules that already have OperationResult
        objects and want to surface them in a Run without re-executing.
        """
        self._step_counter += 1
        step = Step(
            step_id=_new_id('step'),
            run_id=self._run.run_id,
            name=name,
            order=order if order is not None else self._step_counter - 1,
            status=result.status.value if isinstance(result.status, StepStatus)
                   else str(result.status),
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            result=result,
        )
        self._run.steps.append(step)
        return step

    # ── Run-level terminal transitions ─────────────────────

    def succeed(self, metadata: Optional[dict[str, Any]] = None) -> None:
        """Explicitly mark the run SUCCEEDED. Normally auto-derived."""
        self._run.status = RunStatus.SUCCEEDED
        if metadata:
            self._run.metadata.update(metadata)
        self._explicit_run_terminal = True

    def fail(self, error: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Explicitly mark the run FAILED."""
        self._run.status = RunStatus.FAILED
        self._run.error = error
        if metadata:
            self._run.metadata.update(metadata)
        self._explicit_run_terminal = True

    # ── Context manager ────────────────────────────────────

    def __enter__(self) -> 'RunRecorder':
        self._entered = True
        self._explicit_run_terminal = False
        self._run.status = RunStatus.RUNNING
        self._run.started_at = _utc_now_iso()
        self._start_monotonic = time.monotonic()
        self.event(EventType.RUN_STARTED)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self._finalize(exc_val)
        # Don't suppress exceptions
        return False

    def _finalize(self, exc: Optional[BaseException]) -> None:
        if self._run.ended_at is not None:
            # Already finalized (e.g. double-call on result property)
            if self._result is None:
                self._result = RunResult.from_run(self._run)
            return

        self._run.ended_at = _utc_now_iso()
        if self._start_monotonic:
            self._run.duration_ms = int(
                (time.monotonic() - self._start_monotonic) * 1000)

        # Derive terminal status if not explicitly set
        if not getattr(self, '_explicit_run_terminal', False):
            if exc is not None:
                self._run.status = RunStatus.FAILED
                self._run.error = f"{exc.__class__.__name__}: {exc}"
            elif not self._run.steps:
                # No steps recorded — treat as succeeded (empty run)
                self._run.status = RunStatus.SUCCEEDED
            else:
                # Derive from steps: any FAILED → FAILED; any DEGRADED → DEGRADED;
                # else SUCCEEDED
                step_statuses = [s.status for s in self._run.steps]
                if RunStatus.FAILED in step_statuses:
                    self._run.status = RunStatus.FAILED
                elif RunStatus.TIMED_OUT in step_statuses:
                    self._run.status = RunStatus.TIMED_OUT
                elif RunStatus.DEGRADED in step_statuses:
                    self._run.status = RunStatus.DEGRADED
                else:
                    self._run.status = RunStatus.SUCCEEDED

        # Emit terminal event
        if self._run.status == RunStatus.FAILED:
            self.event(EventType.RUN_FAILED,
                       level=EventLevel.ERROR,
                       payload={'error': self._run.error or ''})
        else:
            self.event(EventType.RUN_COMPLETED,
                       payload={'status': self._run.status})

        self._result = RunResult.from_run(self._run)


# ── Adapter helpers ─────────────────────────────────────────

def run_from_step_reports(
    run_type: str,
    step_reports: dict[str, OperationResult],
    *,
    run_id: Optional[str] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    duration_ms: int = 0,
    degraded_mode: bool = False,
    metadata: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Run:
    """Build a Run from a dict of step_reports (e.g. SearchPipelineResult.step_reports).

    This is the primary adapter for existing pipelines that already produce
    OperationResult objects per step.
    """
    run = Run(
        run_id=run_id or _new_id('run'),
        run_type=run_type,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=duration_ms,
        metadata=dict(metadata) if metadata else {},
        error=error,
    )
    for order, (name, result) in enumerate(step_reports.items()):
        step = Step(
            step_id=_new_id('step'),
            run_id=run.run_id,
            name=name,
            order=order,
            status=result.status.value if isinstance(result.status, StepStatus)
                   else str(result.status),
            result=result,
        )
        run.steps.append(step)

    # Derive status
    step_statuses = [s.status for s in run.steps]
    if RunStatus.FAILED in step_statuses:
        run.status = RunStatus.FAILED
    elif RunStatus.TIMED_OUT in step_statuses:
        run.status = RunStatus.TIMED_OUT
    elif RunStatus.DEGRADED in step_statuses or degraded_mode:
        run.status = RunStatus.DEGRADED
    elif step_statuses:
        run.status = RunStatus.SUCCEEDED
    else:
        run.status = RunStatus.PENDING

    return run
