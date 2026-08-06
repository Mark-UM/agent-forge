from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from threading import Barrier, Thread

import pytest

from modules.kernel import (
    AgentRole,
    AgentSpec,
    Approval,
    ApprovalStatus,
    Artifact,
    BudgetLimit,
    BudgetUsage,
    ClaimConflictError,
    ConcurrencyConflictError,
    DegradedInfo,
    FailureCategory,
    FailureInfo,
    IdempotencyConflictError,
    InvalidTransitionError,
    KernelContractError,
    PermissionScope,
    SensitiveValueError,
    Task,
    TaskRepository,
    TaskStatus,
)


@pytest.fixture
def repository(tmp_path: Path) -> TaskRepository:
    return TaskRepository(tmp_path / "kernel.db")


def test_contracts_are_typed_bounded_and_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "known-secret-value")
    with pytest.raises(SensitiveValueError):
        Task(
            task_id="task_secret",
            objective="Persist known-secret-value",
            normalized_input={},
            idempotency_key="secret",
        )
    with pytest.raises(SensitiveValueError):
        Task(
            task_id="task_key",
            objective="Reject secret keys",
            normalized_input={"api_key": "not-even-persisted"},
            idempotency_key="secret-key",
        )
    with pytest.raises(KernelContractError, match="timezone"):
        Task(
            task_id="task_time",
            objective="Reject naive time",
            normalized_input={},
            idempotency_key="naive",
            created_at="2026-08-06T10:00:00",
            updated_at="2026-08-06T10:00:00",
        )


def test_agents_artifacts_and_approvals_validate() -> None:
    spec = AgentSpec(
        agent_id="worker.text",
        role=AgentRole.WORKER,
        capabilities=("text.edit",),
        allowed_tools=("workspace.write",),
        model_policy={"gateway_route": "pro"},
        permission_scope=PermissionScope(
            workspace_roots=("workspace",), allowed_tools=("workspace.write",)
        ),
        input_contract={"type": "object"},
        output_contract={"type": "object"},
    )
    assert spec.max_concurrency == 1
    artifact = Artifact(
        artifact_id="artifact_1",
        task_id="task_1",
        producer_agent_id=spec.agent_id,
        artifact_type="file",
        reference="workspace/result.txt",
        digest=Artifact.digest_bytes(b"result"),
        provenance={"source": "worker"},
    )
    assert artifact.digest.startswith("sha256:")
    with pytest.raises(KernelContractError):
        replace(artifact, reference="../outside.txt")
    approval = Approval(
        approval_id="approval_1",
        task_id="task_1",
        requested_action={"tool": "workspace.write"},
        reason="Write the reviewed file",
        scope={"path": "workspace/result.txt"},
        requester_agent_id=spec.agent_id,
    )
    assert approval.decision is ApprovalStatus.PENDING


def test_terminal_states_require_truthful_information() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with pytest.raises(KernelContractError):
        Task(
            task_id="task_failed",
            objective="Fail truthfully",
            normalized_input={},
            status=TaskStatus.FAILED,
            finished_at=now,
            idempotency_key="failed",
        )
    degraded = Task(
        task_id="task_degraded",
        objective="Return partial output",
        normalized_input={},
        status=TaskStatus.DEGRADED,
        created_at=now,
        updated_at=now,
        finished_at=now,
        degraded=DegradedInfo(
            summary="Optional Vision is unavailable",
            missing_capabilities=("vision.recognize",),
        ),
        idempotency_key="degraded",
    )
    assert degraded.status is TaskStatus.DEGRADED


def test_create_is_idempotent_and_conflicting_reuse_fails(repository: TaskRepository) -> None:
    first, created = repository.create_task(
        objective="Create once", normalized_input={"path": "a.txt"}, idempotency_key="same"
    )
    replay, replay_created = repository.create_task(
        objective="Create once", normalized_input={"path": "a.txt"}, idempotency_key="same"
    )
    assert created is True and replay_created is False
    assert replay.task_id == first.task_id
    with pytest.raises(IdempotencyConflictError):
        repository.create_task(
            objective="Different request", normalized_input={}, idempotency_key="same"
        )


def test_state_and_checkpoint_are_atomic(repository: TaskRepository) -> None:
    task, _ = repository.create_task(
        objective="Atomic transition", normalized_input={}, idempotency_key="atomic"
    )
    claimed, checkpoint = repository.claim_task(
        task.task_id, claimant="worker.atomic", expected_version=0
    )
    assert claimed.status is TaskStatus.RUNNING
    assert checkpoint.snapshot["task"]["claim_token"] is None
    assert checkpoint.snapshot["task"]["claim_token_fingerprint"]
    with pytest.raises(sqlite3.IntegrityError):
        repository.transition_task(
            task.task_id,
            TaskStatus.SUCCEEDED,
            expected_version=1,
            claim_token=claimed.claim_token,
            checkpoint_id=checkpoint.checkpoint_id,
        )
    after = repository.get_task(task.task_id)
    assert after is not None and after.status is TaskStatus.RUNNING
    assert after.record_version == 1


