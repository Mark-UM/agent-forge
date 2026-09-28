from __future__ import annotations

from pathlib import Path

import pytest

from modules.kernel import (
    AgentResult,
    AgentRole,
    CancellationToken,
    DeterministicCoordinator,
    ExecutorEvent,
    ExecutorRequest,
    ExecutorResult,
    FakeExecutor,
    KernelContractError,
    KernelExecutionEngine,
    TaskRepository,
    TaskStatus,
)
from modules.kernel.tests._execution_test_support import make_spec, make_task


def test_fake_executor_runs_through_kernel_without_taking_task_authority(
    tmp_path: Path,
) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "fake-executor")
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    worker = make_spec(
        "worker.text", AgentRole.WORKER, capabilities=("text.edit",)
    )
    executor = FakeExecutor(
        AgentResult.succeeded({"result": "done"}),
        capabilities=("text.edit",),
    )
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.task.owner_agent_id == coordinator.agent_id
    assert len(executor.calls) == 1
    request = executor.calls[0]
    assert request.protocol_version == 1
    assert request.request_id == request.command.command_id
    assert request.command.task_id == task.task_id
    assert isinstance(executor.last_result, ExecutorResult)


@pytest.mark.parametrize(
    ("available", "capabilities", "protocol_version", "expected_code"),
    [
        (False, ("text.edit",), 1, "executor_unavailable"),
        (True, (), 1, "contract_invalid"),
        (True, ("text.edit",), 2, "contract_invalid"),
    ],
)
def test_executor_handshake_fails_before_side_effect(
    tmp_path: Path,
    available: bool,
    capabilities: tuple[str, ...],
    protocol_version: int,
    expected_code: str,
) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "handshake")
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    worker = make_spec(
        "worker.text", AgentRole.WORKER, capabilities=("text.edit",)
    )
    executor = FakeExecutor(
        AgentResult.succeeded({"result": "must-not-run"}),
        available=available,
        capabilities=capabilities,
        protocol_version=protocol_version,
    )
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.agent_result is not None
    assert outcome.agent_result.failure is not None
    assert outcome.agent_result.failure.code == expected_code
    assert executor.calls == []


def test_mismatched_executor_result_cannot_succeed_task(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "mismatched-result")
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    worker = make_spec(
        "worker.text", AgentRole.WORKER, capabilities=("text.edit",)
    )
    executor = FakeExecutor(
        ExecutorResult(1, "other-request", AgentResult.succeeded({"result": "wrong"})),
        capabilities=("text.edit",),
    )
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.agent_result is not None
    assert outcome.agent_result.failure is not None
    assert outcome.agent_result.failure.code == "contract_invalid"
    assert len(executor.calls) == 1


def test_executor_event_sequence_must_not_skip_or_change_request() -> None:
    with pytest.raises(KernelContractError, match="sequence must be contiguous"):
        ExecutorResult(
            1,
            "request-1",
            AgentResult.succeeded({"result": "done"}),
            events=(ExecutorEvent("request-1", 2, "executor.started"),),
        )

    with pytest.raises(KernelContractError, match="request_id mismatch"):
        ExecutorResult(
            1,
            "request-1",
            AgentResult.succeeded({"result": "done"}),
            events=(ExecutorEvent("request-2", 1, "executor.started"),),
        )


def test_executor_events_reject_unbounded_input_before_iteration() -> None:
    class UnsafeIterable:
        def __iter__(self):
            raise AssertionError("unbounded events must not be iterated")

    result = AgentResult.succeeded({"result": "done"})
    with pytest.raises(KernelContractError, match="bounded sequence"):
        ExecutorResult(1, "request-1", result, events=UnsafeIterable())
    event = ExecutorEvent("request-1", 1, "executor.started")
    with pytest.raises(KernelContractError, match="bounded sequence"):
        ExecutorResult(1, "request-1", result, events=(event,) * 1_001)


def test_cancellation_during_handshake_prevents_executor_call(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "cancel-during-handshake")
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    worker = make_spec(
        "worker.text", AgentRole.WORKER, capabilities=("text.edit",)
    )
    cancellation = CancellationToken()

    class CancelDuringHandshake(FakeExecutor):
        def capabilities(self):
            cancellation.cancel("stop before invocation")
            return super().capabilities()

    executor = CancelDuringHandshake(
        AgentResult.succeeded({"result": "must-not-run"}),
        capabilities=("text.edit",),
    )
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(
        task.task_id, prefer_direct=False, cancellation=cancellation
    )

    assert outcome.task.status is TaskStatus.CANCELLED
    assert executor.calls == []


def test_cancellation_during_unavailable_health_takes_precedence(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "cancel-during-health")
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    worker = make_spec(
        "worker.text", AgentRole.WORKER, capabilities=("text.edit",)
    )
    cancellation = CancellationToken()

    class CancelDuringHealth(FakeExecutor):
        def health(self):
            cancellation.cancel("stop during health")
            return super().health()

    executor = CancelDuringHealth(
        AgentResult.succeeded({"result": "must-not-run"}),
        available=False,
        capabilities=("text.edit",),
    )
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(
        task.task_id, prefer_direct=False, cancellation=cancellation
    )

    assert outcome.task.status is TaskStatus.CANCELLED
    assert executor.calls == []


def test_executor_cannot_report_success_after_cancellation(tmp_path: Path) -> None:
    repository = TaskRepository(tmp_path / "kernel.db")
    task = make_task(repository, "cancelled-executor")
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    worker = make_spec(
        "worker.text", AgentRole.WORKER, capabilities=("text.edit",)
    )

    def cancel_then_succeed(
        _request: ExecutorRequest, cancellation: CancellationToken
    ) -> AgentResult:
        cancellation.cancel("stop")
        return AgentResult.succeeded({"result": "must-not-succeed"})

    executor = FakeExecutor(cancel_then_succeed, capabilities=("text.edit",))
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: executor},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert len(executor.calls) == 1
    assert outcome.task.status is TaskStatus.CANCELLED
