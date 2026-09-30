"""Versioned, Kernel-owned Executor boundary and deterministic test runtime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from .agents import AgentCommand, AgentResult, AgentRuntime, CancellationToken
from .contracts import MAX_ITEMS, FailureCategory, KernelContractError, WorkspaceBinding, normalise_json_value
from .execution_support import _identifier


EXECUTOR_PROTOCOL_VERSION = 1
EXECUTOR_EVENT_TYPES = frozenset(
    {
        "executor.started",
        "agent.started",
        "turn.started",
        "tool.requested",
        "tool.started",
        "tool.finished",
        "context.compacted",
        "checkpoint.created",
        "usage.updated",
        "artifact.candidate",
        "executor.finished",
    }
)


def _protocol_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KernelContractError("executor protocol_version must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class ExecutorCapabilities:
    protocol_version: int
    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _protocol_version(self.protocol_version)
        if not isinstance(self.names, (tuple, list)) or len(self.names) > MAX_ITEMS:
            raise KernelContractError("executor capabilities must be a bounded sequence")
        names = tuple(_identifier(name, "executor capability") for name in self.names)
        if len(set(names)) != len(names):
            raise KernelContractError("executor capabilities must be unique")
        object.__setattr__(self, "names", names)


@dataclass(frozen=True, slots=True)
class ExecutorHealth:
    available: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise KernelContractError("executor health available must be boolean")
        if not isinstance(self.reason, str) or len(self.reason) > 256:
            raise KernelContractError("executor health reason must be a short string")
        normalise_json_value(self.reason, field_name="executor_health_reason")


@dataclass(frozen=True, slots=True)
class ExecutorRequest:
    protocol_version: int
    request_id: str
    command: AgentCommand
    workspace: WorkspaceBinding | None = None

    def __post_init__(self) -> None:
        _protocol_version(self.protocol_version)
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        if not isinstance(self.command, AgentCommand):
            raise KernelContractError("executor request command must be AgentCommand")
        if self.request_id != self.command.command_id:
            raise KernelContractError("executor request_id must match command_id")
        if self.workspace is not None and not isinstance(self.workspace, WorkspaceBinding):
            raise KernelContractError("executor request workspace must be WorkspaceBinding")


@dataclass(frozen=True, slots=True)
class ExecutorEvent:
    request_id: str
    sequence: int
    event_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise KernelContractError("executor event sequence must be an integer")
        if self.sequence < 1:
            raise KernelContractError("executor event sequence must be positive")
        if not isinstance(self.event_type, str) or self.event_type not in EXECUTOR_EVENT_TYPES:
            raise KernelContractError("executor event type is unsupported")


@dataclass(frozen=True, slots=True)
class ExecutorResult:
    protocol_version: int
    request_id: str
    agent_result: AgentResult
    events: tuple[ExecutorEvent, ...] = ()

    def __post_init__(self) -> None:
        _protocol_version(self.protocol_version)
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        if not isinstance(self.agent_result, AgentResult):
            raise KernelContractError("executor result must contain AgentResult")
        if not isinstance(self.events, (tuple, list)) or len(self.events) > MAX_ITEMS:
            raise KernelContractError("executor events must be a bounded sequence")
        events = tuple(self.events)
        if any(not isinstance(event, ExecutorEvent) for event in events):
            raise KernelContractError("executor events must be ExecutorEvent")
        if any(event.request_id != self.request_id for event in events):
            raise KernelContractError("executor event request_id mismatch")
        if tuple(event.sequence for event in events) != tuple(range(1, len(events) + 1)):
            raise KernelContractError("executor event sequence must be contiguous")
        object.__setattr__(self, "events", events)


@runtime_checkable
class ExecutorRuntime(Protocol):
    def health(self) -> ExecutorHealth:
        """Report current readiness before a protected invocation."""

    def capabilities(self) -> ExecutorCapabilities:
        """Report negotiated protocol and capabilities."""

    def execute(
        self, request: ExecutorRequest, cancellation: CancellationToken
    ) -> ExecutorResult:
        """Run one bounded request without owning Kernel Task state."""


class LegacyAgentRuntimeAdapter:
    """Keep existing AgentRuntime callers on the versioned Executor path."""

    def __init__(self, runtime: AgentRuntime, *, capabilities: tuple[str, ...]) -> None:
        if not hasattr(runtime, "execute"):
            raise KernelContractError("legacy AgentRuntime must expose execute()")
        self._runtime = runtime
        self._capabilities = ExecutorCapabilities(
            EXECUTOR_PROTOCOL_VERSION, capabilities
        )

    def health(self) -> ExecutorHealth:
        return ExecutorHealth(True)

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def execute(
        self, request: ExecutorRequest, cancellation: CancellationToken
    ) -> ExecutorResult:
        result = self._runtime.execute(request.command, cancellation)
        if not isinstance(result, AgentResult):
            raise TypeError("AgentRuntime must return AgentResult")
        return ExecutorResult(request.protocol_version, request.request_id, result)


FakeScript = (
    AgentResult
    | ExecutorResult
    | Callable[[ExecutorRequest, CancellationToken], AgentResult | ExecutorResult]
)


class FakeExecutor:
    """Deterministic Executor for Kernel contract and integration tests."""

    def __init__(
        self,
        script: FakeScript,
        *,
        capabilities: tuple[str, ...] = (),
        available: bool = True,
        protocol_version: int = EXECUTOR_PROTOCOL_VERSION,
    ) -> None:
        self._script = script
        self._health = ExecutorHealth(available)
        self._capabilities = ExecutorCapabilities(protocol_version, capabilities)
        self.calls: list[ExecutorRequest] = []
        self.last_result: ExecutorResult | None = None

    def health(self) -> ExecutorHealth:
        return self._health

    def capabilities(self) -> ExecutorCapabilities:
        return self._capabilities

    def execute(
        self, request: ExecutorRequest, cancellation: CancellationToken
    ) -> ExecutorResult:
        cancellation.raise_if_cancelled()
        self.calls.append(request)
        result = (
            self._script(request, cancellation)
            if callable(self._script)
            else self._script
        )
        if isinstance(result, AgentResult):
            result = ExecutorResult(request.protocol_version, request.request_id, result)
        if not isinstance(result, ExecutorResult):
            raise TypeError("FakeExecutor script must return ExecutorResult")
        self.last_result = result
        return result


def invoke_executor(
    executor: ExecutorRuntime,
    command: AgentCommand,
    cancellation: CancellationToken,
    *,
    workspace: WorkspaceBinding | None = None,
) -> AgentResult:
    """Validate the handshake and exact response before reducing Task state."""

    health = executor.health()
    cancellation.raise_if_cancelled()
    if not isinstance(health, ExecutorHealth):
        raise KernelContractError("executor health response is invalid")
    if not health.available:
        return AgentResult.failed(
            "selected Executor is unavailable",
            category=FailureCategory.CONFIGURATION,
            code="executor_unavailable",
        )
    capabilities = executor.capabilities()
    cancellation.raise_if_cancelled()
    if not isinstance(capabilities, ExecutorCapabilities):
        raise KernelContractError("executor capabilities response is invalid")
    if capabilities.protocol_version != EXECUTOR_PROTOCOL_VERSION:
        raise KernelContractError("executor protocol version is unsupported")
    missing = sorted(set(command.required_capabilities) - set(capabilities.names))
    if missing:
        raise KernelContractError(
            "executor lacks required capabilities: " + ", ".join(missing)
        )
    if workspace is not None:
        workspace.validate_current()
    request = ExecutorRequest(EXECUTOR_PROTOCOL_VERSION, command.command_id, command, workspace)
    cancellation.raise_if_cancelled()
    response = executor.execute(request, cancellation)
    cancellation.raise_if_cancelled()
    if not isinstance(response, ExecutorResult):
        raise KernelContractError("executor response must be ExecutorResult")
    if response.protocol_version != request.protocol_version:
        raise KernelContractError("executor response protocol version mismatch")
    if response.request_id != request.request_id:
        raise KernelContractError("executor response request_id mismatch")
    return response.agent_result
