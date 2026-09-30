from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from modules.kernel import (
    AgentResult,
    AgentRole,
    DeterministicCoordinator,
    EXECUTOR_PROTOCOL_VERSION,
    FakeExecutor,
    KernelContractError,
    KernelExecutionEngine,
    TaskRepository,
    TaskStatus,
)
from modules.kernel.tests._execution_test_support import make_spec, make_task


def test_claim_persists_unique_attempt_identity_before_execution(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task, _ = repository.create_task(
        objective="One external attempt", normalized_input={}, idempotency_key="attempt-1",
    )
    claimed, checkpoint = repository.claim_task(
        task.task_id, claimant="coordinator.main", expected_version=task.record_version,
    )
    first_attempt = checkpoint.payload["executor_attempt_id"]
    assert first_attempt.startswith("attempt_")
    assert claimed.claim_token not in str(checkpoint.payload)
    reopened = TaskRepository(tmp_path / "kernel.db")
    assert reopened.list_checkpoints(task.task_id)[1].payload["executor_attempt_id"] == first_attempt

    waiting, _ = repository.transition_task(
        task.task_id,
        TaskStatus.WAITING_APPROVAL,
        expected_version=claimed.record_version,
        claim_token=claimed.claim_token,
    )
    _, next_checkpoint = repository.claim_task(
        task.task_id, claimant="coordinator.main", expected_version=waiting.record_version,
    )
    assert next_checkpoint.payload["executor_attempt_id"] != first_attempt


def test_executor_request_and_terminal_checkpoint_share_claim_attempt(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "external-attempt")
    worker = make_spec("worker.text", AgentRole.WORKER, capabilities=("text.edit",))
    executor = FakeExecutor(AgentResult.succeeded({"result": "done"}), capabilities=("text.edit",))
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(make_spec("coordinator.main", AgentRole.COORDINATOR), (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    checkpoints = repository.list_checkpoints(task.task_id)
    attempt_id = checkpoints[1].payload["executor_attempt_id"]
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert executor.calls[0].attempt_id == attempt_id
    assert executor.calls[0].request_id != attempt_id
    assert checkpoints[-1].payload["executor_attempt_id"] == attempt_id
    assert executor.calls[0].protocol_version == EXECUTOR_PROTOCOL_VERSION == 2
    with pytest.raises(KernelContractError, match="attempt_id"):
        replace(executor.calls[0], attempt_id="")
