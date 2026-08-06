"""Deterministic Coordinator selection and Agent-as-tool planning policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .agents import AgentResult, AgentResultStatus
from .contracts import AgentRole, AgentSpec, KernelContractError, Task, new_id
from .execution_support import (
    BudgetExceededError,
    ExecutionError,
    ExecutionMode,
    NoEligibleAgentError,
    PermissionDeniedError,
    _identifier,
)
from .permissions import PermissionAuthorizer, PermissionRequest

@dataclass(frozen=True, slots=True)
class CoordinatorPlan:
    plan_id: str
    task_id: str
    mode: ExecutionMode
    selected_agent_id: str
    required_capabilities: tuple[str, ...]
    rationale: str
    tool_name: str | None = None

    def __post_init__(self) -> None:
        for name in ("plan_id", "task_id", "selected_agent_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        if not isinstance(self.mode, ExecutionMode):
            raise KernelContractError("plan mode must be ExecutionMode")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise KernelContractError("plan rationale is required")

    def telemetry(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "mode": self.mode.value,
            "selected_agent_id": self.selected_agent_id,
            "required_capabilities": list(self.required_capabilities),
            "tool_name": self.tool_name,
        }


class DeterministicCoordinator:
    """Sort-stable eligibility and direct-call decision policy."""

    def __init__(self, coordinator: AgentSpec, workers: tuple[AgentSpec, ...] = ()) -> None:
        if coordinator.role is not AgentRole.COORDINATOR:
            raise KernelContractError("coordinator AgentSpec must use coordinator role")
        if len({worker.agent_id for worker in workers}) != len(workers):
            raise KernelContractError("worker agent IDs must be unique")
        if any(worker.role is not AgentRole.WORKER for worker in workers):
            raise KernelContractError("worker AgentSpecs must use worker role")
        self.coordinator = coordinator
        self.workers = tuple(sorted(workers, key=lambda item: item.agent_id))

    @staticmethod
    def _contract_required_fields(contract: Mapping[str, Any]) -> tuple[str, ...]:
        raw = contract.get("required", ()) if isinstance(contract, Mapping) else ()
        if not isinstance(raw, (list, tuple)):
            raise KernelContractError("contract required field must be a list")
        return tuple(_identifier(value, "contract field") for value in raw)

    def _eligible(
        self,
        spec: AgentSpec,
        task: Task,
        *,
        tool_name: str | None,
        permission_request: PermissionRequest,
        available_agent_ids: frozenset[str],
    ) -> None:
        if spec.agent_id not in available_agent_ids:
            raise NoEligibleAgentError(f"agent {spec.agent_id} has no ready runtime")
        if not spec.budget_limits.contains(task.budget):
            raise BudgetExceededError(f"task budget exceeds AgentSpec {spec.agent_id} limits")
        required_input = self._contract_required_fields(spec.input_contract)
        missing_input = sorted(set(required_input) - set(task.normalized_input.keys()))
        if missing_input:
            raise KernelContractError(
                f"task input is missing fields for {spec.agent_id}: {', '.join(missing_input)}"
            )
        PermissionAuthorizer.authorize(
            spec,
            required_capabilities=task.required_capabilities,
            tool_name=tool_name,
            request=permission_request,
        )

    def plan(
        self,
        task: Task,
        *,
        available_agent_ids: frozenset[str],
        permission_request: PermissionRequest,
        tool_name: str | None = None,
        prefer_direct: bool = True,
    ) -> CoordinatorPlan:
        failures: list[ExecutionError | KernelContractError] = []
        if prefer_direct:
            try:
                self._eligible(
                    self.coordinator,
                    task,
                    tool_name=tool_name,
                    permission_request=permission_request,
                    available_agent_ids=available_agent_ids,
                )
                return CoordinatorPlan(
                    plan_id=new_id("plan"),
                    task_id=task.task_id,
                    mode=ExecutionMode.DIRECT,
                    selected_agent_id=self.coordinator.agent_id,
                    required_capabilities=task.required_capabilities,
                    rationale="Coordinator can satisfy the bounded Task directly",
                    tool_name=tool_name,
                )
            except (ExecutionError, KernelContractError) as exc:
                failures.append(exc)

        for worker in self.workers:
            try:
                self._eligible(
                    worker,
                    task,
                    tool_name=tool_name,
                    permission_request=permission_request,
                    available_agent_ids=available_agent_ids,
                )
                return CoordinatorPlan(
                    plan_id=new_id("plan"),
                    task_id=task.task_id,
                    mode=ExecutionMode.WORKER_AS_TOOL,
                    selected_agent_id=worker.agent_id,
                    required_capabilities=task.required_capabilities,
                    rationale="Selected first eligible Worker by stable agent_id order",
                    tool_name=tool_name,
                )
            except (ExecutionError, KernelContractError) as exc:
                failures.append(exc)

        permission_failure = next(
            (failure for failure in failures if isinstance(failure, PermissionDeniedError)),
            None,
        )
        if permission_failure is not None:
            raise permission_failure
        budget_failure = next(
            (failure for failure in failures if isinstance(failure, BudgetExceededError)),
            None,
        )
        if budget_failure is not None:
            raise budget_failure
        contract_failure = next(
            (failure for failure in failures if isinstance(failure, KernelContractError)),
            None,
        )
        if contract_failure is not None:
            raise contract_failure
        raise NoEligibleAgentError("no ready Agent satisfies capability and contract requirements")

    def validate_output(self, spec: AgentSpec, result: AgentResult) -> None:
        if result.status not in {AgentResultStatus.SUCCEEDED, AgentResultStatus.DEGRADED}:
            return
        required_output = self._contract_required_fields(spec.output_contract)
        missing = sorted(set(required_output) - set(result.output.keys()))
        if missing:
            raise KernelContractError(
                f"agent output is missing fields for {spec.agent_id}: {', '.join(missing)}"
            )


