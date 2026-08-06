from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from modules.kernel import (
    AgentResult,
    AgentRole,
    BudgetLimit,
    BudgetUsage,
    DeterministicAgentRuntime,
    DeterministicCoordinator,
    ExecutionMode,
    KernelContractError,
    KernelExecutionEngine,
    PermissionRequest,
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
    assert outcome.task.run_id == outcome.run.run_id
    assert outcome.run.metadata["task_id"] == task.task_id
    assert outcome.run.metadata["selected_agent_id"] == "worker.a"

    terminal = repository.list_checkpoints(task.task_id)[-1]
    assert terminal.payload["run_id"] == outcome.run.run_id
    assert terminal.snapshot["task"]["run_id"] == outcome.run.run_id
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


def test_coordinator_and_worker_ids_must_be_globally_unique() -> None:
    coordinator = _spec("agent.same", AgentRole.COORDINATOR)
    worker = _spec("agent.same", AgentRole.WORKER)

    with pytest.raises(KernelContractError, match="must be unique"):
        DeterministicCoordinator(coordinator, (worker,))


def test_execution_spec_rejects_direct_transport_model_policy() -> None:
    coordinator = _spec("coordinator.main", AgentRole.COORDINATOR)
    invalid = coordinator.__class__(
        agent_id=coordinator.agent_id,
        role=coordinator.role,
        capabilities=coordinator.capabilities,
        allowed_tools=coordinator.allowed_tools,
        model_policy={"provider_url": "https://example.invalid/v1"},
        permission_scope=coordinator.permission_scope,
        max_concurrency=coordinator.max_concurrency,
        budget_limits=coordinator.budget_limits,
        input_contract=coordinator.input_contract,
        output_contract=coordinator.output_contract,
    )

    with pytest.raises(KernelContractError, match="Gateway policy"):
        DeterministicCoordinator(invalid)


def test_claim_lease_covers_remaining_wall_clock_budget(
    repository: TaskRepository,
) -> None:
    budget = BudgetLimit(wall_clock_seconds=900)
    task = _task(repository, "lease-budget", budget=budget)
    coordinator = _spec("coordinator.main", AgentRole.COORDINATOR)
    worker = _spec(
        "worker.text",
        AgentRole.WORKER,
        capabilities=("text.edit",),
    )
    runtime = DeterministicAgentRuntime(AgentResult.succeeded({"result": "done"}))
    engine = KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, (worker,)),
        {worker.agent_id: runtime},
        lease_seconds=1,
    )

    captured: dict[str, object] = {}
    original_claim = repository.claim_task

    def capture_claim(*args, **kwargs):
        claimed, checkpoint = original_claim(*args, **kwargs)
        captured["claimed"] = claimed
        return claimed, checkpoint

    repository.claim_task = capture_claim  # type: ignore[method-assign]
    engine.execute_task(task.task_id, prefer_direct=False)

    claimed = captured["claimed"]
    assert claimed.claim_expires_at is not None
    assert claimed.started_at is not None
    duration = datetime.fromisoformat(claimed.claim_expires_at) - datetime.fromisoformat(
        claimed.started_at
    )
    assert duration.total_seconds() >= budget.wall_clock_seconds + 30
