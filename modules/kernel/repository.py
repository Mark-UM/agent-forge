"""SQLite Task/checkpoint authority for Agent Forge 2.0 Slice 1."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .contracts import (
    BudgetLimit,
    BudgetUsage,
    DegradedInfo,
    FailureCategory,
    FailureInfo,
    Task,
    TaskStatus,
    WorkspaceBinding,
    canonical_json,
    new_id,
    normalise_json_value,
    utc_now_iso,
)
from .state import TASK_TERMINAL, InvalidTransitionError, validate_task_transition

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "_runtime" / "kernel" / "kernel.db"
SCHEMA_VERSION = 2

_SCHEMA_SQL = """
CREATE TABLE kernel_schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE kernel_tasks (
    task_id TEXT PRIMARY KEY,
    parent_task_id TEXT,
    objective TEXT NOT NULL,
    normalized_input_json TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_agent_id TEXT,
    required_capabilities_json TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    budget_used_json TEXT NOT NULL,
    current_step TEXT,
    artifact_ids_json TEXT NOT NULL,
    approval_ids_json TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    failure_json TEXT,
    degraded_json TEXT,
    idempotency_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    record_version INTEGER NOT NULL,
    claim_owner TEXT,
    claim_token TEXT,
    claim_expires_at TEXT,
    FOREIGN KEY(parent_task_id) REFERENCES kernel_tasks(task_id),
    UNIQUE(idempotency_scope, idempotency_key)
);
CREATE INDEX idx_kernel_tasks_status_created ON kernel_tasks(status, created_at);
CREATE INDEX idx_kernel_tasks_parent ON kernel_tasks(parent_task_id);
CREATE TABLE kernel_task_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    task_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, task_version),
    FOREIGN KEY(task_id) REFERENCES kernel_tasks(task_id) ON DELETE CASCADE
);
CREATE INDEX idx_kernel_checkpoints_task ON kernel_task_checkpoints(task_id, task_version);
""".strip()
_SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()
_WORKSPACE_MIGRATION_SQL = "ALTER TABLE kernel_tasks ADD COLUMN workspace_json TEXT"
_WORKSPACE_MIGRATION_CHECKSUM = hashlib.sha256(
    _WORKSPACE_MIGRATION_SQL.encode("utf-8")
).hexdigest()


class KernelRepositoryError(RuntimeError):
    pass


class TaskNotFoundError(KernelRepositoryError):
    pass


class IdempotencyConflictError(KernelRepositoryError):
    pass


class ConcurrencyConflictError(KernelRepositoryError):
    pass


class ClaimConflictError(KernelRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class TaskCheckpoint:
    checkpoint_id: str
    task_id: str
    task_version: int
    status: TaskStatus
    snapshot: Mapping[str, Any]
    payload: Mapping[str, Any]
    created_at: str


class TaskRepository:
    """Only first-party writer for Kernel Task/checkpoint tables."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kernel_schema_migrations'"
                ).fetchone()
                if not exists:
                    for statement in _SCHEMA_SQL.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(
                        "INSERT INTO kernel_schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                        (1, _SCHEMA_CHECKSUM, utc_now_iso()),
                    )
                rows = connection.execute(
                    "SELECT version, checksum FROM kernel_schema_migrations ORDER BY version"
                ).fetchall()
                if not rows:
                    raise KernelRepositoryError("kernel migration ledger is empty")
                versions = [(int(row["version"]), row["checksum"]) for row in rows]
                expected = [(1, _SCHEMA_CHECKSUM), (2, _WORKSPACE_MIGRATION_CHECKSUM)]
                if versions[-1][0] > SCHEMA_VERSION:
                    raise KernelRepositoryError("kernel database schema is newer than this code")
                if versions != expected[: len(versions)]:
                    raise KernelRepositoryError("kernel schema version/checksum mismatch")
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(kernel_tasks)")
                }
                if "task_id" not in columns or (len(versions) == 1 and "workspace_json" in columns):
                    raise KernelRepositoryError("kernel task schema does not match migration ledger")
                if len(versions) == 1:
                    connection.execute(_WORKSPACE_MIGRATION_SQL)
                    connection.execute(
                        "INSERT INTO kernel_schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                        (2, _WORKSPACE_MIGRATION_CHECKSUM, utc_now_iso()),
                    )
                elif "workspace_json" not in columns:
                    raise KernelRepositoryError("kernel task schema does not match migration ledger")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _budget_dict(value: BudgetLimit | BudgetUsage) -> dict[str, int]:
        return {name: getattr(value, name) for name in value.__dataclass_fields__}

    @staticmethod
    def _failure_dict(value: FailureInfo | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "category": value.category.value,
            "message": value.message,
            "retryable": value.retryable,
            "code": value.code,
            "details": dict(value.details),
        }

    @staticmethod
    def _degraded_dict(value: DegradedInfo | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {
            "summary": value.summary,
            "missing_capabilities": list(value.missing_capabilities),
            "details": dict(value.details),
        }

    @staticmethod
    def _loads(raw: str | None, fallback: Any) -> Any:
        return json.loads(raw) if raw else fallback

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        failure_raw = self._loads(row["failure_json"], None)
        degraded_raw = self._loads(row["degraded_json"], None)
        return Task(
            task_id=row["task_id"],
            parent_task_id=row["parent_task_id"],
            objective=row["objective"],
            normalized_input=self._loads(row["normalized_input_json"], {}),
            workspace=(
                WorkspaceBinding(**json.loads(row["workspace_json"]))
                if row["workspace_json"] else None
            ),
            status=TaskStatus(row["status"]),
            owner_agent_id=row["owner_agent_id"],
            required_capabilities=tuple(self._loads(row["required_capabilities_json"], [])),
            budget=BudgetLimit(**self._loads(row["budget_json"], {})),
            budget_used=BudgetUsage(**self._loads(row["budget_used_json"], {})),
            current_step=row["current_step"],
            artifact_ids=tuple(self._loads(row["artifact_ids_json"], [])),
            approval_ids=tuple(self._loads(row["approval_ids_json"], [])),
            run_id=row["run_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            failure=(
                FailureInfo(
                    category=FailureCategory(failure_raw["category"]),
                    message=failure_raw["message"],
                    retryable=bool(failure_raw.get("retryable", False)),
                    code=failure_raw.get("code"),
                    details=failure_raw.get("details", {}),
                )
                if failure_raw else None
            ),
            degraded=(
                DegradedInfo(
                    summary=degraded_raw["summary"],
                    missing_capabilities=tuple(degraded_raw.get("missing_capabilities", [])),
                    details=degraded_raw.get("details", {}),
                )
                if degraded_raw else None
            ),
            idempotency_scope=row["idempotency_scope"],
            idempotency_key=row["idempotency_key"],
            record_version=int(row["record_version"]),
            claim_owner=row["claim_owner"],
            claim_token=row["claim_token"],
            claim_expires_at=row["claim_expires_at"],
        )

    def _insert_task(self, connection: sqlite3.Connection, task: Task, request_digest: str) -> None:
        connection.execute(
            """INSERT INTO kernel_tasks (
                task_id, parent_task_id, objective, normalized_input_json, status,
                owner_agent_id, required_capabilities_json, budget_json, budget_used_json,
                current_step, artifact_ids_json, approval_ids_json, run_id, created_at,
                updated_at, started_at, finished_at, failure_json, degraded_json,
                idempotency_scope, idempotency_key, request_digest, record_version,
                claim_owner, claim_token, claim_expires_at, workspace_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task.task_id, task.parent_task_id, task.objective,
                canonical_json(task.normalized_input), task.status.value,
                task.owner_agent_id, canonical_json(list(task.required_capabilities)),
                canonical_json(self._budget_dict(task.budget)),
                canonical_json(self._budget_dict(task.budget_used)), task.current_step,
                canonical_json(list(task.artifact_ids)), canonical_json(list(task.approval_ids)),
                task.run_id, task.created_at, task.updated_at, task.started_at,
                task.finished_at, canonical_json(self._failure_dict(task.failure)) if task.failure else None,
                canonical_json(self._degraded_dict(task.degraded)) if task.degraded else None,
                task.idempotency_scope, task.idempotency_key, request_digest,
                task.record_version, task.claim_owner, task.claim_token, task.claim_expires_at,
                canonical_json(task.workspace.to_dict()) if task.workspace else None,
            ),
        )

    def _update_task(self, connection: sqlite3.Connection, task: Task, expected_version: int) -> None:
        values = (
            task.parent_task_id, task.objective, canonical_json(task.normalized_input),
            task.status.value, task.owner_agent_id,
            canonical_json(list(task.required_capabilities)),
            canonical_json(self._budget_dict(task.budget)),
            canonical_json(self._budget_dict(task.budget_used)), task.current_step,
            canonical_json(list(task.artifact_ids)), canonical_json(list(task.approval_ids)),
            task.run_id, task.updated_at, task.started_at, task.finished_at,
            canonical_json(self._failure_dict(task.failure)) if task.failure else None,
            canonical_json(self._degraded_dict(task.degraded)) if task.degraded else None,
            task.record_version, task.claim_owner, task.claim_token, task.claim_expires_at,
            canonical_json(task.workspace.to_dict()) if task.workspace else None,
            task.task_id, expected_version,
        )
        cursor = connection.execute(
            """UPDATE kernel_tasks SET
                parent_task_id=?, objective=?, normalized_input_json=?, status=?,
                owner_agent_id=?, required_capabilities_json=?, budget_json=?,
                budget_used_json=?, current_step=?, artifact_ids_json=?, approval_ids_json=?,
                run_id=?, updated_at=?, started_at=?, finished_at=?, failure_json=?,
                degraded_json=?, record_version=?, claim_owner=?, claim_token=?, claim_expires_at=?,
                workspace_json=?
               WHERE task_id=? AND record_version=?""",
            values,
        )
        if cursor.rowcount != 1:
            raise ConcurrencyConflictError("task changed during compare-and-set")

    def _insert_checkpoint(
        self,
        connection: sqlite3.Connection,
        task: Task,
        *,
        payload: Mapping[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            checkpoint_id=checkpoint_id or new_id("checkpoint"),
            task_id=task.task_id,
            task_version=task.record_version,
            status=task.status,
            snapshot={"task": task.to_dict(include_claim_token=False)},
            payload=normalise_json_value(payload or {}, field_name="checkpoint_payload"),
            created_at=utc_now_iso(),
        )
        connection.execute(
            """INSERT INTO kernel_task_checkpoints
               (checkpoint_id, task_id, task_version, status, snapshot_json, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                checkpoint.checkpoint_id, checkpoint.task_id, checkpoint.task_version,
                checkpoint.status.value, canonical_json(checkpoint.snapshot),
                canonical_json(checkpoint.payload), checkpoint.created_at,
            ),
        )
        return checkpoint

    def create_task(
        self,
        *,
        objective: str,
        normalized_input: Mapping[str, Any],
        idempotency_key: str,
        idempotency_scope: str = "default",
        parent_task_id: str | None = None,
        required_capabilities: tuple[str, ...] = (),
        budget: BudgetLimit | None = None,
        owner_agent_id: str | None = None,
        workspace: WorkspaceBinding | None = None,
        task_id: str | None = None,
    ) -> tuple[Task, bool]:
        if workspace is not None:
            if not isinstance(workspace, WorkspaceBinding):
                raise KernelRepositoryError("workspace must be WorkspaceBinding")
        candidate = Task(
            task_id=task_id or new_id("task"), objective=objective,
            normalized_input=normalized_input, parent_task_id=parent_task_id,
            required_capabilities=required_capabilities, budget=budget or BudgetLimit(),
            owner_agent_id=owner_agent_id, idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key, workspace=workspace,
        )
        request_payload = {
            "objective": candidate.objective,
            "normalized_input": candidate.normalized_input,
            "parent_task_id": candidate.parent_task_id,
            "required_capabilities": list(candidate.required_capabilities),
            "budget": self._budget_dict(candidate.budget),
            "owner_agent_id": candidate.owner_agent_id,
        }
        if candidate.workspace is not None:
            request_payload["workspace"] = candidate.workspace.to_dict()
        request_digest = hashlib.sha256(canonical_json(request_payload).encode("utf-8")).hexdigest()
        self.initialize()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM kernel_tasks WHERE idempotency_scope=? AND idempotency_key=?",
                    (candidate.idempotency_scope, candidate.idempotency_key),
                ).fetchone()
                if row:
                    if row["request_digest"] != request_digest:
                        raise IdempotencyConflictError("idempotency key was reused for a different request")
                    connection.commit()
                    return self._row_to_task(row), False
                if candidate.workspace is not None:
                    candidate.workspace.validate_current()
                self._insert_task(connection, candidate, request_digest)
                self._insert_checkpoint(connection, candidate, payload={"event": "created"})
                connection.commit()
                return candidate, True
            except Exception:
                connection.rollback()
                raise

    def get_task(self, task_id: str) -> Task | None:
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM kernel_tasks WHERE task_id=?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    @staticmethod
    def _resolve_run_id(current: Task, run_id: str | None) -> str | None:
        if run_id is None:
            return current.run_id
        candidate = replace(current, run_id=run_id).run_id
        if current.run_id is not None and current.run_id != candidate:
            raise ConcurrencyConflictError("task is already correlated to another run")
        return candidate

    def transition_task(
        self,
        task_id: str,
        target_status: TaskStatus,
        *,
        expected_version: int,
        owner_agent_id: str | None = None,
        current_step: str | None = None,
        budget_used: BudgetUsage | None = None,
        artifact_ids: tuple[str, ...] | None = None,
        approval_ids: tuple[str, ...] | None = None,
        failure: FailureInfo | None = None,
        degraded: DegradedInfo | None = None,
        run_id: str | None = None,
        checkpoint_payload: Mapping[str, Any] | None = None,
        checkpoint_id: str | None = None,
        claim_token: str | None = None,
        control_plane: bool = False,
    ) -> tuple[Task, TaskCheckpoint]:
        self.initialize()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_tasks WHERE task_id=?", (task_id,)).fetchone()
                if not row:
                    raise TaskNotFoundError(f"Task not found: {task_id}")
                current = self._row_to_task(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale task version")
                now = utc_now_iso()
                if current.claim_token and not control_plane:
                    if claim_token != current.claim_token:
                        raise ClaimConflictError("active claim token is required")
                    if not current.claim_expires_at or datetime.fromisoformat(current.claim_expires_at) <= datetime.fromisoformat(now):
                        raise ClaimConflictError("claim expired and requires reconciliation")
                validate_task_transition(current.status, target_status)
                clear_claim = target_status in TASK_TERMINAL or target_status in {
                    TaskStatus.WAITING_APPROVAL, TaskStatus.HANDOFF_PENDING, TaskStatus.REVIEWING,
                }
                next_task = replace(
                    current, status=target_status,
                    owner_agent_id=owner_agent_id or current.owner_agent_id,
                    current_step=current_step if current_step is not None else current.current_step,
                    budget_used=budget_used or current.budget_used,
                    artifact_ids=artifact_ids if artifact_ids is not None else current.artifact_ids,
                    approval_ids=approval_ids if approval_ids is not None else current.approval_ids,
                    run_id=self._resolve_run_id(current, run_id),
                    failure=failure, degraded=degraded, updated_at=now,
                    started_at=current.started_at or (now if target_status is TaskStatus.RUNNING else None),
                    finished_at=now if target_status in TASK_TERMINAL else None,
                    record_version=current.record_version + 1,
                    claim_owner=None if clear_claim else current.claim_owner,
                    claim_token=None if clear_claim else current.claim_token,
                    claim_expires_at=None if clear_claim else current.claim_expires_at,
                )
                self._update_task(connection, next_task, expected_version)
                checkpoint = self._insert_checkpoint(
                    connection, next_task, payload=checkpoint_payload, checkpoint_id=checkpoint_id,
                )
                connection.commit()
                return next_task, checkpoint
            except Exception:
                connection.rollback()
                raise

    def claim_task(
        self,
        task_id: str,
        *,
        claimant: str,
        expected_version: int,
        lease_seconds: int = 300,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Task, TaskCheckpoint]:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise ValueError("lease_seconds must be an integer")
        if not 1 <= lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        self.initialize()
        claimed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(self._connect()) as connection:
            # The claim and attempt ID must survive power loss before an external launch.
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_tasks WHERE task_id=?", (task_id,)).fetchone()
                if not row:
                    raise TaskNotFoundError(f"Task not found: {task_id}")
                current = self._row_to_task(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale task version")
                if current.claim_owner:
                    raise ClaimConflictError("task already has an active claim")
                try:
                    validate_task_transition(current.status, TaskStatus.RUNNING)
                except InvalidTransitionError as exc:
                    raise ClaimConflictError(f"task is not claimable from {current.status.value}") from exc
                now_iso = claimed_at.isoformat()
                correlated_run_id = self._resolve_run_id(current, run_id)
                next_task = replace(
                    current, status=TaskStatus.RUNNING, owner_agent_id=claimant,
                    run_id=correlated_run_id,
                    started_at=current.started_at or now_iso, updated_at=now_iso,
                    record_version=current.record_version + 1, claim_owner=claimant,
                    claim_token=new_id("claim"),
                    claim_expires_at=(claimed_at + timedelta(seconds=lease_seconds)).isoformat(),
                )
                self._update_task(connection, next_task, expected_version)
                checkpoint = self._insert_checkpoint(
                    connection,
                    next_task,
                    payload={
                        "event": "claimed",
                        "run_id": correlated_run_id,
                        "executor_attempt_id": new_id("attempt"),
                    },
                )
                connection.commit()
                return next_task, checkpoint
            except Exception:
                connection.rollback()
                raise

    def renew_claim(
        self,
        task_id: str,
        *,
        claim_token: str,
        expected_version: int,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> tuple[Task, TaskCheckpoint]:
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise ValueError("lease_seconds must be an integer")
        if not 1 <= lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        self.initialize()
        renewed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT * FROM kernel_tasks WHERE task_id=?", (task_id,)).fetchone()
                if not row:
                    raise TaskNotFoundError(f"Task not found: {task_id}")
                current = self._row_to_task(row)
                if current.record_version != expected_version:
                    raise ConcurrencyConflictError("stale task version")
                if current.status is not TaskStatus.RUNNING or current.claim_token != claim_token or not current.claim_expires_at:
                    raise ClaimConflictError("supplied claim is not active")
                if datetime.fromisoformat(current.claim_expires_at) <= renewed_at:
                    raise ClaimConflictError("claim expired and cannot be renewed")
                next_task = replace(
                    current, updated_at=renewed_at.isoformat(),
                    record_version=current.record_version + 1,
                    claim_expires_at=(renewed_at + timedelta(seconds=lease_seconds)).isoformat(),
                )
                self._update_task(connection, next_task, expected_version)
                checkpoint = self._insert_checkpoint(connection, next_task, payload={"event": "claim_renewed"})
                connection.commit()
                return next_task, checkpoint
            except Exception:
                connection.rollback()
                raise

    def list_checkpoints(self, task_id: str) -> list[TaskCheckpoint]:
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM kernel_task_checkpoints WHERE task_id=? ORDER BY task_version",
                (task_id,),
            ).fetchall()
        return [
            TaskCheckpoint(
                checkpoint_id=row["checkpoint_id"], task_id=row["task_id"],
                task_version=int(row["task_version"]), status=TaskStatus(row["status"]),
                snapshot=self._loads(row["snapshot_json"], {}),
                payload=self._loads(row["payload_json"], {}), created_at=row["created_at"],
            )
            for row in rows
        ]
