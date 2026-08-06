from __future__ import annotations

from pathlib import Path

import pytest

from modules.common.run import RunStatus
from modules.kernel import (
    AgentResult, AgentResultStatus, AgentRole, BudgetLimit, BudgetUsage,
    CancellationToken, DegradedInfo, DeterministicAgentRuntime,
    DeterministicCoordinator, DuplicateExecutionError, ExecutionMode,
    FailureCategory, KernelContractError, KernelExecutionEngine,
    PermissionRequest, TaskRepository, TaskStatus,
)
from modules.kernel.tests._execution_test_support import (
    make_spec as _spec, make_task as _task, make_worker_engine as _worker_engine,
)


@pytest.fixture
def repository(tmp_path: Path) -> TaskRepository:
    return TaskRepository(tmp_path / "kernel.db")


def test_malformed_output_contract_fails_without_false_success(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "bad-output")
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"wrong": "shape"}))
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert len(runtime.cals) == 1
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.task.failure is not None
    assert outcome.task.failure.code == "contract_invalid"


def test_runtime_exception_is_redacted_to_exception_type(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "exception")

    def explode(command, token):
        raise RuntimeError("raw provider payload must not be persisted")

    runtime = DeterministicAgentRuntime(explode)
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.task.failure is not None
    assert outcome.task.failure.message == "agent runtime raised RuntimeError"
    assert "raw provider payload" not in str(repository.list_checkpoints(task.task_id)[-1].payload)


def test_no_ready_runtime_fails_configuration_without_direct_endpoint(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "unavailable")
    coordinator = _spec("coordinator.main", AgentRole.COORDINATOR)
    worker = _spec(
        "worker.missing",
        AgentRole.WORKER,
        capabilities=("text.edit",),
    )
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.task.failure is not None
    assert outcome.task.failure.category is FailureCategory.CONFIGURATION


def test_secret_shaped_runtime_output_is_rejected_and_not_persisted(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "secret-output")

    def secret_output(command, token):
        return AgentResult.succeeded({"result": "sk-secretvalue1234567890"})

    runtime = DeterministicAgentRuntime(secret_output)
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.FAILED
    payload = repository.list_checkpoints(task.task_id)[-1].payload
    assert "sk-secretvalue1234567890" not in str(payload)


def test_agent_result_cannot_double_charge_engine_structural_usage() -> None:
    with pytest.raises(KernelContractError, match="engine-owned structural counters"):
        AgentResult.succeeded(
            {"result": "invalid usage"},
            usage=BudgetUsage(steps=1, tool_calls=1),
        )
