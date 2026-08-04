"""modules.scheduler.contracts — typed scheduler domain (Round 2 Phase 1).

Separates ActionItem (what should happen) from ScheduledJob (when it runs)
from JobRun (one execution). Round 1 conflated these into a single table;
Round 2 keeps three SQLite tables:

    action_items    — user-facing to-do items extracted from chat
    scheduled_jobs  — APScheduler job config (trigger, timezone, coalesce)
    job_runs        — one row per execution attempt

Trigger whitelist: only `cron` is currently supported. `date` and `interval`
return UNSUPPORTED_TRIGGER until APScheduler wiring lands for them.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules.common.result import OperationResult, StepStatus
from modules.common.run import Run, RunStatus


# ── Enums ────────────────────────────────────────────────────

class TriggerType(str, Enum):
    """APScheduler trigger types. Only CRON is currently supported."""

    CRON = 'cron'
    DATE = 'date'         # one-shot at specific datetime — UNSUPPORTED
    INTERVAL = 'interval'  # fixed period — UNSUPPORTED

    @classmethod
    def supported(cls) -> list['TriggerType']:
        """Trigger types actually wired into APScheduler."""
        return [cls.CRON]


class ActionItemStatus(str, Enum):
    PENDING = 'pending'
    SCHEDULED = 'scheduled'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    ABANDONED = 'abandoned'


class JobRunStatus(str, Enum):
    QUEUED = 'queued'         # manual trigger accepted, not yet started
    RUNNING = 'running'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    TIMED_OUT = 'timed_out'
    ABANDONED = 'abandoned'


class MigrationOutcome(str, Enum):
    """Per-row outcome of a jobs.json → SQLite migration."""

    MIGRATED = 'migrated'
    QUARANTINED = 'quarantined'  # unmapped / unconvertible
    SKIPPED = 'skipped'          # already migrated (idempotent)


# ── Trigger Spec ─────────────────────────────────────────────

@dataclass
class TriggerSpec:
    """Typed trigger configuration. Replaces loose dict.

    Only `cron` is supported in Round 2. Attempting to build a `date` or
    `interval` trigger raises UnsupportedOperationError at the scheduler
    layer (not here — this dataclass is just a value object).
    """

    type: TriggerType = TriggerType.CRON
    cron_expression: str = ''       # e.g. '0 9 * * 1-5'
    timezone: str = 'UTC'
    # APScheduler passthrough fields (R2-4.3)
    max_instances: int = 1
    misfire_grace_time: int = 60    # seconds
    coalesce: bool = True
    # date trigger fields (UNSUPPORTED — present for forward compat)
    run_date: Optional[datetime] = None
    # interval trigger fields (UNSUPPORTED — present for forward compat)
    interval_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        if self.type == TriggerType.CRON and not self.cron_expression:
            raise ValueError('cron trigger requires cron_expression')
        if self.type not in TriggerType.supported():
            # Allowed to construct (for forward compat) but flagged.
            pass

    def to_apscheduler_kwargs(self) -> dict[str, Any]:
        """Build kwargs for APScheduler add_job(trigger=...).

        Only call when self.type is in TriggerType.supported().
        """
        if self.type == TriggerType.CRON:
            # APScheduler CronTrigger.from_crontab accepts standard 5-field cron.
            parts = self.cron_expression.split()
            if len(parts) != 5:
                raise ValueError(
                    f'cron_expression must be 5 fields, got {len(parts)}: '
                    f'{self.cron_expression!r}'
                )
            return {
                'trigger': 'cron',
                'minute': parts[0],
                'hour': parts[1],
                'day': parts[2],
                'month': parts[3],
                'day_of_week': parts[4],
                'timezone': self.timezone,
                'max_instances': self.max_instances,
                'misfire_grace_time': self.misfire_grace_time,
                'coalesce': self.coalesce,
            }
        raise NotImplementedError(
            f'trigger type {self.type!r} is not wired into APScheduler; '
            f'supported: {[t.value for t in TriggerType.supported()]}'
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'type': self.type.value,
            'cron_expression': self.cron_expression,
            'timezone': self.timezone,
            'max_instances': self.max_instances,
            'misfire_grace_time': self.misfire_grace_time,
            'coalesce': self.coalesce,
            'run_date': self.run_date.isoformat() if self.run_date else None,
            'interval_seconds': self.interval_seconds,
        }


# ── Action Item ──────────────────────────────────────────────

@dataclass
class ActionItem:
    """A user-facing action extracted from chat.

    Distinct from ScheduledJob: an ActionItem may exist without being
    scheduled (status=PENDING), and a ScheduledJob references an ActionItem
    by `action_item_id`.
    """

    id: str
    title: str
    due_at_utc: Optional[str] = None        # ISO 8601 UTC
    original_due_at: Optional[str] = None   # verbatim user input
    source_timezone: Optional[str] = None   # tz attached during normalization
    priority: str = 'medium'                # low|medium|high|urgent
    status: ActionItemStatus = ActionItemStatus.PENDING
    source: str = 'chat'                    # chat|manual|imported
    notes: str = ''
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'due_at_utc': self.due_at_utc,
            'original_due_at': self.original_due_at,
            'source_timezone': self.source_timezone,
            'priority': self.priority,
            'status': self.status.value,
            'source': self.source,
            'notes': self.notes,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'metadata': self.metadata,
        }


# ── Scheduled Job ────────────────────────────────────────────

@dataclass
class ScheduledJob:
    """An APScheduler job config row.

    References an ActionItem. The job itself is the *schedule*; each
    execution is a separate JobRun.
    """

    id: str                                # APScheduler job id
    action_item_id: str
    trigger: TriggerSpec
    next_run_at: Optional[str] = None      # ISO 8601 UTC
    last_run_at: Optional[str] = None
    run_count: int = 0
    last_error: Optional[str] = None
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'action_item_id': self.action_item_id,
            'trigger': self.trigger.to_dict(),
            'next_run_at': self.next_run_at,
            'last_run_at': self.last_run_at,
            'run_count': self.run_count,
            'last_error': self.last_error,
            'enabled': self.enabled,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'metadata': self.metadata,
        }


# ── Job Run ──────────────────────────────────────────────────

@dataclass
class JobRun:
    """One execution attempt of a ScheduledJob.

    Created by `execute_job_with_tracking(job_id)` BEFORE the job runs,
    marked RUNNING, then SUCCEEDED / FAILED / TIMED_OUT after.
    """

    id: str
    job_id: str
    status: JobRunStatus = JobRunStatus.QUEUED
    started_at: Optional[str] = None       # ISO 8601 UTC
    ended_at: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    triggered_by: str = 'cron'             # cron|manual|test
    run_count_at_start: int = 0
    next_run_at_after: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'job_id': self.job_id,
            'status': self.status.value,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'duration_ms': self.duration_ms,
            'error': self.error,
            'triggered_by': self.triggered_by,
            'run_count_at_start': self.run_count_at_start,
            'next_run_at_after': self.next_run_at_after,
        }

    def to_run(self) -> Run:
        """Adapt this JobRun into a module-agnostic Run.

        A JobRun has no sub-steps (it is a single execution), so the Run
        contains a single Step named 'job_execute' whose OperationResult
        reflects the JobRunStatus. This is the Phase C adapter — existing
        callers are unaffected.

        JobRunStatus → RunStatus mapping:
            QUEUED    → pending (not yet started)
            RUNNING   → running
            SUCCEEDED → succeeded
            FAILED    → failed
            TIMED_OUT → timed_out
            ABANDONED → abandoned
        """
        status_map = {
            JobRunStatus.QUEUED: RunStatus.PENDING,
            JobRunStatus.RUNNING: RunStatus.RUNNING,
            JobRunStatus.SUCCEEDED: RunStatus.SUCCEEDED,
            JobRunStatus.FAILED: RunStatus.FAILED,
            JobRunStatus.TIMED_OUT: RunStatus.TIMED_OUT,
            JobRunStatus.ABANDONED: RunStatus.ABANDONED,
        }
        run_status = status_map.get(self.status, RunStatus.PENDING)

        # Build a single OperationResult for the job execution step
        if run_status == RunStatus.SUCCEEDED:
            op_result = OperationResult.success_with(step='job_execute')
        elif run_status == RunStatus.FAILED:
            op_result = OperationResult.failed(
                code='job_failed', message=self.error or 'job failed',
                step='job_execute')
        elif run_status == RunStatus.TIMED_OUT:
            op_result = OperationResult.timed_out(
                message=self.error or 'deadline exceeded', step='job_execute')
        elif run_status == RunStatus.ABANDONED:
            op_result = OperationResult.skipped(
                reason=self.error or 'abandoned', step='job_execute')
        else:
            op_result = OperationResult(
                success=False, status=StepStatus.PENDING,
                metadata={'step': 'job_execute'},
            )

        from modules.common.run import Step
        run = Run(
            run_id=self.id,
            run_type='scheduler.job',
            status=run_status,
            started_at=self.started_at,
            ended_at=self.ended_at,
            duration_ms=int(self.duration_ms),
            error=self.error,
            metadata={
                'job_id': self.job_id,
                'triggered_by': self.triggered_by,
                'run_count_at_start': self.run_count_at_start,
            },
        )
        run.steps.append(Step(
            step_id=f"{self.id}-step",
            run_id=run.run_id,
            name='job_execute',
            order=0,
            status=run_status,
            started_at=self.started_at,
            ended_at=self.ended_at,
            duration_ms=int(self.duration_ms),
            result=op_result,
        ))
        return run


# ── Quarantine ───────────────────────────────────────────────

@dataclass
class QuarantinedJob:
    """A legacy job that could not be migrated cleanly.

    Stored in a `quarantined_jobs` table (or a JSON sidecar) for manual
    review. NEVER auto-converted to an invalid Custom Job.
    """

    id: str
    raw_legacy: dict[str, Any]
    reason: str                           # why migration failed
    migrated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'raw_legacy': self.raw_legacy,
            'reason': self.reason,
            'migrated_at': self.migrated_at,
        }
