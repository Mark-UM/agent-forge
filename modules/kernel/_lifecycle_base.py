"""Slice 3 Handoff, Artifact, Approval, and append-audited lifecycle authority."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any, Mapping

from .contracts import (
    ApprovalStatus,
    ArtifactStatus,
    BudgetLimit,
    FailureInfo,
    HandoffStatus,
    KernelContractError,
    Sensitivity,
    Task,
    TaskStatus,
    canonical_json,
    new_id,
    normalise_json_value,
    normalise_utc_timestamp,
    utc_now_iso,
)
from .repository import (
    ClaimConflictError,
    KernelRepositoryError,
    TaskNotFoundError,
    TaskRepository,
)
from .state import (
    validate_task_transition,
)

RECORDS_SCHEMA_VERSION = 1
_RECORD_SCHEMA_STATEMENTS = (
    """CREATE TABLE kernel_records_schema_migrations (
        version INTEGER PRIMARY KEY,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )""",
    """CREATE TABLE kernel_handoffs (
        handoff_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        source_agent_id TEXT NOT NULL,
        target_agent_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        bounded_context_json TEXT NOT NULL,
        expected_artifact TEXT NOT NULL,
        acceptance_criteria_json TEXT NOT NULL,
        transferred_budget_json TEXT NOT NULL,
        status TEXT NOT NULL,
        idempotency_scope TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        consume_idempotency_key TEXT,
        consume_digest TEXT,
        outcome_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        accepted_at TEXT,
        consumed_at TEXT,
        finished_at TEXT,
        record_version INTEGER NOT NULL,
        FOREIGN KEY(task_id) REFERENCES kernel_tasks(task_id) ON DELETE CASCADE,
        UNIQUE(idempotency_scope, idempotency_key)
    )""",
    "CREATE INDEX idx_kernel_handoffs_task_status ON kernel_handoffs(task_id, status)",
    """CREATE TABLE kernel_artifacts (
        artifact_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        producer_agent_id TEXT NOT NULL,
        handoff_id TEXT,
        artifact_type TEXT NOT NULL,
        reference TEXT NOT NULL,
        digest TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        validation_json TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        retention TEXT NOT NULL,
        supersedes_artifact_id TEXT,
        idempotency_scope TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        record_version INTEGER NOT NULL,
        FOREIGN KEY(task_id) REFERENCES kernel_tasks(task_id) ON DELETE CASCADE,
        FOREIGN KEY(handoff_id) REFERENCES kernel_handoffs(handoff_id),
        FOREIGN KEY(supersedes_artifact_id) REFERENCES kernel_artifacts(artifact_id),
        UNIQUE(idempotency_scope, idempotency_key)
    )""",
    "CREATE INDEX idx_kernel_artifacts_task_reference ON kernel_artifacts(task_id, reference, created_at)",
    """CREATE TABLE kernel_approvals (
        approval_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        requested_action_json TEXT NOT NULL,
        action_digest TEXT NOT NULL,
        reason TEXT NOT NULL,
        scope_json TEXT NOT NULL,
        requester_agent_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        decision_maker TEXT,
        requested_at TEXT NOT NULL,
        decided_at TEXT,
        expires_at TEXT,
        consumed_at TEXT,
        audit_reference TEXT,
        idempotency_scope TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        consume_idempotency_key TEXT,
        consume_digest TEXT,
        record_version INTEGER NOT NULL,
        FOREIGN KEY(task_id) REFERENCES kernel_tasks(task_id) ON DELETE CASCADE,
        UNIQUE(idempotency_scope, idempotency_key)
    )""",
    "CREATE INDEX idx_kernel_approvals_task_decision ON kernel_approvals(task_id, decision)",
    """CREATE TABLE kernel_domain_audit (
        audit_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        record_id TEXT NOT NULL,
        event TEXT NOT NULL,
        state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES kernel_tasks(task_id) ON DELETE CASCADE
    )""",
    "CREATE INDEX idx_kernel_domain_audit_record ON kernel_domain_audit(record_type, record_id, created_at)",
)
_RECORD_SCHEMA_CANONICAL = "\n-- statement --\n".join(statement.strip() for statement in _RECORD_SCHEMA_STATEMENTS)
_RECORD_SCHEMA_CHECKSUM = hashlib.sha256(_RECORD_SCHEMA_CANONICAL.encode("utf-8")).hexdigest()
_EXPECTED_RECORD_TABLE_COLUMNS = {
    "kernel_records_schema_migrations": {"version", "checksum", "applied_at"},
    "kernel_handoffs": {
        "handoff_id", "task_id", "source_agent_id", "target_agent_id", "reason",
        "bounded_context_json", "expected_artifact", "acceptance_criteria_json",
        "transferred_budget_json", "status", "idempotency_scope", "idempotency_key",
        "request_digest", "consume_idempotency_key", "consume_digest", "outcome_json",
        "created_at", "expires_at", "accepted_at", "consumed_at", "finished_at",
        "record_version",
    },
    "kernel_artifacts": {
        "artifact_id", "task_id", "producer_agent_id", "handoff_id", "artifact_type",
        "reference", "digest", "provenance_json", "validation_status", "validation_json",
        "sensitivity", "retention", "supersedes_artifact_id", "idempotency_scope",
        "idempotency_key", "request_digest", "created_at", "updated_at", "record_version",
    },
    "kernel_approvals": {
        "approval_id", "task_id", "requested_action_json", "action_digest", "reason",
        "scope_json", "requester_agent_id", "decision", "decision_maker", "requested_at",
        "decided_at", "expires_at", "consumed_at", "audit_reference", "idempotency_scope",
        "idempotency_key", "request_digest", "consume_idempotency_key", "consume_digest",
        "record_version",
    },
    "kernel_domain_audit": {
        "audit_id", "task_id", "record_type", "record_id", "event", "state",
        "payload_json", "created_at",
    },
}

_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
_APPROVAL_SCOPE_KEYS = frozenset(
    {
        "workspace_write",
        "network_write",
        "git_write",
        "publication",
        "private_memory",
        "credential_use",
        "subprocess",
    }
)
_ARTIFACT_ACTIVE = (
    ArtifactStatus.DECLARED.value,
    ArtifactStatus.MATERIALIZED.value,
    ArtifactStatus.VALIDATING.value,
    ArtifactStatus.ACCEPTED.value,
    ArtifactStatus.DEGRADED.value,
)


class LifecycleRepositoryError(KernelRepositoryError):
    """Base error for Slice 3 authority operations."""


class LifecycleRecordNotFoundError(LifecycleRepositoryError):
    pass


class LifecycleConflictError(LifecycleRepositoryError):
    pass


class ArtifactIntegrityError(LifecycleRepositoryError):
    pass


class ApprovalAuthorizationError(LifecycleRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class HandoffRecord:
    handoff_id: str
    task_id: str
    source_agent_id: str
    target_agent_id: str
    reason: str
    bounded_context: Mapping[str, Any]
    expected_artifact: str
    acceptance_criteria: tuple[str, ...]
    transferred_budget: BudgetLimit
    status: HandoffStatus
    idempotency_scope: str
    idempotency_key: str
    outcome: Mapping[str, Any]
    created_at: str
    expires_at: str | None
    accepted_at: str | None
    consumed_at: str | None
    finished_at: str | None
    record_version: int


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    task_id: str
    producer_agent_id: str
    handoff_id: str | None
    artifact_type: str
    reference: str
    digest: str
    provenance: Mapping[str, Any]
    validation_status: ArtifactStatus
    validation: Mapping[str, Any]
    sensitivity: Sensitivity
    retention: str
    supersedes_artifact_id: str | None
    idempotency_scope: str
    idempotency_key: str
    created_at: str
    updated_at: str
    record_version: int


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    task_id: str
    requested_action: Mapping[str, Any]
    action_digest: str
    reason: str
    scope: Mapping[str, Any]
    requester_agent_id: str
    decision: ApprovalStatus
    decision_maker: str | None
    requested_at: str
    decided_at: str | None
    expires_at: str | None
    consumed_at: str | None
    audit_reference: str | None
    idempotency_scope: str
    idempotency_key: str
    record_version: int


@dataclass(frozen=True, slots=True)
class ApprovalAuthorization:
    approval_id: str
    task_id: str
    requester_agent_id: str
    requested_action: Mapping[str, Any]
    scope: Mapping[str, Any]
    action_digest: str
    audit_reference: str
    consumed_at: str


@dataclass(frozen=True, slots=True)
class DomainAuditEvent:
    audit_id: str
    task_id: str
    record_type: str
    record_id: str
    event: str
    state: str
    payload: Mapping[str, Any]
    created_at: str


def _identifier(value: str, field_name: str) -> str:
    normalized = normalise_json_value(value, field_name=field_name)
    if not isinstance(normalized, str) or not _IDENTIFIER_RE.fullmatch(normalized.strip()):
        raise KernelContractError(f"{field_name} has invalid characters or length")
    return normalized.strip()


def _bounded_text(value: str, field_name: str, limit: int = 4_000) -> str:
    normalized = normalise_json_value(value, field_name=field_name)
    if not isinstance(normalized, str):
        raise KernelContractError(f"{field_name} must be a string")
    normalized = normalized.strip()
    if not normalized or len(normalized) > limit:
        raise KernelContractError(f"{field_name} is empty or too long")
    return normalized


def _timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        return utc_now_iso()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise KernelContractError("timestamp must include a timezone")
        return value.astimezone(timezone.utc).isoformat()
    result = normalise_utc_timestamp(value, field_name="timestamp", allow_none=False)
    assert result is not None
    return result


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _budget_dict(value: BudgetLimit) -> dict[str, int]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def _remaining_budget(task: Task) -> BudgetLimit:
    return BudgetLimit(
        **{
            name: getattr(task.budget, name) - getattr(task.budget_used, name)
            for name in task.budget.__dataclass_fields__
        }
    )


def _validate_lease_seconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86_400:
        raise KernelContractError("lease_seconds must be an integer between 1 and 86400")
    return value


def _protected_workspace_target(parts: tuple[str, ...]) -> str | None:
    folded = tuple(part.casefold() for part in parts)
    if ".git" in folded:
        return ".git"
    if any(first == "_runtime" and second == "kernel"
           for first, second in zip(folded, folded[1:])):
        return "Kernel authority storage"
    return None


def _normalise_reference(reference: str) -> str:
    if not isinstance(reference, str):
        raise KernelContractError("artifact reference must be a string")
    normalized = normalise_json_value(reference, field_name="artifact reference")
    assert isinstance(normalized, str)
    raw = normalized.replace("\\", "/").strip()
    path = PurePosixPath(raw)
    parts = path.parts
    if (
        not raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any("\x00" in part for part in parts)
    ):
        raise KernelContractError("artifact reference must be relative and non-escaping")
    protected = _protected_workspace_target(parts)
    if protected is not None:
        raise KernelContractError(f"artifact reference cannot target {protected}")
    return path.as_posix()


def _resolve_workspace_file(workspace_root: str | Path, reference: str) -> Path:
    root = Path(workspace_root).resolve(strict=True)
    if not root.is_dir():
        raise ArtifactIntegrityError("workspace root is not a directory")
    candidate = (root / reference).resolve(strict=True)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactIntegrityError("artifact path escapes the assigned Workspace") from exc
    protected = _protected_workspace_target(candidate.parts)
    if protected is not None:
        raise ArtifactIntegrityError(f"artifact path targets protected {protected}")
    if not candidate.is_file():
        raise ArtifactIntegrityError("artifact path is not a regular file")
    return candidate


def _require_provenance(value: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = normalise_json_value(value, field_name="provenance")
    if not isinstance(normalized, Mapping):
        raise KernelContractError("provenance must be an object")
    required = {"source_inputs", "step_refs", "producer_version"}
    if not required.issubset(normalized):
        raise KernelContractError("provenance requires source_inputs, step_refs, and producer_version")
    if not isinstance(normalized["source_inputs"], list) or not isinstance(normalized["step_refs"], list):
        raise KernelContractError("provenance source_inputs and step_refs must be lists")
    _identifier(str(normalized["producer_version"]), "producer_version")
    return normalized


class LifecycleRepositoryBase(TaskRepository):
    """Extends TaskRepository with one SQLite authority for Slice 3 records."""

    def initialize(self) -> None:
        super().initialize()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kernel_records_schema_migrations'"
                ).fetchone()
                if not exists:
                    for statement in _RECORD_SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO kernel_records_schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                        (RECORDS_SCHEMA_VERSION, _RECORD_SCHEMA_CHECKSUM, utc_now_iso()),
                    )
                else:
                    rows = connection.execute(
                        "SELECT version, checksum FROM kernel_records_schema_migrations ORDER BY version"
                    ).fetchall()
                    if not rows:
                        raise LifecycleRepositoryError("Kernel records migration ledger is empty")
                    if int(rows[-1]["version"]) > RECORDS_SCHEMA_VERSION:
                        raise LifecycleRepositoryError("Kernel records schema is newer than this code")
                    if len(rows) != 1:
                        raise LifecycleRepositoryError("Kernel records migration ledger is inconsistent")
                    row = rows[0]
                    if int(row["version"]) != RECORDS_SCHEMA_VERSION or row["checksum"] != _RECORD_SCHEMA_CHECKSUM:
                        raise LifecycleRepositoryError("Kernel records schema version/checksum mismatch")
                self._verify_records_schema(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _verify_records_schema(connection: sqlite3.Connection) -> None:
        for table, expected_columns in _EXPECTED_RECORD_TABLE_COLUMNS.items():
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists is None:
                raise LifecycleRepositoryError(f"Kernel records schema is missing table {table}")
            actual_columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if actual_columns != expected_columns:
                raise LifecycleRepositoryError(f"Kernel records schema columns mismatch for {table}")

    @staticmethod
    def _load(raw: str | None, fallback: Any) -> Any:
        return json.loads(raw) if raw else fallback

    def _row_to_handoff(self, row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row["handoff_id"],
            task_id=row["task_id"],
            source_agent_id=row["source_agent_id"],
            target_agent_id=row["target_agent_id"],
            reason=row["reason"],
            bounded_context=self._load(row["bounded_context_json"], {}),
            expected_artifact=row["expected_artifact"],
            acceptance_criteria=tuple(self._load(row["acceptance_criteria_json"], [])),
            transferred_budget=BudgetLimit(**self._load(row["transferred_budget_json"], {})),
            status=HandoffStatus(row["status"]),
            idempotency_scope=row["idempotency_scope"],
            idempotency_key=row["idempotency_key"],
            outcome=self._load(row["outcome_json"], {}),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            accepted_at=row["accepted_at"],
            consumed_at=row["consumed_at"],
            finished_at=row["finished_at"],
            record_version=int(row["record_version"]),
        )

    def _row_to_artifact(self, row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            task_id=row["task_id"],
            producer_agent_id=row["producer_agent_id"],
            handoff_id=row["handoff_id"],
            artifact_type=row["artifact_type"],
            reference=row["reference"],
            digest=row["digest"],
            provenance=self._load(row["provenance_json"], {}),
            validation_status=ArtifactStatus(row["validation_status"]),
            validation=self._load(row["validation_json"], {}),
            sensitivity=Sensitivity(row["sensitivity"]),
            retention=row["retention"],
            supersedes_artifact_id=row["supersedes_artifact_id"],
            idempotency_scope=row["idempotency_scope"],
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            record_version=int(row["record_version"]),
        )

    def _row_to_approval(self, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row["approval_id"],
            task_id=row["task_id"],
            requested_action=self._load(row["requested_action_json"], {}),
            action_digest=row["action_digest"],
            reason=row["reason"],
            scope=self._load(row["scope_json"], {}),
            requester_agent_id=row["requester_agent_id"],
            decision=ApprovalStatus(row["decision"]),
            decision_maker=row["decision_maker"],
            requested_at=row["requested_at"],
            decided_at=row["decided_at"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            audit_reference=row["audit_reference"],
            idempotency_scope=row["idempotency_scope"],
            idempotency_key=row["idempotency_key"],
            record_version=int(row["record_version"]),
        )

    def _task_in_tx(self, connection: sqlite3.Connection, task_id: str) -> Task:
        row = connection.execute("SELECT * FROM kernel_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return self._row_to_task(row)

    @staticmethod
    def _claim_is_active(task: Task, claim_token: str, owner_agent_id: str, now_iso: str) -> None:
        if task.status is not TaskStatus.RUNNING or task.owner_agent_id != owner_agent_id:
            raise ClaimConflictError("Agent does not own a running Task")
        if task.claim_owner != owner_agent_id or task.claim_token != claim_token or not task.claim_expires_at:
            raise ClaimConflictError("active Task claim is required")
        if datetime.fromisoformat(task.claim_expires_at) <= datetime.fromisoformat(now_iso):
            raise ClaimConflictError("Task claim expired and requires reconciliation")

    def _pause_task(
        self,
        connection: sqlite3.Connection,
        task: Task,
        *,
        target_status: TaskStatus,
        current_step: str,
        event: str,
        reference_id: str,
        now_iso: str,
        artifact_ids: tuple[str, ...] | None = None,
        approval_ids: tuple[str, ...] | None = None,
    ) -> Task:
        validate_task_transition(task.status, target_status)
        next_task = replace(
            task,
            status=target_status,
            current_step=current_step,
            artifact_ids=artifact_ids if artifact_ids is not None else task.artifact_ids,
            approval_ids=approval_ids if approval_ids is not None else task.approval_ids,
            updated_at=now_iso,
            record_version=task.record_version + 1,
            claim_owner=None,
            claim_token=None,
            claim_expires_at=None,
        )
        self._update_task(connection, next_task, task.record_version)
        self._insert_checkpoint(
            connection,
            next_task,
            payload={"event": event, "record_id": reference_id},
        )
        return next_task

    def _resume_task(
        self,
        connection: sqlite3.Connection,
        task: Task,
        *,
        owner_agent_id: str,
        current_step: str,
        event: str,
        reference_id: str,
        now_iso: str,
        lease_seconds: int,
    ) -> Task:
        validate_task_transition(task.status, TaskStatus.RUNNING)
        claim_token = new_id("claim")
        next_task = replace(
            task,
            status=TaskStatus.RUNNING,
            owner_agent_id=owner_agent_id,
            current_step=current_step,
            updated_at=now_iso,
            record_version=task.record_version + 1,
            claim_owner=owner_agent_id,
            claim_token=claim_token,
            claim_expires_at=(datetime.fromisoformat(now_iso) + timedelta(seconds=lease_seconds)).isoformat(),
        )
        self._update_task(connection, next_task, task.record_version)
        self._insert_checkpoint(
            connection,
            next_task,
            payload={"event": event, "record_id": reference_id},
        )
        return next_task

    def _finish_task(
        self,
        connection: sqlite3.Connection,
        task: Task,
        *,
        target_status: TaskStatus,
        failure: FailureInfo,
        event: str,
        reference_id: str,
        now_iso: str,
    ) -> Task:
        validate_task_transition(task.status, target_status)
        next_task = replace(
            task,
            status=target_status,
            failure=failure,
            updated_at=now_iso,
            finished_at=now_iso,
            record_version=task.record_version + 1,
            claim_owner=None,
            claim_token=None,
            claim_expires_at=None,
        )
        self._update_task(connection, next_task, task.record_version)
        self._insert_checkpoint(
            connection,
            next_task,
            payload={"event": event, "record_id": reference_id},
        )
        return next_task

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        record_type: str,
        record_id: str,
        event: str,
        state: str,
        payload: Mapping[str, Any] | None = None,
        created_at: str,
    ) -> None:
        connection.execute(
            """INSERT INTO kernel_domain_audit
               (audit_id, task_id, record_type, record_id, event, state, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id("audit"),
                task_id,
                _identifier(record_type, "record_type"),
                _identifier(record_id, "record_id"),
                _identifier(event, "event"),
                _identifier(state, "state"),
                canonical_json(payload or {}),
                created_at,
            ),
        )


    def list_audit_events(
        self,
        *,
        record_type: str | None = None,
        record_id: str | None = None,
        task_id: str | None = None,
    ) -> list[DomainAuditEvent]:
        self.initialize()
        clauses: list[str] = []
        values: list[str] = []
        for column, value in (("record_type", record_type), ("record_id", record_id), ("task_id", task_id)):
            if value is not None:
                clauses.append(f"{column}=?")
                values.append(_identifier(value, column))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM kernel_domain_audit" + where + " ORDER BY created_at, audit_id",
                tuple(values),
            ).fetchall()
        return [
            DomainAuditEvent(
                audit_id=row["audit_id"],
                task_id=row["task_id"],
                record_type=row["record_type"],
                record_id=row["record_id"],
                event=row["event"],
                state=row["state"],
                payload=self._load(row["payload_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        ]
