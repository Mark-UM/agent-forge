"""Immutable Workspace Artifact authority for Kernel Slice 3."""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    Artifact,
    ArtifactStatus,
    HandoffStatus,
    KernelContractError,
    Sensitivity,
    Task,
    canonical_json,
    new_id,
    normalise_json_value,
)
from .repository import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from .state import validate_artifact_transition
from ._lifecycle_base import (
    _ARTIFACT_ACTIVE,
    ArtifactIntegrityError,
    ArtifactRecord,
    LifecycleConflictError,
    LifecycleRecordNotFoundError,
    LifecycleRepositoryBase,
    _digest,
    _identifier,
    _normalise_reference,
    _require_provenance,
    _resolve_workspace_file,
    _timestamp,
)


class ArtifactRepositoryMixin(LifecycleRepositoryBase):
    @staticmethod
    def _reject_bound_artifact_access(task: Task) -> None:
        if task.workspace is not None:
            raise ArtifactIntegrityError(
                "Task-bound Artifact file access requires an OS-isolated Workspace"
            )

    def register_file_artifact(
        self,
        *,
        task_id: str,
        producer_agent_id: str,
        workspace_root: str | Path,
        reference: str,
        artifact_type: str,
        provenance: Mapping[str, Any],
        idempotency_key: str,
        claim_token: str,
        expected_task_version: int,
        expected_digest: str | None = None,
        expected_previous_digest: str | None = None,
        supersedes_artifact_id: str | None = None,
        handoff_id: str | None = None,
        sensitivity: Sensitivity = Sensitivity.PROJECT,
        retention: str = "task_lifetime",
        idempotency_scope: str = "artifact",
        artifact_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[ArtifactRecord, Task, bool]:
        self.initialize()
        now_iso = _timestamp(now)
        normalized_reference = _normalise_reference(reference)
        task = self.get_task(task_id)
        if task is None:
            raise LifecycleRecordNotFoundError(f"Task not found: {task_id}")
        self._reject_bound_artifact_access(task)
        resolved = _resolve_workspace_file(workspace_root, normalized_reference)
        digest = Artifact.digest_file(resolved)
        if expected_digest is not None and expected_digest != digest:
            raise ArtifactIntegrityError("materialized Artifact digest does not match expected digest")
        if sensitivity not in {Sensitivity.PUBLIC, Sensitivity.PROJECT}:
            raise ArtifactIntegrityError("general Workspace Artifact registration accepts only public/project sensitivity")
        normalized_provenance = _require_provenance(provenance)
        producer_agent_id = _identifier(producer_agent_id, "producer_agent_id")
        artifact_type = _identifier(artifact_type, "artifact_type")
        retention = _identifier(retention, "retention")
        if handoff_id is not None:
            handoff_id = _identifier(handoff_id, "handoff_id")
        if supersedes_artifact_id is not None:
            supersedes_artifact_id = _identifier(supersedes_artifact_id, "supersedes_artifact_id")
        scope = _identifier(idempotency_scope, "idempotency_scope")
        key = _identifier(idempotency_key, "idempotency_key")
        candidate = Artifact(
            artifact_id=artifact_id or new_id("artifact"),
            task_id=task_id,
            producer_agent_id=producer_agent_id,
            artifact_type=artifact_type,
            reference=normalized_reference,
            digest=digest,
            provenance=normalized_provenance,
            validation_status=ArtifactStatus.MATERIALIZED,
            sensitivity=sensitivity,
            retention=retention,
            created_at=now_iso,
        )
        request = {
            "task_id": candidate.task_id,
            "producer_agent_id": candidate.producer_agent_id,
            "handoff_id": handoff_id,
            "artifact_type": candidate.artifact_type,
            "reference": candidate.reference,
            "digest": candidate.digest,
            "provenance": candidate.provenance,
            "sensitivity": candidate.sensitivity.value,
            "retention": candidate.retention,
            "supersedes_artifact_id": supersedes_artifact_id,
            "expected_previous_digest": expected_previous_digest,
        }
        request_digest = _digest(request)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT * FROM kernel_artifacts WHERE idempotency_scope=? AND idempotency_key=?",
                    (scope, key),
                ).fetchone()
                if replay is not None:
                    if replay["request_digest"] != request_digest:
                        raise IdempotencyConflictError("Artifact idempotency key was reused for a different request")
                    task = self._task_in_tx(connection, candidate.task_id)
                    connection.commit()
                    return self._row_to_artifact(replay), task, False
                task = self._task_in_tx(connection, candidate.task_id)
                if task.record_version != expected_task_version:
                    raise ConcurrencyConflictError("stale Task version")
                self._claim_is_active(task, claim_token, candidate.producer_agent_id, now_iso)
                if handoff_id is not None:
                    handoff_row = connection.execute(
                        "SELECT * FROM kernel_handoffs WHERE handoff_id=?", (handoff_id,)
                    ).fetchone()
                    if handoff_row is None:
                        raise LifecycleRecordNotFoundError(f"Handoff not found: {handoff_id}")
                    handoff = self._row_to_handoff(handoff_row)
                    if handoff.task_id != task.task_id or handoff.target_agent_id != candidate.producer_agent_id:
                        raise LifecycleConflictError("Artifact producer does not match its Handoff")
                    if handoff.status not in {HandoffStatus.CONSUMED, HandoffStatus.COMPLETED}:
                        raise LifecycleConflictError("Artifact Handoff has not been consumed")
                active_row = connection.execute(
                    """SELECT * FROM kernel_artifacts
                       WHERE task_id=? AND reference=? AND validation_status IN (?,?,?,?,?)
                       ORDER BY created_at DESC LIMIT 1""",
                    (task.task_id, candidate.reference, *_ARTIFACT_ACTIVE),
                ).fetchone()
                if active_row is not None:
                    active = self._row_to_artifact(active_row)
                    if active.digest == candidate.digest:
                        raise LifecycleConflictError("Artifact bytes are already registered under another identity")
                    if supersedes_artifact_id != active.artifact_id:
                        raise ArtifactIntegrityError("changed bytes require explicit supersedes_artifact_id")
                    if expected_previous_digest != active.digest:
                        raise ArtifactIntegrityError("Artifact previous digest precondition failed")
                    validate_artifact_transition(active.validation_status, ArtifactStatus.SUPERSEDED)
                    cursor = connection.execute(
                        """UPDATE kernel_artifacts SET validation_status=?, validation_json=?, updated_at=?, record_version=?
                           WHERE artifact_id=? AND record_version=?""",
                        (
                            ArtifactStatus.SUPERSEDED.value,
                            canonical_json({"superseded_by": candidate.artifact_id}),
                            now_iso,
                            active.record_version + 1,
                            active.artifact_id,
                            active.record_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConcurrencyConflictError("superseded Artifact changed during compare-and-set")
                    self._audit(
                        connection,
                        task_id=task.task_id,
                        record_type="artifact",
                        record_id=active.artifact_id,
                        event="superseded",
                        state=ArtifactStatus.SUPERSEDED.value,
                        payload={"superseded_by": candidate.artifact_id},
                        created_at=now_iso,
                    )
                elif supersedes_artifact_id is not None or expected_previous_digest is not None:
                    raise ArtifactIntegrityError("Artifact supersession precondition has no active predecessor")
                connection.execute(
                    """INSERT INTO kernel_artifacts (
                        artifact_id, task_id, producer_agent_id, handoff_id, artifact_type,
                        reference, digest, provenance_json, validation_status, validation_json,
                        sensitivity, retention, supersedes_artifact_id, idempotency_scope,
                        idempotency_key, request_digest, created_at, updated_at, record_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                    (
                        candidate.artifact_id,
                        candidate.task_id,
                        candidate.producer_agent_id,
                        handoff_id,
                        candidate.artifact_type,
                        candidate.reference,
                        candidate.digest,
                        canonical_json(candidate.provenance),
                        ArtifactStatus.MATERIALIZED.value,
                        canonical_json({}),
                        candidate.sensitivity.value,
                        candidate.retention,
                        supersedes_artifact_id,
                        scope,
                        key,
                        request_digest,
                        now_iso,
                        now_iso,
                    ),
                )
                next_task = replace(
                    task,
                    artifact_ids=tuple((*task.artifact_ids, candidate.artifact_id)),
                    current_step=candidate.artifact_id,
                    updated_at=now_iso,
                    record_version=task.record_version + 1,
                )
                self._update_task(connection, next_task, task.record_version)
                self._insert_checkpoint(
                    connection,
                    next_task,
                    payload={"event": "artifact_materialized", "artifact_id": candidate.artifact_id, "digest": digest},
                )
                self._audit(
                    connection,
                    task_id=task.task_id,
                    record_type="artifact",
                    record_id=candidate.artifact_id,
                    event="materialized",
                    state=ArtifactStatus.MATERIALIZED.value,
                    payload={"reference": candidate.reference, "digest": digest, "run_id": task.run_id},
                    created_at=now_iso,
                )
                row = connection.execute("SELECT * FROM kernel_artifacts WHERE artifact_id=?", (candidate.artifact_id,)).fetchone()
                connection.commit()
                assert row is not None
                return self._row_to_artifact(row), next_task, True
            except Exception:
                connection.rollback()
                raise

    def transition_artifact(
        self,
        artifact_id: str,
        target_status: ArtifactStatus,
        *,
        expected_version: int,
        workspace_root: str | Path,
        validator_agent_id: str,
        validation: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ArtifactRecord:
        self.initialize()
        artifact_id = _identifier(artifact_id, "artifact_id")
        now_iso = _timestamp(now)
        validator = _identifier(validator_agent_id, "validator_agent_id")
        if target_status is ArtifactStatus.SUPERSEDED:
            raise KernelContractError("Artifact supersession must name and register a replacement Artifact")
        details = normalise_json_value(validation or {}, field_name="artifact validation")
        if target_status in {ArtifactStatus.ACCEPTED, ArtifactStatus.DEGRADED} and not details:
            raise KernelContractError("accepted/degraded Artifact requires validation evidence")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                if row is None:
                    raise LifecycleRecordNotFoundError(f"Artifact not found: {artifact_id}")
                current = self._row_to_artifact(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale Artifact version")
                validate_artifact_transition(current.validation_status, target_status)
                if target_status not in {ArtifactStatus.INVALIDATED, ArtifactStatus.SUPERSEDED}:
                    task = self._task_in_tx(connection, current.task_id)
                    self._reject_bound_artifact_access(task)
                    path = _resolve_workspace_file(workspace_root, current.reference)
                    actual_digest = Artifact.digest_file(path)
                    if actual_digest != current.digest:
                        raise ArtifactIntegrityError("Artifact bytes changed after materialization")
                payload = {"validator_agent_id": validator, **dict(details)}
                cursor = connection.execute(
                    """UPDATE kernel_artifacts SET validation_status=?, validation_json=?, updated_at=?, record_version=?
                       WHERE artifact_id=? AND record_version=?""",
                    (
                        target_status.value,
                        canonical_json(payload),
                        now_iso,
                        current.record_version + 1,
                        artifact_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyConflictError("Artifact changed during compare-and-set")
                self._audit(
                    connection,
                    task_id=current.task_id,
                    record_type="artifact",
                    record_id=artifact_id,
                    event=target_status.value,
                    state=target_status.value,
                    payload=payload,
                    created_at=now_iso,
                )
                updated = connection.execute("SELECT * FROM kernel_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
                connection.commit()
                assert updated is not None
                return self._row_to_artifact(updated)
            except Exception:
                connection.rollback()
                raise

    def verify_artifact(self, artifact_id: str, *, workspace_root: str | Path) -> bool:
        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            raise LifecycleRecordNotFoundError(f"Artifact not found: {artifact_id}")
        task = self.get_task(artifact.task_id)
        if task is None:
            raise LifecycleRecordNotFoundError(f"Task not found: {artifact.task_id}")
        self._reject_bound_artifact_access(task)
        path = _resolve_workspace_file(workspace_root, artifact.reference)
        return Artifact.digest_file(path) == artifact.digest

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        self.initialize()
        artifact_id = _identifier(artifact_id, "artifact_id")
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM kernel_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
            return self._row_to_artifact(row) if row is not None else None

    def list_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        self.initialize()
        task_id = _identifier(task_id, "task_id")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM kernel_artifacts WHERE task_id=? ORDER BY created_at, artifact_id",
                (task_id,),
            ).fetchall()
        return [self._row_to_artifact(row) for row in rows]
