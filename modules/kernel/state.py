"""Explicit state machines for Agent Forge 2.0 kernel records."""
from __future__ import annotations

from collections.abc import Mapping

from .contracts import ApprovalStatus, ArtifactStatus, HandoffStatus, TaskStatus


class InvalidTransitionError(ValueError):
    """Raised when a record attempts an unsupported state transition."""


TASK_TERMINAL = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.DEGRADED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMED_OUT,
        TaskStatus.ABANDONED,
    }
)

TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.ABANDONED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.HANDOFF_PENDING,
            TaskStatus.REVIEWING,
            TaskStatus.SUCCEEDED,
            TaskStatus.DEGRADED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.ABANDONED,
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.ABANDONED,
        }
    ),
    TaskStatus.HANDOFF_PENDING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.REVIEWING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.ABANDONED,
        }
    ),
    TaskStatus.REVIEWING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.DEGRADED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
            TaskStatus.ABANDONED,
        }
    ),
    **{status: frozenset() for status in TASK_TERMINAL},
}

HANDOFF_TRANSITIONS: Mapping[HandoffStatus, frozenset[HandoffStatus]] = {
    HandoffStatus.REQUESTED: frozenset(
        {
            HandoffStatus.ACCEPTED,
            HandoffStatus.REJECTED,
            HandoffStatus.CANCELLED,
            HandoffStatus.EXPIRED,
            HandoffStatus.TIMED_OUT,
            HandoffStatus.FAILED,
        }
    ),
    HandoffStatus.ACCEPTED: frozenset(
        {
            HandoffStatus.CONSUMED,
            HandoffStatus.CANCELLED,
            HandoffStatus.EXPIRED,
            HandoffStatus.TIMED_OUT,
            HandoffStatus.FAILED,
        }
    ),
    HandoffStatus.CONSUMED: frozenset(
        {
            HandoffStatus.COMPLETED,
            HandoffStatus.TIMED_OUT,
            HandoffStatus.FAILED,
            HandoffStatus.CANCELLED,
        }
    ),
    **{
        status: frozenset()
        for status in {
            HandoffStatus.COMPLETED,
            HandoffStatus.REJECTED,
            HandoffStatus.FAILED,
            HandoffStatus.CANCELLED,
            HandoffStatus.EXPIRED,
            HandoffStatus.TIMED_OUT,
        }
    },
}

ARTIFACT_TRANSITIONS: Mapping[ArtifactStatus, frozenset[ArtifactStatus]] = {
    ArtifactStatus.DECLARED: frozenset(
        {ArtifactStatus.MATERIALIZED, ArtifactStatus.REJECTED, ArtifactStatus.INVALIDATED}
    ),
    ArtifactStatus.MATERIALIZED: frozenset(
        {ArtifactStatus.VALIDATING, ArtifactStatus.REJECTED, ArtifactStatus.INVALIDATED}
    ),
    ArtifactStatus.VALIDATING: frozenset(
        {
            ArtifactStatus.ACCEPTED,
            ArtifactStatus.DEGRADED,
            ArtifactStatus.REJECTED,
            ArtifactStatus.INVALIDATED,
        }
    ),
    ArtifactStatus.ACCEPTED: frozenset(
        {ArtifactStatus.INVALIDATED, ArtifactStatus.SUPERSEDED}
    ),
    ArtifactStatus.DEGRADED: frozenset(
        {ArtifactStatus.INVALIDATED, ArtifactStatus.SUPERSEDED}
    ),
    ArtifactStatus.REJECTED: frozenset(),
    ArtifactStatus.INVALIDATED: frozenset(),
    ArtifactStatus.SUPERSEDED: frozenset(),
}

APPROVAL_TRANSITIONS: Mapping[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.DENIED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        }
    ),
    ApprovalStatus.APPROVED: frozenset(
        {ApprovalStatus.CONSUMED, ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED}
    ),
    ApprovalStatus.CONSUMED: frozenset(),
    ApprovalStatus.DENIED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.CANCELLED: frozenset(),
}


def _validate(current: object, target: object, table: Mapping[object, frozenset]) -> None:
    if current == target:
        raise InvalidTransitionError(f"state is already {current!s}")
    if target not in table.get(current, frozenset()):
        raise InvalidTransitionError(f"invalid transition: {current!s} -> {target!s}")


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    _validate(current, target, TASK_TRANSITIONS)


def validate_handoff_transition(current: HandoffStatus, target: HandoffStatus) -> None:
    _validate(current, target, HANDOFF_TRANSITIONS)


def validate_artifact_transition(current: ArtifactStatus, target: ArtifactStatus) -> None:
    _validate(current, target, ARTIFACT_TRANSITIONS)


def validate_approval_transition(current: ApprovalStatus, target: ApprovalStatus) -> None:
    _validate(current, target, APPROVAL_TRANSITIONS)
