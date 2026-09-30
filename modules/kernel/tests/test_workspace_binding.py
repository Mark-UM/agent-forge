from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from modules.kernel import (
    AgentResult,
    AgentRole,
    ArtifactIntegrityError,
    DeterministicCoordinator,
    FakeExecutor,
    IdempotencyConflictError,
    KernelContractError,
    KernelExecutionEngine,
    LifecycleRepository,
    TaskRepository,
    TaskStatus,
    WorkspaceBinding,
)
from modules.kernel.repository import _SCHEMA_SQL
from modules.kernel.tests._execution_test_support import make_spec
from modules.kernel.tests._lifecycle_test_support import NOW, provenance, running_task


def test_workspace_binding_persists_and_reaches_executor(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    binding = WorkspaceBinding.capture("project.alpha", workspace)
    repository = TaskRepository(tmp_path / "kernel.db")
    task, created = repository.create_task(
        objective="Read workspace",
        normalized_input={"text": "source"},
        idempotency_key="workspace-task",
        required_capabilities=("text.edit",),
        workspace=binding,
    )
    assert created
    assert repository.get_task(task.task_id).workspace == binding
    assert repository.list_checkpoints(task.task_id)[0].snapshot["task"]["workspace"] == binding.to_dict()

    worker = make_spec("worker.text", AgentRole.WORKER, capabilities=("text.edit",))
    executor = FakeExecutor(AgentResult.succeeded({"result": "done"}), capabilities=("text.edit",))
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(make_spec("coordinator.main", AgentRole.COORDINATOR), (worker,)),
        {worker.agent_id: executor},
    )
    outcome = engine.execute_task(task.task_id, prefer_direct=False)
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert executor.calls[0].workspace == binding


def test_workspace_binding_is_part_of_idempotent_request(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    repository = TaskRepository(tmp_path / "kernel.db")
    binding = WorkspaceBinding.capture("project.alpha", first)
    original, _ = repository.create_task(
        objective="Read workspace", normalized_input={}, idempotency_key="same", workspace=binding,
    )
    replay, created = repository.create_task(
        objective="Read workspace", normalized_input={}, idempotency_key="same", workspace=binding,
    )
    assert not created and replay.task_id == original.task_id
    with pytest.raises(IdempotencyConflictError):
        repository.create_task(
            objective="Read workspace",
            normalized_input={},
            idempotency_key="same",
            workspace=WorkspaceBinding.capture("project.alpha", second),
        )


def test_idempotent_replay_survives_workspace_unavailability(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    binding = WorkspaceBinding.capture("project.alpha", workspace)
    repository = TaskRepository(tmp_path / "kernel.db")
    original, _ = repository.create_task(
        objective="Read workspace", normalized_input={}, idempotency_key="same", workspace=binding,
    )
    workspace.rename(tmp_path / "moved")
    replay, created = repository.create_task(
        objective="Read workspace", normalized_input={}, idempotency_key="same", workspace=binding,
    )
    assert not created and replay.task_id == original.task_id


def test_replaced_workspace_fails_before_executor_call(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    repository = TaskRepository(tmp_path / "kernel.db")
    task, _ = repository.create_task(
        objective="Read workspace",
        normalized_input={"text": "source"},
        idempotency_key="replaced",
        required_capabilities=("text.edit",),
        workspace=WorkspaceBinding.capture("project.alpha", workspace),
    )
    workspace.rename(tmp_path / "moved")
    workspace.mkdir()
    worker = make_spec("worker.text", AgentRole.WORKER, capabilities=("text.edit",))
    executor = FakeExecutor(AgentResult.succeeded({"result": "wrong"}), capabilities=("text.edit",))
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(make_spec("coordinator.main", AgentRole.COORDINATOR), (worker,)),
        {worker.agent_id: executor},
    )
    outcome = engine.execute_task(task.task_id, prefer_direct=False)
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.agent_result.failure.code == "contract_invalid"
    assert executor.calls == []


def test_workspace_capture_rejects_missing_or_non_directory_root(tmp_path: Path) -> None:
    with pytest.raises(KernelContractError, match="workspace root"):
        WorkspaceBinding.capture("project.alpha", tmp_path / "missing")
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(KernelContractError, match="workspace root"):
        WorkspaceBinding.capture("project.alpha", file_path)


def test_bound_task_artifact_access_fails_closed_until_isolated(tmp_path: Path) -> None:
    root = tmp_path / "assigned"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    (root / "note.txt").write_text("assigned", encoding="utf-8")
    (other / "note.txt").write_text("other", encoding="utf-8")
    repository = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(
        repository,
        owner="worker.text",
        workspace=WorkspaceBinding.capture("project.alpha", root),
    )
    arguments = dict(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="bound-artifact",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        now=NOW,
    )
    with pytest.raises(ArtifactIntegrityError, match="OS-isolated Workspace"):
        repository.register_file_artifact(workspace_root=other, **arguments)
    with pytest.raises(ArtifactIntegrityError, match="OS-isolated Workspace"):
        repository.register_file_artifact(workspace_root=root, **arguments)
    assert repository.list_artifacts(task.task_id) == []


def test_existing_v1_database_migrates_without_losing_tasks(tmp_path: Path) -> None:
    assert hashlib.sha256(_SCHEMA_SQL.encode()).hexdigest() == (
        "5486e0279ed15d3915862fc39434552696dab0fd1adcd5eedddfd5ee4e4ae559"
    )
    db_path = tmp_path / "kernel.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO kernel_schema_migrations(version, checksum, applied_at) VALUES (1, ?, ?)",
            (hashlib.sha256(_SCHEMA_SQL.encode()).hexdigest(), "2026-09-01T00:00:00+00:00"),
        )
        budget = {
            "steps": 20, "tokens": 100_000, "wall_clock_seconds": 1_800,
            "retries": 2, "tool_calls": 50, "agent_count": 3, "child_tasks": 8,
        }
        old_request = {
            "objective": "Old task", "normalized_input": {}, "parent_task_id": None,
            "required_capabilities": [], "budget": budget, "owner_agent_id": None,
        }
        digest = hashlib.sha256(
            json.dumps(old_request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        connection.execute(
            """INSERT INTO kernel_tasks (
                task_id, objective, normalized_input_json, status,
                required_capabilities_json, budget_json, budget_used_json,
                artifact_ids_json, approval_ids_json, created_at, updated_at,
                idempotency_scope, idempotency_key, request_digest, record_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "task_legacy", "Old task", "{}", "queued", "[]",
                json.dumps(budget), json.dumps({name: 0 for name in budget}),
                "[]", "[]", "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00", "default", "legacy", digest, 0,
            ),
        )
    repository = TaskRepository(db_path)
    repository.initialize()
    with sqlite3.connect(db_path) as connection:
        versions = [row[0] for row in connection.execute("SELECT version FROM kernel_schema_migrations ORDER BY version")]
        columns = [row[1] for row in connection.execute("PRAGMA table_info(kernel_tasks)")]
    assert versions == [1, 2]
    assert "workspace_json" in columns
    legacy, created = repository.create_task(
        objective="Old task", normalized_input={}, idempotency_key="legacy",
    )
    assert not created and legacy.task_id == "task_legacy"
    assert repository.get_task(legacy.task_id).workspace is None
