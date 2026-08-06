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


def test_worker_assignment_is_deterministic_and_coordinator_keeps_ownership(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "assignment")
    first = _spec(
        "worker.a",
        AgentRole.WORKER,
        capabilities=("text.edit",),
    )
    second = _spec(
        "worker.z",
        AgentRole.WORKER,
        capabilities=("text.edit",),
    )
    first_runtime = DeterministicAgentRuntime(
        AgentResult.succeeded({"result": "worker-output-unique"})
    )
    second_runtime = DeterministicAgentRuntime(
        AgentResult.succeeded({"result": "should-not-run"})
    )
    coordinator = _spec("coordinator.main", AgentRole.COORDINATOR)
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (second, first)),
        {"worker.a": first_runtime, "worker.z": second_runtime},
    )

    outcome = engine.execute_task(task.task_id, prefer_direct=False)

    assert outcome.plan is not None
    assert outcome.plan.mode is ExecutionMode.WORKER_AS_TOOL
    assert outcome.plan.selected_agent_id == "worker.a"
    assert len(first_runtime.calls) == 1
    assert second_runtime.calls == []
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.task.owner_agent_id == coordinator.agent_id
    assert outcome.run.metadata["task_id"] == task.task_id
    assert outcome.run.metadata["selected_agent_id"] == "worker.a"

    terminal = repository.list_checkpoints(task.task_id)[-1]
    assert terminal.payload["run_id"] == outcome.run.run_id
    assert terminal.payload["agent_result"]["output_keys"] == ["result"]
    assert "worker-output-unique" not in str(terminal.payload)


def test_direct_decision_uses_coordinator_runtime_without_worker(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "direct")
    coordinator = _spec(
        "coordinator.direct",
        AgentRole.COORDINATOR,
        capabilities=("text.edit",),
    )
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "direct"}))
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator),
        {coordinator.agent_id: runtime},
    )

    outcome = engine.execute_task(task.task_id)

    assert outcome.plan is not None and outcome.plan.mode is ExecutionMode.DIRECT
    assert outcome.task.owner_agent_id == coordinator.agent_id
    assert outcome.task.budget_used.steps == 2
    assert outcome.task.budget_used.agent_count == 1
    assert len(runtime.calls) == 1


def test_worker_as_tool_charges_usage_once_and_preserves_parent_ownership(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "usage")
    runtime = DeterministicAgentRuntime(
        AgentResult.succeeded(
            {"result": "done"},
            usage=BudgetUsage(tokens=12, wall_clock_seconds=1),
        )
    )
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(
        task.task_id,
        prefer_direct=False,
        tool_name="workspace.write",
        permission_request=PermissionRequest(workspace_path="workspace/result.txt"),
    )

    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.task.owner_agent_id == "coordinator.main"
    assert outcome.task.budget_used == BudgetUsage(
        steps=2,
        tokens=12,
        wall_clock_seconds=1,
        tool_calls=1,
        agent_count=2,
    )


def test_disallowed_tool_is_rejected_before_runtime_side_effect(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "permission")
    worker = _spec(
        "worker.readonly",
        AgentRole.WORKER,
        capabilities=("text.edit",),
    )
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "unsafe"}))
    engine = _worker_engine(repository, runtime, workers=(worker,))

    outcome = engine.execute_task(
        task.task_id,
        prefer_direct=False,
        tool_name="workspace.write",
    )

    assert runtime.calls == []
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.task.failure is not None
    assert outcome.task.failure.category is FailureCategory.VALIDATION
    assert outcome.task.failure.code == "permission_denied"


def test_workspace_scope_escape_is_rejected_before_runtime(
    repository: TaskRepository,
) -> None:
    task = _task(repository, "workspace-scope")
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "unsafe"}))
    engine = _worker_engine(repository, runtime)

    outcome = engine.execute_task(
        task.task_id,
        prefer_direct=False,
        permission_request=PermissionRequest(workspace_path="other/result.txt"),
    )

    assert outcome.task.status is TaskStatus.FAILED
    assert runtime.calls == []


