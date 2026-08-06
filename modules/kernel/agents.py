"""Typed Agent commands, results, cancellation, and deterministic runtimes."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from threading import Event as ThreadEvent
from typing import Any, Callable, Mapping, Protocol

from .contracts import (
    BudgetLimit,
    BudgetUsage,
    DegradedInfo,
    FailureCategory,
    FailureInfo,
    KernelContractError,
    canonical_json,
    normalise_json_value,
)
from .execution_support import (
    AgentResultStatus,
    ExecutionCancelled,
    _identifier,
    _usage_dict,
)
from .permissions import PermissionRequest

@dataclass(frozen=True, slots=True)
class AgentCommand:
    command_id: str
    task_id: str
    agent_id: str
    objective: str
    normalized_input: Mapping[str, Any]
    required_capabilities: tuple[str, ...]
    remaining_budget: BudgetLimit
    permission_request: PermissionRequest = field(default_factory=PermissionRequest)
    tool_name: str | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        for name in ("command_id", "task_id", "agent_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise KernelContractError("objective must be a non-empty string")
        object.__setattr__(
            self,
            "normalized_input",
            normalise_json_value(self.normalized_input, field_name="command_input"),
        )
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(_identifier(value, "capability") for value in self.required_capabilities),
        )
        if self.tool_name is not None:
            object.__setattr__(self, "tool_name", _identifier(self.tool_name, "tool_name"))
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt != 1:
            raise KernelContractError("Slice 2 supports exactly one bounded attempt")

    def telemetry(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "required_capabilities": list(self.required_capabilities),
            "tool_name": self.tool_name,
            "input_keys": sorted(self.normalized_input.keys()),
            "attempt": self.attempt,
        }


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: AgentResultStatus
    output: Mapping[str, Any] = field(default_factory=dict)
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    failure: FailureInfo | None = None
    degraded: DegradedInfo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, AgentResultStatus):
            raise KernelContractError("agent result status must be AgentResultStatus")
        object.__setattr__(
            self,
            "output",
            normalise_json_value(self.output, field_name="agent_output"),
        )
        engine_owned = ("steps", "tool_calls", "agent_count", "child_tasks")
        if any(getattr(self.usage, name) for name in engine_owned):
            raise KernelContractError(
                "AgentResult usage must not charge engine-owned structural counters"
            )
        if self.status is AgentResultStatus.SUCCEEDED:
            if not self.output or self.failure is not None or self.degraded is not None:
                raise KernelContractError("succeeded AgentResult requires usable output only")
        elif self.status is AgentResultStatus.DEGRADED:
            if not self.output or self.degraded is None or self.failure is not None:
                raise KernelContractError("degraded AgentResult requires usable output and details")
        else:
            if self.failure is None or self.degraded is not None:
                raise KernelContractError("non-success AgentResult requires failure information")
        if self.status is AgentResultStatus.CANCELLED and self.failure:
            if self.failure.category is not FailureCategory.CANCELLATION:
                raise KernelContractError("cancelled result requires cancellation failure category")
        if self.status is AgentResultStatus.TIMED_OUT and self.failure:
            if self.failure.category is not FailureCategory.TIMEOUT:
                raise KernelContractError("timed-out result requires timeout failure category")

    @classmethod
    def succeeded(
        cls, output: Mapping[str, Any], *, usage: BudgetUsage | None = None
    ) -> "AgentResult":
        return cls(AgentResultStatus.SUCCEEDED, output=output, usage=usage or BudgetUsage())

    @classmethod
    def failed(
        cls,
        message: str,
        *,
        category: FailureCategory = FailureCategory.EXECUTION,
        code: str = "agent_failed",
        retryable: bool = False,
        usage: BudgetUsage | None = None,
    ) -> "AgentResult":
        return cls(
            AgentResultStatus.FAILED,
            usage=usage or BudgetUsage(),
            failure=FailureInfo(category, message, retryable=retryable, code=code),
        )

    @classmethod
    def cancelled(cls, message: str = "execution cancelled") -> "AgentResult":
        return cls(
            AgentResultStatus.CANCELLED,
            failure=FailureInfo(
                FailureCategory.CANCELLATION,
                message,
                retryable=False,
                code="cancelled",
            ),
        )

    @classmethod
    def timed_out(cls, message: str = "execution timed out") -> "AgentResult":
        return cls(
            AgentResultStatus.TIMED_OUT,
            failure=FailureInfo(
                FailureCategory.TIMEOUT,
                message,
                retryable=False,
                code="timed_out",
            ),
        )

    def checkpoint_summary(self) -> dict[str, Any]:
        output_json = canonical_json(self.output)
        summary: dict[str, Any] = {
            "status": self.status.value,
            "usage": _usage_dict(self.usage),
            "output_keys": sorted(self.output.keys()),
            "output_digest": "sha256:" + hashlib.sha256(output_json.encode("utf-8")).hexdigest(),
        }
        if self.failure is not None:
            summary["failure"] = {
                "category": self.failure.category.value,
                "code": self.failure.code,
                "retryable": self.failure.retryable,
                "message": self.failure.message,
            }
        if self.degraded is not None:
            summary["degraded"] = {
                "summary": self.degraded.summary,
                "missing_capabilities": list(self.degraded.missing_capabilities),
            }
        return summary


class AgentRuntime(Protocol):
    def execute(self, command: AgentCommand, cancellation: "CancellationToken") -> AgentResult:
        """Execute one command without owning Task state transitions."""


class CancellationToken:
    """Thread-safe cooperative cancellation used by deterministic runtimes."""

    def __init__(self) -> None:
        self._event = ThreadEvent()
        self._reason = "execution cancelled"

    def cancel(self, reason: str = "execution cancelled") -> None:
        self._reason = reason.strip() or "execution cancelled"
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ExecutionCancelled(self.reason)


class DeterministicAgentRuntime:
    """In-memory deterministic runtime for Slice 2 tests and PoC equivalence."""

    def __init__(
        self,
        script: AgentResult
        | Callable[[AgentCommand, CancellationToken], AgentResult],
    ) -> None:
        self._script = script
        self.calls: list[AgentCommand] = []

    def execute(self, command: AgentCommand, cancellation: CancellationToken) -> AgentResult:
        self.calls.append(command)
        cancellation.raise_if_cancelled()
        result = self._script(command, cancellation) if callable(self._script) else self._script
        if not isinstance(result, AgentResult):
            raise TypeError("AgentRuntime must return AgentResult")
        return result


