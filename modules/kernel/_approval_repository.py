"""Scoped Approval decision and consumption authority for Kernel Slice 3."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import hashlib
import re
from typing import Any, Mapping

from .contracts import (
    Approval,
    ApprovalStatus,
    FailureCategory,
    FailureInfo,
    KernelContractError,
    Task,
    TaskStatus,
    canonical_json,
    new_id,
    normalise_json_value,
    normalise_utc_timestamp,
)
from .repository import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from .state import validate_approval_transition
from ._lifecycle_base import (
    _APPROVAL_SCOPE_KEYS,
    ApprovalAuthorization,
    ApprovalAuthorizationError,
    ApprovalRecord,
    LifecycleConflictError,
    LifecycleRecordNotFoundError,
    LifecycleRepositoryBase,
    _digest,
    _identifier,
    _normalise_reference,
    _timestamp,
    _validate_lease_seconds,
)


class ApprovalRepositoryMixin(LifecycleRepositoryBase):
    @staticmethod
    def _normalise_approval_scope(scope: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = normalise_json_value(scope, field_name="approval scope")
        if not isinstance(normalized, Mapping) or not normalized:
            raise KernelContractError("Approval scope must be a non-empty object")
        unknown = set(normalized) - _APPROVAL_SCOPE_KEYS
        if unknown:
            raise KernelContractError(f"Approval scope has unsupported keys: {sorted(unknown)}")
        result = dict(normalized)
        for key, value in result.items():
            if not isinstance(value, (bool, str, list)):
                raise KernelContractError(f"Approval scope {key} has unsupported value type")
            if isinstance(value, list):
                if not value or not all(isinstance(item, str) and item.strip() for item in value):
                    raise KernelContractError(f"Approval scope {key} list must contain strings")
                if key == "workspace_write":
                    result[key] = [_normalise_reference(item) for item in value]
                else:
                    result[key] = [str(normalise_json_value(item, field_name=f"approval scope {key}")) for item in value]
        return result

    @staticmethod
    def _normalise_requested_action(action: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized = normalise_json_value(action, field_name="requested_action")
        if not isinstance(normalized, Mapping):
            raise KernelContractError("requested_action must be an object")
        if not {"operation", "target"}.issubset(normalized):
            raise KernelContractError("requested_action requires operation and target")
        operation = _identifier(str(normalized["operation"]), "requested_action.operation")
        if not isinstance(normalized["target"], str) or not normalized["target"].strip():
            raise KernelContractError("requested_action.target must be a non-empty string")
        if operation.startswith("workspace."):
            normalized = dict(normalized)
            normalized["target"] = _normalise_reference(str(normalized["target"]))
        artifact_digest = normalized.get("artifact_digest")
        if operation == "workspace.write" and artifact_digest is None:
            raise KernelContractError("workspace.write Approval requires artifact_digest")
        if artifact_digest is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact_digest)):
            raise KernelContractError("requested_action.artifact_digest must be sha256")
        return normalized

    def request_approval(
        self,
        *,
        task_id: str,
        requested_action: Mapping[str, Any],
        reason: str,
        scope: Mapping[str, Any],
        requester_agent_id: str,
        idempotency_key: str,
        claim_token: str,
        expected_task_version: int,
        expires_at: str | None = None,
        idempotency_scope: str = "approval",
        approval_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[ApprovalRecord, Task, bool]:
        self.initialize()
        now_iso = _timestamp(now)
        normalized_action = self._normalise_requested_action(requested_action)
        normalized_scope = self._normalise_approval_scope(scope)
        scope_name = _identifier(idempotency_scope, "idempotency_scope")
        key = _identifier(idempotency_key, "idempotency_key")
        expiry = normalise_utc_timestamp(expires_at, field_name="expires_at")
        candidate = Approval(
            approval_id=approval_id or new_id("approval"),
            task_id=task_id,
            requested_action=normalized_action,
            reason=reason,
            scope=normalized_scope,
            requester_agent_id=requester_agent_id,
            requested_at=now_iso,
            expires_at=expiry,
        )
        if candidate.requested_action["operation"] == "workspace.write":
            allowed_paths = candidate.scope.get("workspace_write")
            if allowed_paths != [candidate.requested_action["target"]]:
                raise KernelContractError("workspace.write Approval scope must contain only the exact target")
        action_digest = "sha256:" + hashlib.sha256(canonical_json(candidate.requested_action).encode("utf-8")).hexdigest()
        request = {
            "task_id": candidate.task_id,
            "requested_action": candidate.requested_action,
            "action_digest": action_digest,
            "reason": candidate.reason,
            "scope": candidate.scope,
            "requester_agent_id": candidate.requester_agent_id,
            "expires_at": candidate.expires_at,
        }
        request_digest = _digest(request)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT * FROM kernel_approvals WHERE idempotency_scope=? AND idempotency_key=?",
                    (scope_name, key),
                ).fetchone()
                if replay is not None:
                    if replay["request_digest"] != request_digest:
                        raise IdempotencyConflictError("Approval idempotency key was reused for a different request")
                    task = self._task_in_tx(connection, candidate.task_id)
                    connection.commit()
                    return self._row_to_approval(replay), task, False
                task = self._task_in_tx(connection, candidate.task_id)
                if task.record_version != expected_task_version:
                    raise ConcurrencyConflictError("stale Task version")
                self._claim_is_active(task, claim_token, candidate.requester_agent_id, now_iso)
                connection.execute(
                    """INSERT INTO kernel_approvals (
                        approval_id, task_id, requested_action_json, action_digest, reason,
                        scope_json, requester_agent_id, decision, decision_maker, requested_at,
                        decided_at, expires_at, consumed_at, audit_reference, idempotency_scope,
                        idempotency_key, request_digest, consume_idempotency_key, consume_digest,
                        record_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, NULL, NULL, ?, ?, ?, NULL, NULL, 0)""",
                    (
                        candidate.approval_id,
                        candidate.task_id,
                        canonical_json(candidate.requested_action),
                        action_digest,
                        candidate.reason,
                        canonical_json(candidate.scope),
                        candidate.requester_agent_id,
                        ApprovalStatus.PENDING.value,
                        now_iso,
                        candidate.expires_at,
                        scope_name,
                        key,
                        request_digest,
                    ),
                )
                next_task = self._pause_task(
                    connection,
                    task,
                    target_status=TaskStatus.WAITING_APPROVAL,
                    current_step=candidate.approval_id,
                    event="approval_requested",
                    reference_id=candidate.approval_id,
                    now_iso=now_iso,
                    approval_ids=tuple((*task.approval_ids, candidate.approval_id)),
                )
                self._audit(
                    connection,
                    task_id=task.task_id,
                    record_type="approval",
                    record_id=candidate.approval_id,
                    event="requested",
                    state=ApprovalStatus.PENDING.value,
                    payload={"action_digest": action_digest, "run_id": task.run_id},
                    created_at=now_iso,
                )
                row = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (candidate.approval_id,)).fetchone()
                connection.commit()
                assert row is not None
                return self._row_to_approval(row), next_task, True
            except Exception:
                connection.rollback()
                raise

    def decide_approval(
        self,
        approval_id: str,
        decision: ApprovalStatus,
        *,
        decision_maker: str,
        audit_reference: str,
        expected_version: int,
        now: datetime | None = None,
    ) -> tuple[ApprovalRecord, Task | None]:
        if decision not in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise KernelContractError("decision must be approved or denied")
        self.initialize()
        approval_id = _identifier(approval_id, "approval_id")
        now_iso = _timestamp(now)
        maker = _identifier(decision_maker, "decision_maker")
        audit_ref = _identifier(audit_reference, "audit_reference")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Approval not found: {approval_id}")
                current = self._row_to_approval(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Approval version")
                if current.expires_at and datetime.fromisoformat(current.expires_at) <= datetime.fromisoformat(now_iso):
                    raise ApprovalAuthorizationError("expired Approval cannot be decided")
                validate_approval_transition(current.decision, decision)
                cursor = connection.execute(
                    """UPDATE kernel_approvals SET decision=?, decision_maker=?, decided_at=?,
                       audit_reference=?, record_version=? WHERE approval_id=? AND record_version=?""",
                    (
                        decision.value,
                        maker,
                        now_iso,
                        audit_ref,
                        current.record_version + 1,
                        approval_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Approval changed during compare-and-set")
                task_result: Task | None = None
                if decision is ApprovalStatus.DENIED:
                    task = self._task_in_tx(connection, current.task_id)
                    if task.status is not TaskStatus.WAITING_APPROVAL:
                        raise LifecycleConflictError("Task is no longer waiting for this Approval")
                    task_result = self._finish_task(
                        connection,
                        task,
                        target_status=TaskStatus.FAILED,
                        failure=FailureInfo(
                            category=FailureCategory.APPROVAL_DENIED,
                            message="Required action was denied",
                            retryable=False,
                            code="approval_denied",
                            details={"approval_id": approval_id, "audit_reference": audit_ref},
                        ),
                        event="approval_denied",
                        reference_id=approval_id,
                        now_iso=now_iso,
                    )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="approval",
                    record_id=approval_id,
                    event=decision.value,
                    state=decision.value,
                    payload={"decision_maker": maker, "audit_reference": audit_ref},
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_approval(updated), task_result
            except Exception:
                connection.rollback()
                raise

    def consume_approval(
        self,
        approval_id: str,
        *,
        requester_agent_id: str,
        requested_action: Mapping[str, Any],
        expected_version: int,
        consume_idempotency_key: str,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> tuple[ApprovalAuthorization, ApprovalRecord, Task, bool]:
        self.initialize()
        approval_id = _identifier(approval_id, "approval_id")
        lease = _validate_lease_seconds(lease_seconds)
        now_iso = _timestamp(now)
        requester = _identifier(requester_agent_id, "requester_agent_id")
        consume_key = _identifier(consume_idempotency_key, "consume_idempotency_key")
        normalized_action = self._normalise_requested_action(requested_action)
        action_digest = "sha256:" + hashlib.sha256(canonical_json(normalized_action).encode("utf-8")).hexdigest()
        consume_digest = _digest({"approval_id": approval_id, "requester_agent_id": requester, "action_digest": action_digest})
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Approval not found: {approval_id}")
                current = self._row_to_approval(row)
                if current.decision is ApprovalStatus.CONSUMED:
                    if row["consume_idempotency_key"] == consume_key and row["consume_digest"] == consume_digest:
                        task = self._task_in_tx(connection, current.task_id)
                        authorization = self._authorization(current)
                        connection.commit()
                        return authorization, current, task, False
                    raise ApprovalAuthorizationError("Approval was already consumed")
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Approval version")
                if current.requester_agent_id != requester:
                    raise ApprovalAuthorizationError("Approval requester does not match")
                if current.action_digest != action_digest:
                    raise ApprovalAuthorizationError("requested action or Artifact digest changed")
                if current.expires_at and datetime.fromisoformat(current.expires_at) <= datetime.fromisoformat(now_iso):
                    raise ApprovalAuthorizationError("expired Approval cannot authorize work")
                validate_approval_transition(current.decision, ApprovalStatus.CONSUMED)
                cursor = connection.execute(
                    """UPDATE kernel_approvals SET decision=?, consumed_at=?, consume_idempotency_key=?,
                       consume_digest=?, record_version=? WHERE approval_id=? AND record_version=?""",
                    (
                        ApprovalStatus.CONSUMED.value,
                        now_iso,
                        consume_key,
                        consume_digest,
                        current.record_version + 1,
                        approval_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Approval changed during compare-and-set")
                task = self._task_in_tx(connection, current.task_id)
                if task.status is not TaskStatus.WAITING_APPROVAL or task.owner_agent_id != requester:
                    raise ApprovalAuthorizationError("Task is no longer waiting for this requester")
                next_task = self._resume_task(
                    connection,
                    task,
                    owner_agent_id=requester,
                    current_step=approval_id,
                    event="approval_consumed",
                    reference_id=approval_id,
                    now_iso=now_iso,
                    lease_seconds=lease,
                )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="approval",
                    record_id=approval_id,
                    event="consumed",
                    state=ApprovalStatus.CONSUMED.value,
                    payload={"action_digest": action_digest, "audit_reference": current.audit_reference},
                    created_at=now_iso,
                )
                updated_row = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
                connection.commit()
                assert updated_row is not None
                updated = self._row_to_approval(updated_row)
                return self._authorization(updated), updated, next_task, True
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _authorization(approval: ApprovalRecord) -> ApprovalAuthorization:
        if approval.decision is not ApprovalStatus.CONSUMED or approval.consumed_at is None or approval.audit_reference is None:
            raise ApprovalAuthorizationError("Approval is not a consumed authorization")
        return ApprovalAuthorization(
            approval_id=approval.approval_id,
            task_id=approval.task_id,
            requester_agent_id=approval.requester_agent_id,
            requested_action=approval.requested_action,
            scope=approval.scope,
            action_digest=approval.action_digest,
            audit_reference=approval.audit_reference,
            consumed_at=approval.consumed_at,
        )

    def expire_approval(
        self,
        approval_id: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> tuple[ApprovalRecord, Task]:
        return self._terminate_approval(
            approval_id,
            target=ApprovalStatus.EXPIRED,
            expected_version=expected_version,
            now=now,
        )

    def cancel_approval(
        self,
        approval_id: str,
        *,
        expected_version: int,
        decision_maker: str,
        audit_reference: str,
        now: datetime | None = None,
    ) -> tuple[ApprovalRecord, Task]:
        return self._terminate_approval(
            approval_id,
            target=ApprovalStatus.CANCELLED,
            expected_version=expected_version,
            decision_maker=decision_maker,
            audit_reference=audit_reference,
            now=now,
        )

    def _terminate_approval(
        self,
        approval_id: str,
        *,
        target: ApprovalStatus,
        expected_version: int,
        decision_maker: str | None = None,
        audit_reference: str | None = None,
        now: datetime | None,
    ) -> tuple[ApprovalRecord, Task]:
        self.initialize()
        approval_id = _identifier(approval_id, "approval_id")
        now_iso = _timestamp(now)
        maker = _identifier(decision_maker or "kernel.expiry", "decision_maker")
        audit_ref = _identifier(audit_reference or f"approval:{approval_id}", "audit_reference")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Approval not found: {approval_id}")
                current = self._row_to_approval(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Approval version")
                if target is ApprovalStatus.EXPIRED:
                    if current.expires_at is None or datetime.fromisoformat(current.expires_at) > datetime.fromisoformat(now_iso):
                        raise ApprovalAuthorizationError("Approval has not expired")
                validate_approval_transition(current.decision, target)
                cursor = connection.execute(
                    """UPDATE kernel_approvals SET decision=?, decision_maker=?, decided_at=?,
                       audit_reference=?, record_version=? WHERE approval_id=? AND record_version=?""",
                    (target.value, maker, now_iso, audit_ref, current.record_version + 1, approval_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Approval changed during compare-and-set")
                task = self._task_in_tx(connection, current.task_id)
                if task.status is not TaskStatus.WAITING_APPROVAL:
                    raise LifecycleConflictError("Task is no longer waiting for this Approval")
                if target is ApprovalStatus.EXPIRED:
                    task_status = TaskStatus.TIMED_OUT
                    failure = FailureInfo(
                        category=FailureCategory.TIMEOUT,
                        message="Required Approval expired",
                        code="approval_expired",
                        details={"approval_id": approval_id, "audit_reference": audit_ref},
                    )
                else:
                    task_status = TaskStatus.CANCELLED
                    failure = FailureInfo(
                        category=FailureCategory.CANCELLATION,
                        message="Required Approval was cancelled",
                        code="approval_cancelled",
                        details={"approval_id": approval_id, "audit_reference": audit_ref},
                    )
                next_task = self._finish_task(
                    connection,
                    task,
                    target_status=task_status,
                    failure=failure,
                    event=f"approval_{target.value}",
                    reference_id=approval_id,
                    now_iso=now_iso,
                )
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="approval",
                    record_id=approval_id,
                    event=target.value,
                    state=target.value,
                    payload={"decision_maker": maker, "audit_reference": audit_ref},
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_approval(updated), next_task
            except Exception:
                connection.rollback()
                raise

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        self.initialize()
        approval_id = _identifier(approval_id, "approval_id")
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM kernel_approvals WHERE approval_id=?", (approval_id,)).fetchone()
            return self._row_to_approval(row) if row is not None else None

    def list_approvals(self, task_id: str) -> list[ApprovalRecord]:
        self.initialize()
        task_id = _identifier(task_id, "task_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM kernel_approvals WHERE task_id=? ORDER BY requested_at, approval_id",
                (task_id,),
            ).fetchall()
        return [self._row_to_approval(row) for row in rows]
