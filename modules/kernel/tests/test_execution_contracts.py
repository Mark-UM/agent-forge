from __future__ import annotations

from pathlib import Path

import pytest

from modules.kernel import (
    AgentCommand,
    AgentResult,
    AgentResultStatus,
    AgentRole,
    BudgetLimit,
    BudgetUsage,
    CancellationToken,
    DeterministicAgentRuntime,
    DeterministicCoordinator,
    FailureCategory,
    FailureInfo,
    KernelContractError,
    KernelExecutionEngine,
    PermissionRequest,
    SensitiveValueError,
    TaskRepository,
    TaskStatus,
)
from modules.kernel.tests._execution_test_support import (
    make_spec as _spec,
    make_task as _task,
    make_worker_engine as _worker_engine,
)


@pytest.fixture
def repository(tmp_path: Path) -> TaskRepository:
    return TaskRepository(tmp_path / "kernel.db")


def test_malformed_output_contract_fails_without_false_success(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "bad-output")
    runtime = DeterministicAgentRuntime(
        AgentResult.succeeded(
            {"wrong": "shape"},
            usage=BudgetUsage(tokens=7, wall_clock_seconds=2),
        )
    )
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert len(runtime.calls) == 1
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.task.failure is not None
    assert outcome.task.failure.code == "contract_invalid"
    assert outcome.task.budget_used.tokens == 7
    assert outcome.task.budget_used.wall_clock_seconds == 2


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


def test_non_success_agent_result_cannot_smuggle_partial_output() -> None:
    with pytest.raises(KernelContractError, match="must not expose output"):
        AgentResult(
            AgentResultStatus.FAILED,
            output={"result": "unaccepted partial output"},
            failure=FailureInfo(FailureCategory.EXECUTION, "failed"),
        )


def test_permission_request_rejects_truthy_non_boolean_values() -> None:
    with pytest.raises(KernelContractError, match="must be boolean"):
        PermissionRequest(network_read=1)  # type: ignore[arg-type]


def test_cancellation_reason_rejects_secret_shaped_text() -> None:
    token = CancellationToken()
    with pytest.raises(SensitiveValueError):
        token.cancel("cancel sk-secretvalue1234567890")
    assert token.cancelled is False


def test_agent_command_requires_typed_budget_and_permission_request() -> None:
    with pytest.raises(KernelContractError, match="remaining_budget"):
        AgentCommand(
            command_id="command_1",
            task_id="task_1",
            agent_id="worker.text",
            objective="Edit text",
            normalized_input={"text": "source"},
            required_capabilities=("text.edit",),
            remaining_budget={},  # type: ignore[arg-type]
        )
    with pytest.raises(KernelContractError, match="permission_request"):
        AgentCommand(
            command_id="command_2",
            task_id="task_1",
            agent_id="worker.text",
            objective="Edit text",
            normalized_input={"text": "source"},
            required_capabilities=("text.edit",),
            remaining_budget=BudgetLimit(),
            permission_request={},  # type: ignore[arg-type]
        )


def test_claim_atomically_binds_authoritative_run_id(
    repository: TaskRepository,
) -> None:
    task, created = repository.create_task(
        objective="Bind one run",
        normalized_input={},
        idempotency_key="bind-run",
    )
    assert created is True

    claimed, checkpoint = repository.claim_task(
        task.task_id,
        claimant="coordinator.main",
        expected_version=task.record_version,
        run_id="run_kernel_1",
    )

    assert claimed.run_id == "run_kernel_1"
    assert checkpoint.snapshot["task"]["run_id"] == "run_kernel_1"
    assert checkpoint.payload["run_id"] == "run_kernel_1"
    reopened = repository.get_task(task.task_id)
    assert reopened is not None and reopened.run_id == "run_kernel_1"


def test_run_id_cannot_change_after_correlation(
    repository: TaskRepository,
) -> None:
    from modules.kernel import ConcurrencyConflictError

    task, _ = repository.create_task(
        objective="Keep correlation stable",
        normalized_input={},
        idempotency_key="stable-run",
    )
    claimed, _ = repository.claim_task(
        task.task_id,
        claimant="coordinator.main",
        expected_version=task.record_version,
        run_id="run_original",
    )

    with pytest.raises(ConcurrencyConflictError, match="another run"):
        repository.transition_task(
            task.task_id,
            TaskStatus.FAILED,
            expected_version=claimed.record_version,
            run_id="run_changed",
            claim_token=claimed.claim_token,
            failure=FailureInfo(FailureCategory.EXECUTION, "failure"),
        )

    persisted = repository.get_task(task.task_id)
    assert persisted is not None
    assert persisted.status is TaskStatus.RUNNING
    assert persisted.run_id == "run_original"
