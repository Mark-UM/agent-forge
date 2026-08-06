from __future__ import annotations

from modules.kernel import (
    AgentRole,
    AgentSpec,
    BudgetLimit,
    DeterministicAgentRuntime,
    DeterministicCoordinator,
    KernelExecutionEngine,
    PermissionScope,
    TaskRepository,
)


def make_spec(
    agent_id: str,
    role: AgentRole,
    *,
    capabilities: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    budget: BudgetLimit | None = None,
    required_input: tuple[str, ...] = ("text",),
    required_output: tuple[str, ...] = ("result",),
) -> AgentSpec:
    return AgentSpec(
        agent_id=agent_id,
        role=role,
        capabilities=capabilities,
        allowed_tools=tools,
        model_policy={"gateway_route": "deterministic_stub"},
        permission_scope=PermissionScope(
            workspace_roots=("workspace",),
            allowed_tools=tools,
        ),
        budget_limits=budget or BudgetLimit(),
        input_contract={"type": "object", "required": list(required_input)},
        output_contract={"type": "object", "required": list(required_output)},
    )


def make_task(
    repository: TaskRepository,
    key: str,
    *,
    capabilities: tuple[str, ...] = ("text.edit",),
    budget: BudgetLimit | None = None,
):
    task, created = repository.create_task(
        objective="Apply one deterministic text edit",
        normalized_input={"text": "source"},
        idempotency_key=key,
        required_capabilities=capabilities,
        budget=budget or BudgetLimit(),
    )
    assert created is True
    return task


def make_worker_engine(
    repository: TaskRepository,
    runtime: DeterministicAgentRuntime,
    *,
    workers: tuple[AgentSpec, ...] | None = None,
) -> KernelExecutionEngine:
    coordinator = make_spec("coordinator.main", AgentRole.COORDINATOR)
    default_worker = make_spec(
        "worker.text",
        AgentRole.WORKER,
        capabilities=("text.edit",),
        tools=("workspace.write",),
    )
    selected_workers = workers or (default_worker,)
    runtimes = {worker.agent_id: runtime for worker in selected_workers}
    return KernelExecutionEngine(
        repository,
        DeterministicCoordinator(coordinator, selected_workers),
        runtimes,
    )
