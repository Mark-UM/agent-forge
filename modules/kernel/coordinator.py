"""Deterministic Coordinator selection and Agent-as-tool planning policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .agents import AgentResult, AgentResultStatus
from .contracts import (
    AgentRole,
    AgentSpec,
    BudgetLimit,
    KernelContractError,
    PermissionScope,
    Task,
    new_id,
)
from .execution_support import (
    BudgetExceededError,
    ExecutionError,
    ExecutionMode,
    NoEligibleAgentError,
    PermissionDeniedError,
    _identifier,
)
from .permissions import PermissionAuthorizer, PermissionRequest

_DIRECT_TRANSPORT_KEYS = frozenset(
    {
        "endpoint",
        "url",
        "base_url",
        "api_url",
        "provider_url",
        "api_key",
        "token",
        "authorization",
    }
)


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
        if not isinstance(coordinator, AgentSpec):
            raise KernelContractError("coordinator must be AgentSpec")
        if coordinator.role is not AgentRole.COORDINATOR:
            raise KernelContractError("coordinator AgentSpec must use coordinator role")
        if any(not isinstance(worker, AgentSpec) for worker in workers):
            raise KernelContractError("workers must contain only AgentSpec values")
        if any(worker.role is not AgentRole.WORKER for worker in workers):
            raise KernelContractError("worker AgentSpecs must use worker role")

        specs = (coordinator, *workers)
        if len({spec.agent_id for spec in specs}) != len(specs):
            raise KernelContractError("all Coordinator and Worker agent IDs must be unique")
        for spec in specs:
            self._validate_execution_spec(spec)

        self.coordinator = coordinator
        self.workers = tuple(sorted(workers, key=lambda item: item.agent_id))

    @classmethod
    def _validate_execution_spec(cls, spec: AgentSpec) -> None:
        if isinstance(spec.max_concurrency, bool) or not isinstance(spec.max_concurrency, int):
            raise KernelContractError("AgentSpec max_concurrency must be an integer")
        if not 1 <= spec.max_concurrency <= 64:
            raise KernelContractError("AgentSpec max_concurrency must be between 1 and 64")
        if not isinstance(spec.budget_limits, BudgetLimit):
            raise KernelContractError("AgentSpec budget_limits must be BudgetLimit")
        if not isinstance(spec.permission_scope, PermissionScope):
            raise KernelContractError("AgentSpec permission_scope must be PermissionScope")
        for name in (
            "network_read",
            "network_write",
            "subprocess",
            "private_memory",
            "git_write",
        ):
            if type(getattr(spec.permission_scope, name)) is not bool:
                raise KernelContractError(f"AgentSpec permission {name} must be boolean")
        if not isinstance(spec.model_policy, Mapping) or not spec.model_policy:
            raise KernelContractError("AgentSpec model_policy must name a Gateway policy")
        cls._reject_direct_transport(spec.model_policy)
        for name in ("input_contract", "output_contract"):
            contract = getattr(spec, name)
            if not isinstance(contract, Mapping) or not contract:
                raise KernelContractError(f"AgentSpec {name} must be a non-empty object contract")
            if contract.get("type") != "object":
                raise KernelContractError(f"AgentSpec {name} type must be object")
            cls._contract_required_fields(contract)

    @classmethod
    def _reject_direct_transport(cls, value: Any, *, field_name: str = "model_policy") -> None:
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                key = str(raw_key).strip().lower()
                if (
                    key in _DIRECT_TRANSPORT_KEYS
                    or key.endswith("_endpoint")
                    or key.endswith("_url")
                ):
                    raise KernelContractError(
                        f"{field_name} must reference Gateway policy, not direct transport"
                    )
                cls._reject_direct_transport(item, field_name=field_name)
        elif isinstance(value, list):
            for item in value:
                cls._reject_direct_transport(item, field_name=field_name)
        elif isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            raise KernelContractError(
                f"{field_name} must reference Gateway policy, not direct transport"
            )

    @staticmethod
    def _contract_required_fields(contract: Mapping[str, Any]) -> tuple[str, ...]:
        raw = contract.get("required", ()) if isinstance(contract, Mapping) else ()
        if not isinstance(raw, (list, tuple)):
            raise KernelContractError("contract required field must be a list")
        values = tuple(_identifier(value, "contract field") for value in raw)
        if len(set(values)) != len(values):
            raise KernelContractError("contract required fields must be unique")
        return values

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
        if type(prefer_direct) is not bool:
            raise KernelContractError("prefer_direct must be boolean")
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