def test_cas_and_claim_token_prevent_duplicate_execution(repository: TaskRepository) -> None:
    task, _ = repository.create_task(
        objective="Single claim", normalized_input={}, idempotency_key="claim"
    )
    claimed, _ = repository.claim_task(
        task.task_id, claimant="worker.one", expected_version=0
    )
    with pytest.raises(ConcurrencyConflictError):
        repository.claim_task(task.task_id, claimant="worker.two", expected_version=0)
    with pytest.raises(ClaimConflictError):
        repository.transition_task(task.task_id, TaskStatus.SUCCEEDED, expected_version=1)
    done, _ = repository.transition_task(
        task.task_id,
        TaskStatus.SUCCEEDED,
        expected_version=1,
        claim_token=claimed.claim_token,
    )
    assert done.status is TaskStatus.SUCCEEDED and done.claim_token is None


def test_waiting_state_releases_claim_and_can_be_reclaimed(repository: TaskRepository) -> None:
    task, _ = repository.create_task(
        objective="Wait", normalized_input={}, idempotency_key="wait"
    )
    claimed, _ = repository.claim_task(task.task_id, claimant="worker.one", expected_version=0)
    waiting, _ = repository.transition_task(
        task.task_id,
        TaskStatus.WAITING_APPROVAL,
        expected_version=1,
        claim_token=claimed.claim_token,
    )
    assert waiting.claim_owner is None
    resumed, _ = repository.claim_task(
        task.task_id, claimant="worker.two", expected_version=2
    )
    assert resumed.status is TaskStatus.RUNNING


def test_live_claim_renews_but_expired_claim_does_not(repository: TaskRepository) -> None:
    task, _ = repository.create_task(
        objective="Lease", normalized_input={}, idempotency_key="lease"
    )
    base = datetime.fromisoformat(task.created_at) + timedelta(seconds=1)
    claimed, _ = repository.claim_task(
        task.task_id,
        claimant="worker.lease",
        expected_version=0,
        lease_seconds=60,
        now=base,
    )
    renewed, _ = repository.renew_claim(
        task.task_id,
        claim_token=claimed.claim_token or "",
        expected_version=1,
        lease_seconds=120,
        now=base + timedelta(seconds=30),
    )
    assert renewed.record_version == 2
    with pytest.raises(ClaimConflictError, match="expired"):
        repository.renew_claim(
            task.task_id,
            claim_token=renewed.claim_token or "",
            expected_version=2,
            now=base + timedelta(seconds=151),
        )


def test_competing_threads_only_claim_once(repository: TaskRepository) -> None:
    task, _ = repository.create_task(
        objective="Concurrent claim", normalized_input={}, idempotency_key="threads"
    )
    barrier = Barrier(2)
    results: list[str] = []

    def claim(name: str) -> None:
        barrier.wait()
        try:
            repository.claim_task(task.task_id, claimant=name, expected_version=0)
            results.append("claimed")
        except (ConcurrencyConflictError, ClaimConflictError):
            results.append("rejected")

    threads = [Thread(target=claim, args=(f"worker.{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["claimed", "rejected"]


def test_database_handles_are_released(tmp_path: Path) -> None:
    path = tmp_path / "closable.db"
    repository = TaskRepository(path)
    repository.create_task(
        objective="Close handles", normalized_input={}, idempotency_key="close"
    )
    path.unlink()
    assert not path.exists()


def test_state_machine_rejects_terminal_reopen(repository: TaskRepository) -> None:
    task, _ = repository.create_task(
        objective="Terminal", normalized_input={}, idempotency_key="terminal"
    )
    failed, _ = repository.transition_task(
        task.task_id,
        TaskStatus.FAILED,
        expected_version=0,
        failure=FailureInfo(FailureCategory.EXECUTION, "deterministic failure"),
        control_plane=True,
    )
    with pytest.raises(InvalidTransitionError):
        repository.transition_task(
            failed.task_id,
            TaskStatus.RUNNING,
            expected_version=1,
            control_plane=True,
        )


def test_child_budget_must_fit_parent() -> None:
    parent = BudgetLimit(steps=4, tokens=1_000, wall_clock_seconds=60)
    assert parent.contains(BudgetLimit(steps=2, tokens=500, wall_clock_seconds=30))
    assert not parent.contains(BudgetLimit(steps=5, tokens=500, wall_clock_seconds=30))
    assert BudgetUsage(steps=4, tokens=1_000, wall_clock_seconds=60).within(parent)
