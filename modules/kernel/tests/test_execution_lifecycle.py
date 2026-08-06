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


def test_queued_cancellation_never_claims_or_invokes_runtime(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "queued-cancel")
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "unused"}))
    cancellation = CancellationToken()
    cancellation.cancel("caller cancelled queued work")
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(
        task.task_id,
        prefer_direct=False,
        cancellation=cancellation,
    )

    assert runtime.calls == []
    assert outcome.task.status is TaskStatus.CANCELLED
    assert outcome.task.record_version == 1
    assert outcome.run.status == RunStatus.ABANDONED
    assert [checkpoint.status for checkpoint in repository.list_checkpoints(task.task_id)] == [
        TaskStatus.QUEUED,
        TaskStatus.CANCELLED,
    ]


def test_running_cancellation_propagates_from_runtime(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "running-cancel")

    def cancel_during_call(command, token):
        token.cancel("cancel during Worker execution")
        token.raise_if_cancelled()
        raise AssertionError("unreachable")

    runtime = DeterministicAgentRuntime(cancel_during_call)
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert len(runtime.calls) == 1
    assert outcome.task.status is TaskStatus.CANCELLED
    assert outcome.run.status == RunStatus.ABANDONED
    assert outcome.task.failure is not None
    assert outcome.task.failure.category is FailureCategory.CANCELLATION


def test_timeout_is_distinct_from_failure_and_cancellation(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "timeout")
    runtime = DeterministicAgentRuntime(AgentResult.timed_out())
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.TIMED_OUT
    assert outcome.run.status == RunStatus.TIMED_OUT
    assert outcome.task.failure is not None
    assert outcome.task.failure.category is FailureCategory.TIMEOUT


def test_runtime_failure_is_truthful_and_not_degraded(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "failure")
    runtime = DeterministicAgentRuntime(
        AgentResult.failed("deterministic Worker failure")
    )
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.run.status == RunStatus.FAILED
    assert outcome.task.degraded is None
    assert outcome.agent_result is not None
    assert outcome.agent_result.status is AgentResultStatus.FAILED


def test_usable_partial_result_is_explicitly_degraded(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "degraded")
    runtime = DeterministicAgentRuntime(
        AgentResult(
            AgentResultStatus.DEGRADED,
            output={"result": "usable partial"},
            degraded=DegradedInfo(
                summary="Optional evidence is unavailable",
                missing_capabilities=("search.pipeline",),
            ),
        )
    )
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.task.status is TaskStatus.DEGRADED
    assert outcome.run.status == RunStatus.DEGRADED
    assert outcome.task.degraded is not None


def test_duplicate_execution_does_not_repeat_worker_side_effect(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "duplicate")
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "once"}))
    engine = _worker_engine(repository, runtime)

    engine.execute_task(task.task_id, prefer_direct=False)
    with pytest.raises(DuplicateExecutionError):
        engine.execute_task(task.task_id, prefer_direct=False)

    assert len(runtime.calls) == 1


def test_structural_budget_exhaustion_prevents_agent_call(
    repository: TaskRepository,
) -> None:
    task = _task(
        repository,
        "budget",
        budget=BudgetLimit(
            steps=1,
            tokens=1,
            wall_clock_seconds=1,
            retries=0,
            tool_calls=0,
            agent_count=1,
            child_tasks=0,
        ),
    )
    worker_budget = BudgetLimit(
        steps=1,
        tokens=1,
        wall_clock_seconds=1,
        retries=0,
        tool_calls=0,
        agent_count=1,
        child_tasks=0,
    )
    worker = _spec(
        "worker.small",
        AgentRole.WORKER,
        capabilities=("text.edit",),
        budget=worker_budget,
    )
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "unused"}))
    engine = _worker_engine(repository, runtime, workers=(worker,))

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert runtime.calls == []
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.task.failure is not None
    assert outcome.task.failure.category is FailureCategory.BUDGET_EXHAUSTION
    assert outcome.task.budget_used.steps == 1


