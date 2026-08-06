"""Persisted Handoff ownership-transfer authority for Kernel Slice 3."""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from .contracts import (
    ArtifactStatus,
    BudgetLimit,
    FailureCategory,
    FailureInfo,
    Handoff,
    HandoffStatus,
    KernelContractError,
    Task,
    TaskStatus,
    canonical_json,
    new_id,
    normalise_json_value,
    normalise_utc_timestamp,
)
from .repository import (
    ClaimConflictError,
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from .state import validate_handoff_transition
from ._lifecycle_base import (
    HandoffRecord,
    LifecycleConflictError,
    LifecycleRecordNotFoundError,
    LifecycleRepositoryBase,
    _bounded_text,
    _budget_dict,
    _digest,
    _identifier,
    _remaining_budget,
    _timestamp,
    _validate_lease_seconds,
)


class HandoffRepositoryMixin(LifecycleRepositoryBase):
    def request_handoff(
        self,
        *,
        task_id: str,
        source_agent_id: str,
        target_agent_id: str,
        reason: str,
        bounded_context: Mapping[str, Any],
        expected_artifact: str,
        acceptance_criteria: tuple[str, ...],
        transferred_budget: BudgetLimit,
        idempotency_key: str,
        claim_token: str,
        expected_task_version: int,
        idempotency_scope: str = "handoff",
        expires_at: str | None = None,
        handoff_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task, bool]:
        self.initialize()
        now_iso = _timestamp(now)
        scope = _identifier(idempotency_scope, "idempotency_scope")
        source_agent_id = _identifier(source_agent_id, "source_agent_id")
        target_agent_id = _identifier(target_agent_id, "target_agent_id")
        expected_artifact = _identifier(expected_artifact, "expected_artifact")
        idempotency_key = _identifier(idempotency_key, "idempotency_key")
        if source_agent_id == target_agent_id:
            raise KernelContractError("Handoff target must differ from source")
        candidate = Handoff(
            handoff_id=handoff_id or new_id("handoff"),
            task_id=task_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            reason=reason,
            bounded_context=bounded_context,
            expected_artifact=expected_artifact,
            acceptance_criteria=acceptance_criteria,
            transferred_budget=transferred_budget,
            idempotency_key=idempotency_key,
            created_at=now_iso,
        )
        expiry = normalise_utc_timestamp(expires_at, field_name="expires_at")
        if expiry is not None and datetime.fromisoformat(expiry) <= datetime.fromisoformat(now_iso):
            raise KernelContractError("Handoff expiry must be in the future")
        request = {
            "task_id": candidate.task_id,
            "source_agent_id": candidate.source_agent_id,
            "target_agent_id": candidate.target_agent_id,
            "reason": candidate.reason,
            "bounded_context": candidate.bounded_context,
            "expected_artifact": candidate.expected_artifact,
            "acceptance_criteria": list(candidate.acceptance_criteria),
            "transferred_budget": _budget_dict(candidate.transferred_budget),
            "expires_at": expiry,
        }
        request_digest = _digest(request)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT * FROM kernel_handoffs WHERE idempotency_scope=? AND idempotency_key=?",
                    (scope, candidate.idempotency_key),
                ).fetchone()
                if replay is not None:
                    if replay["request_digest"] != request_digest:
                        raise IdempotencyConflictError("Handoff idempotency key was reused for a different request")
                    task = self._task_in_tx(connection, candidate.task_id)
                    connection.commit()
                    return self._row_to_handoff(replay), task, False
                task = self._task_in_tx(connection, candidate.task_id)
                if task.record_version != expected_task_version:
                    raise ConcurrencyConflictError("stale Task version")
                self._claim_is_active(task, claim_token, candidate.source_agent_id, now_iso)
                if not _remaining_budget(task).contains(candidate.transferred_budget):
                    raise LifecycleConflictError("Handoff budget exceeds the Task remaining budget")
                connection.execute(
                    """INSERT INTO kernel_handoffs (
                        handoff_id, task_id, source_agent_id, target_agent_id, reason,
                        bounded_context_json, expected_artifact, acceptance_criteria_json,
                        transferred_budget_json, status, idempotency_scope, idempotency_key,
                        request_digest, consume_idempotency_key, consume_digest, outcome_json,
                        created_at, expires_at, accepted_at, consumed_at, finished_at, record_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, NULL, NULL, 0)""",
                    (
                        candidate.handoff_id,
                        candidate.task_id,
                        candidate.source_agent_id,
                        candidate.target_agent_id,
                        candidate.reason,
                        canonical_json(candidate.bounded_context),
                        candidate.expected_artifact,
                        canonical_json(list(candidate.acceptance_criteria)),
                        canonical_json(_budget_dict(candidate.transferred_budget)),
                        HandoffStatus.REQUESTED.value,
                        scope,
                        candidate.idempotency_key,
                        request_digest,
                        canonical_json({}),
                        now_iso,
                        expiry,
                    ),
                )
                next_task = self._pause_task(
                    connection,
                    task,
                    target_status=TaskStatus.HANDOFF_PENDING,
                    current_step=candidate.handoff_id,
                    event="handoff_requested",
                    reference_id=candidate.handoff_id,
                    now_iso=now_iso,
                )
                self._audit(
                    connection,
                    task_id=task.task_id,
                    record_type="handoff",
                    record_id=candidate.handoff_id,
                    event="requested",
                    state=HandoffStatus.REQUESTED.value,
                    payload={"target_agent_id": candidate.target_agent_id, "run_id": task.run_id},
                    created_at=now_iso,
                )
                row = connection.execute(
                    "SELECT * FROM kernel_handoffs WHERE handoff_id=?", (candidate.handoff_id,)
                ).fetchone()
                connection.commit()
                assert row is not None
                return self._row_to_handoff(row), next_task, True
            except Exception:
                connection.rollback()
                raise

    def accept_handoff(
        self,
        handoff_id: str,
        *,
        target_agent_id: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> HandoffRecord:
        self.initialize()
        handoff_id = _identifier(handoff_id, "handoff_id")
        now_iso = _timestamp(now)
        target = _identifier(target_agent_id, "target_agent_id")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
                current = self._row_to_handoff(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Handoff version")
                if current.target_agent_id != target:
                    raise LifecycleConflictError("only the intended target can accept a Handoff")
                if current.expires_at and datetime.fromisoformat(current.expires_at) <= datetime.fromisoformat(now_iso):
                    raise LifecycleConflictError("Handoff expired before acceptance")
                validate_handoff_transition(current.status, HandoffStatus.ACCEPTED)
                task = self._task_in_tx(connection, current.task_id)
                if task.status is not TaskStatus.HANDOFF_PENDING or task.owner_agent_id != current.source_agent_id:
                    raise LifecycleConflictError("Task is no longer pending under the Handoff source")
                cursor = connection.execute(
                    """UPDATE kernel_handoffs SET status=?, accepted_at=?, record_version=?
                       WHERE handoff_id=? AND record_version=?""",
                    (HandoffStatus.ACCEPTED.value, now_iso, current.record_version + 1, handoff_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Handoff changed during compare-and-set")
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="handoff",
                    record_id=handoff_id,
                    event="accepted",
                    state=HandoffStatus.ACCEPTED.value,
                    payload={"target_agent_id": target},
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_handoff(updated)
            except Exception:
                connection.rollback()
                raise

    def consume_handoff(
        self,
        handoff_id: str,
        *,
        target_agent_id: str,
        expected_version: int,
        consume_idempotency_key: str,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task, bool]:
        self.initialize()
        handoff_id = _identifier(handoff_id, "handoff_id")
        lease = _validate_lease_seconds(lease_seconds)
        now_iso = _timestamp(now)
        target = _identifier(target_agent_id, "target_agent_id")
        consume_key = _identifier(consume_idempotency_key, "consume_idempotency_key")
        consume_digest = _digest({"handoff_id": handoff_id, "target_agent_id": target})
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
                current = self._row_to_handoff(row)
                if current.status is HandoffStatus.CONSUMED:
                    if row["consume_idempotency_key"] == consume_key and row["consume_digest"] == consume_digest:
                        task = self._task_in_tx(connection, current.task_id)
                        connection.commit()
                        return current, task, False
                    raise LifecycleConflictError("Handoff was already consumed")
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Handoff version")
                if current.target_agent_id != target:
                    raise LifecycleConflictError("only the intended target can consume a Handoff")
                if current.expires_at and datetime.fromisoformat(current.expires_at) <= datetime.fromisoformat(now_iso):
                    raise LifecycleConflictError("expired Handoff cannot be consumed")
                validate_handoff_transition(current.status, HandoffStatus.CONSUMED)
                task = self._task_in_tx(connection, current.task_id)
                if task.status is not TaskStatus.HANDOFF_PENDING or task.owner_agent_id != current.source_agent_id:
                    raise LifecycleConflictError("Task ownership no longer matches the Handoff source")
                cursor = connection.execute(
                    """UPDATE kernel_handoffs SET status=?, consumed_at=?, consume_idempotency_key=?,
                       consume_digest=?, record_version=? WHERE handoff_id=? AND record_version=?""",
                    (
                        HandoffStatus.CONSUMED.value,
                        now_iso,
                        consume_key,
                        consume_digest,
                        current.record_version + 1,
                        handoff_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Handoff changed during compare-and-set")
                next_task = self._resume_task(
                    connection,
                    task,
                    owner_agent_id=target,
                    current_step=handoff_id,
                    event="handoff_consumed",
                    reference_id=handoff_id,
                    now_iso=now_iso,
                    lease_seconds=lease,
                )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="handoff",
                    record_id=handoff_id,
                    event="consumed",
                    state=HandoffStatus.CONSUMED.value,
                    payload={"owner_agent_id": target, "task_version": next_task.record_version},
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_handoff(updated), next_task, True
            except Exception:
                connection.rollback()
                raise

    def reject_handoff(
        self,
        handoff_id: str,
        *,
        target_agent_id: str,
        reason: str,
        expected_version: int,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task]:
        return self._finish_pending_handoff(
            handoff_id,
            target_status=HandoffStatus.REJECTED,
            actor_agent_id=target_agent_id,
            reason=reason,
            expected_version=expected_version,
            lease_seconds=lease_seconds,
            now=now,
        )

    def cancel_handoff(
        self,
        handoff_id: str,
        *,
        actor_agent_id: str,
        reason: str,
        expected_version: int,
        lease_seconds: int = 300,
        claim_token: str | None = None,
        expected_task_version: int | None = None,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task]:
        current = self.get_handoff(handoff_id)
        if current is None:
            raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
        if current.status in {HandoffStatus.REQUESTED, HandoffStatus.ACCEPTED}:
            return self._finish_pending_handoff(
                handoff_id,
                target_status=HandoffStatus.CANCELLED,
                actor_agent_id=actor_agent_id,
                reason=reason,
                expected_version=expected_version,
                lease_seconds=lease_seconds,
                now=now,
            )
        return self._terminate_consumed_handoff(
            handoff_id,
            target_status=HandoffStatus.CANCELLED,
            task_status=TaskStatus.CANCELLED,
            failure_category=FailureCategory.CANCELLATION,
            failure_code="handoff_cancelled",
            actor_agent_id=actor_agent_id,
            reason=reason,
            expected_version=expected_version,
            claim_token=claim_token,
            expected_task_version=expected_task_version,
            now=now,
        )

    def fail_handoff(
        self,
        handoff_id: str,
        *,
        actor_agent_id: str,
        reason: str,
        expected_version: int,
        claim_token: str | None = None,
        expected_task_version: int | None = None,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task]:
        return self._terminate_handoff_with_task(
            handoff_id,
            target_status=HandoffStatus.FAILED,
            task_status=TaskStatus.FAILED,
            failure_category=FailureCategory.EXECUTION,
            failure_code="handoff_failed",
            actor_agent_id=actor_agent_id,
            reason=reason,
            expected_version=expected_version,
            claim_token=claim_token,
            expected_task_version=expected_task_version,
            now=now,
        )

    def time_out_handoff(
        self,
        handoff_id: str,
        *,
        expected_version: int,
        expected_task_version: int,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task]:
        return self._terminate_handoff_with_task(
            handoff_id,
            target_status=HandoffStatus.TIMED_OUT,
            task_status=TaskStatus.TIMED_OUT,
            failure_category=FailureCategory.TIMEOUT,
            failure_code="handoff_timed_out",
            actor_agent_id="kernel.timeout",
            reason="Handoff timed out before completion",
            expected_version=expected_version,
            claim_token=None,
            expected_task_version=expected_task_version,
            control_plane=True,
            now=now,
        )

    def _terminate_consumed_handoff(
        self,
        handoff_id: str,
        **kwargs: Any,
    ) -> tuple[HandoffRecord, Task]:
        return self._terminate_handoff_with_task(handoff_id, require_consumed=True, **kwargs)

    def _terminate_handoff_with_task(
        self,
        handoff_id: str,
        *,
        target_status: HandoffStatus,
        task_status: TaskStatus,
        failure_category: FailureCategory,
        failure_code: str,
        actor_agent_id: str,
        reason: str,
        expected_version: int,
        claim_token: str | None,
        expected_task_version: int | None,
        now: datetime | None,
        require_consumed: bool = False,
        control_plane: bool = False,
    ) -> tuple[HandoffRecord, Task]:
        self.initialize()
        handoff_id = _identifier(handoff_id, "handoff_id")
        now_iso = _timestamp(now)
        actor = _identifier(actor_agent_id, "actor_agent_id")
        detail = _bounded_text(reason, "handoff outcome reason")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)
                ).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
                current = self._row_to_handoff(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Handoff version")
                if require_consumed and current.status is not HandoffStatus.CONSUMED:
                    raise LifecycleConflictError("Handoff is not actively consumed")
                validate_handoff_transition(current.status, target_status)
                task = self._task_in_tx(connection, current.task_id)
                if expected_task_version is None or task.record_version != expected_task_version:
                    raise ConcurrencyConflictError("stale or missing Task version")
                if current.status is HandoffStatus.CONSUMED:
                    if task.status is not TaskStatus.RUNNING or task.owner_agent_id != current.target_agent_id:
                        raise LifecycleConflictError("Task is no longer owned by the Handoff target")
                    if not control_plane:
                        if actor != current.target_agent_id or claim_token is None:
                            raise ClaimConflictError("the active Handoff target claim is required")
                        self._claim_is_active(task, claim_token, current.target_agent_id, now_iso)
                else:
                    if task.status is not TaskStatus.HANDOFF_PENDING or task.owner_agent_id != current.source_agent_id:
                        raise LifecycleConflictError("Task no longer matches the pending Handoff")
                    if actor not in {current.source_agent_id, current.target_agent_id, "kernel.timeout"}:
                        raise LifecycleConflictError("Handoff actor is not authorized")
                outcome = {"actor_agent_id": actor, "reason": detail}
                cursor = connection.execute(
                    """UPDATE kernel_handoffs SET status=?, outcome_json=?, finished_at=?, record_version=?
                       WHERE handoff_id=? AND record_version=?""",
                    (
                        target_status.value, canonical_json(outcome), now_iso,
                        current.record_version + 1, handoff_id, expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Handoff changed during compare-and-set")
                failure = FailureInfo(
                    category=failure_category,
                    message=detail,
                    code=failure_code,
                    details={"handoff_id": handoff_id, "actor_agent_id": actor},
                )
                next_task = self._finish_task(
                    connection,
                    task,
                    target_status=task_status,
                    failure=failure,
                    event=target_status.value,
                    reference_id=handoff_id,
                    now_iso=now_iso,
                )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="handoff",
                    record_id=handoff_id,
                    event=target_status.value,
                    state=target_status.value,
                    payload=outcome,
                    created_at=now_iso,
                )
                updated = connection.execute(
                    "SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)
                ).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_handoff(updated), next_task
            except Exception:
                connection.rollback()
                raise

    def expire_handoff(
        self,
        handoff_id: str,
        *,
        expected_version: int,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task]:
        return self._finish_pending_handoff(
            handoff_id,
            target_status=HandoffStatus.EXPIRED,
            actor_agent_id="kernel.expiry",
            reason="Handoff expired before consumption",
            expected_version=expected_version,
            lease_seconds=lease_seconds,
            now=now,
            require_expired=True,
        )

    def _finish_pending_handoff(
        self,
        handoff_id: str,
        *,
        target_status: HandoffStatus,
        actor_agent_id: str,
        reason: str,
        expected_version: int,
        lease_seconds: int,
        now: datetime | None,
        require_expired: bool = False,
    ) -> tuple[HandoffRecord, Task]:
        self.initialize()
        handoff_id = _identifier(handoff_id, "handoff_id")
        lease = _validate_lease_seconds(lease_seconds)
        now_iso = _timestamp(now)
        actor = _identifier(actor_agent_id, "actor_agent_id")
        detail = _bounded_text(reason, "handoff outcome reason")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
                current = self._row_to_handoff(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Handoff version")
                if target_status is HandoffStatus.REJECTED and actor != current.target_agent_id:
                    raise LifecycleConflictError("only the intended target can reject a Handoff")
                if target_status is HandoffStatus.CANCELLED and actor not in {current.source_agent_id, current.target_agent_id}:
                    raise LifecycleConflictError("only a Handoff participant can cancel it")
                if require_expired:
                    if current.expires_at is None or datetime.fromisoformat(current.expires_at) > datetime.fromisoformat(now_iso):
                        raise LifecycleConflictError("Handoff has not expired")
                validate_handoff_transition(current.status, target_status)
                outcome = {"actor_agent_id": actor, "reason": detail}
                cursor = connection.execute(
                    """UPDATE kernel_handoffs SET status=?, outcome_json=?, finished_at=?, record_version=?
                       WHERE handoff_id=? AND record_version=?""",
                    (
                        target_status.value,
                        canonical_json(outcome),
                        now_iso,
                        current.record_version + 1,
                        handoff_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Handoff changed during compare-and-set")
                task = self._task_in_tx(connection, current.task_id)
                if task.status is not TaskStatus.HANDOFF_PENDING or task.owner_agent_id != current.source_agent_id:
                    raise LifecycleConflictError("Task ownership no longer matches the pending Handoff")
                next_task = self._resume_task(
                    connection,
                    task,
                    owner_agent_id=current.source_agent_id,
                    current_step=handoff_id,
                    event=f"handoff_{target_status.value}",
                    reference_id=handoff_id,
                    now_iso=now_iso,
                    lease_seconds=lease,
                )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="handoff",
                    record_id=handoff_id,
                    event=target_status.value,
                    state=target_status.value,
                    payload=outcome,
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_handoff(updated), next_task
            except Exception:
                connection.rollback()
                raise

    def complete_handoff(
        self,
        handoff_id: str,
        *,
        target_agent_id: str,
        expected_version: int,
        claim_token: str,
        expected_task_version: int,
        outcome: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> tuple[HandoffRecord, Task]:
        self.initialize()
        handoff_id = _identifier(handoff_id, "handoff_id")
        now_iso = _timestamp(now)
        target = _identifier(target_agent_id, "target_agent_id")
        normalized_outcome = normalise_json_value(outcome or {}, field_name="handoff outcome")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
                current = self._row_to_handoff(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Handoff version")
                if current.target_agent_id != target:
                    raise LifecycleConflictError("only the Handoff target can complete it")
                validate_handoff_transition(current.status, HandoffStatus.COMPLETED)
                task = self._task_in_tx(connection, current.task_id)
                if task.record_version != expected_task_version:
                    raise ConcurrencyConflictError("stale Task version")
                self._claim_is_active(task, claim_token, target, now_iso)
                artifact_row = connection.execute(
                    """SELECT artifact_id FROM kernel_artifacts
                       WHERE handoff_id=? AND task_id=? AND producer_agent_id=? AND artifact_type=?
                         AND validation_status IN (?, ?)
                       ORDER BY created_at DESC LIMIT 1""",
                    (
                        handoff_id,
                        current.task_id,
                        target,
                        current.expected_artifact,
                        ArtifactStatus.ACCEPTED.value,
                        ArtifactStatus.DEGRADED.value,
                    ),
                ).fetchone()
                if artifact_row is None:
                    raise LifecycleConflictError("Handoff cannot complete before its expected Artifact is accepted/degraded")
                normalized_outcome = {**dict(normalized_outcome), "artifact_id": artifact_row["artifact_id"]}
                cursor = connection.execute(
                    """UPDATE kernel_handoffs SET status=?, outcome_json=?, finished_at=?, record_version=?
                       WHERE handoff_id=? AND record_version=?""",
                    (
                        HandoffStatus.COMPLETED.value,
                        canonical_json(normalized_outcome),
                        now_iso,
                        current.record_version + 1,
                        handoff_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Handoff changed during compare-and-set")
                next_task = replace(
                    task,
                    current_step=artifact_row["artifact_id"],
                    updated_at=now_iso,
                    record_version=task.record_version + 1,
                )
                self._update_task(connection, next_task, task.record_version)
                self._insert_checkpoint(
                    connection,
                    next_task,
                    payload={
                        "event": "handoff_completed",
                        "handoff_id": handoff_id,
                        "artifact_id": artifact_row["artifact_id"],
                    },
                )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="handoff",
                    record_id=handoff_id,
                    event="completed",
                    state=HandoffStatus.COMPLETED.value,
                    payload=normalized_outcome,
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_handoff(updated), next_task
            except Exception:
                connection.rollback()
                raise

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None:
        self.initialize()
        handoff_id = _identifier(handoff_id, "handoff_id")
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)).fetchone()
            return self._row_to_handoff(row) if row is not None else None

    def list_handoffs(self, task_id: str) -> list[HandoffRecord]:
        self.initialize()
        task_id = _identifier(task_id, "task_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM kernel_handoffs WHERE task_id=? ORDER BY created_at, handoff_id",
                (task_id,),
            ).fetchall()
        return [self._row_to_handoff(row) for row in rows]
