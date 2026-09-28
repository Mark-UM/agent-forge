"""Internal lifecycle helpers for the bounded Kernel execution engine."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any, Iterator, Mapping

from modules.common.run import EventLevel, EventType, RunRecorder, RunResult, RunStatus

from .agents import AgentResult, AgentRuntime, CancellationToken
from .contracts import (
    AgentSpec,
    BudgetLimit,
    BudgetUsage,
    FailureCategory,
    FailureInfo,
    KernelContractError,
    Task,
    TaskStatus,
)
from .coordinator import CoordinatorPlan, DeterministicCoordinator
from .execution_support import (
    AgentBusyError,
    AgentResultStatus,
    BudgetExceededError,
    ExecutionCancelled,
    PermissionDeniedError,
    _add_usage,
    _usage_dict,
)
from .executors import ExecutorRuntime, LegacyAgentRuntimeAdapter
from .repository import TaskRepository


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    task: Task
    run: RunResult
    plan: CoordinatorPlan | None
    agent_result: AgentResult | None

    @property
    def success(self) -> bool:
        return self.task.status in {TaskStatus.SUCCEEDED, TaskStatus.DEGRADED}


class _ExecutionEngineBase:
    """Single-attempt bounded Coordinator/Worker execution loop."""

    def __init__(
        self,
        repository: TaskRepository,
        coordinator: DeterministicCoordinator,
        runtimes: Mapping[str, AgentRuntime | ExecutorRuntime],
        *,
        lease_seconds: int = 300,
    ) -> None:
        if not isinstance(repository, TaskRepository):
            raise TypeError("repository must be TaskRepository")
        if not isinstance(coordinator, DeterministicCoordinator):
            raise TypeError("coordinator must be DeterministicCoordinator")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int):
            raise ValueError("lease_seconds must be an integer")
        if not 1 <= lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        self.repository = repository
        self.coordinator = coordinator
        self.runtimes = dict(runtimes)
        self.lease_seconds = lease_seconds
        self._active_by_agent: dict[str, int] = {}
        self._active_lock = Lock()
        specs = (coordinator.coordinator, *coordinator.workers)
        self._specs = {spec.agent_id: spec for spec in specs}
        unknown_runtime_ids = sorted(set(self.runtimes) - set(self._specs))
        if unknown_runtime_ids:
            raise KernelContractError(
                "runtimes contain undeclared Agent IDs: " + ", ".join(unknown_runtime_ids)
            )
        if any(not hasattr(runtime, "execute") for runtime in self.runtimes.values()):
            raise KernelContractError("each Agent runtime must expose execute()")
        self.executors: dict[str, ExecutorRuntime] = {
            agent_id: (
                runtime
                if isinstance(runtime, ExecutorRuntime)
                else LegacyAgentRuntimeAdapter(
                    runtime, capabilities=self._specs[agent_id].capabilities
                )
            )
            for agent_id, runtime in self.runtimes.items()
        }

    @contextmanager
    def _agent_slot(self, spec: AgentSpec) -> Iterator[None]:
        with self._active_lock:
            active = self._active_by_agent.get(spec.agent_id, 0)
            if active >= spec.max_concurrency:
                raise AgentBusyError(f"agent {spec.agent_id} is at max_concurrency")
            self._active_by_agent[spec.agent_id] = active + 1
        try:
            yield
        finally:
            with self._active_lock:
                remaining = self._active_by_agent.get(spec.agent_id, 1) - 1
                if remaining <= 0:
                    self._active_by_agent.pop(spec.agent_id, None)
                else:
                    self._active_by_agent[spec.agent_id] = remaining

    @staticmethod
    def _safe_runtime_failure(exc: Exception) -> AgentResult:
        if isinstance(exc, ExecutionCancelled):
            return AgentResult.cancelled("execution cancelled by cooperative token")
        if isinstance(exc, TimeoutError):
            return AgentResult.timed_out("agent runtime exceeded its bounded deadline")
        if isinstance(exc, AgentBusyError):
            return AgentResult.failed(
                "selected Agent has no concurrency slot",
                category=FailureCategory.EXECUTION,
                code="agent_busy",
                retryable=True,
            )
        return AgentResult.failed(
            f"agent runtime raised {exc.__class__.__name__}",
            category=FailureCategory.EXECUTION,
            code="agent_exception",
            retryable=False,
        )

    @staticmethod
    def _failure_result(exc: Exception) -> AgentResult:
        if isinstance(exc, PermissionDeniedError):
            return AgentResult.failed(
                str(exc),
                category=FailureCategory.VALIDATION,
                code="permission_denied",
            )
        if isinstance(exc, BudgetExceededError):
            return AgentResult.failed(
                str(exc),
                category=FailureCategory.BUDGET_EXHAUSTION,
                code="budget_exhausted",
            )
        if isinstance(exc, KernelContractError):
            return AgentResult.failed(
                str(exc),
                category=FailureCategory.VALIDATION,
                code="contract_invalid",
            )
        return AgentResult.failed(
            str(exc),
            category=FailureCategory.CONFIGURATION,
            code="no_eligible_agent",
        )

    @staticmethod
    def _task_status(result: AgentResult) -> TaskStatus:
        return {
            AgentResultStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
            AgentResultStatus.DEGRADED: TaskStatus.DEGRADED,
            AgentResultStatus.FAILED: TaskStatus.FAILED,
            AgentResultStatus.CANCELLED: TaskStatus.CANCELLED,
            AgentResultStatus.TIMED_OUT: TaskStatus.TIMED_OUT,
        }[result.status]

    @staticmethod
    def _mark_step(step: Any, result: AgentResult, data: Mapping[str, Any]) -> None:
        if result.status is AgentResultStatus.SUCCEEDED:
            step.succeed(data=dict(data))
        elif result.status is AgentResultStatus.DEGRADED:
            step.degrade(
                data=dict(data),
                reason=result.degraded.summary if result.degraded else "degraded",
            )
        elif result.status is AgentResultStatus.TIMED_OUT:
            step.time_out(result.failure.message if result.failure else "timed out")
        elif result.status is AgentResultStatus.CANCELLED:
            step.skip(reason=result.failure.message if result.failure else "cancelled")
        else:
            step.fail(
                code=result.failure.code or "failed" if result.failure else "failed",
                message=result.failure.message if result.failure else "execution failed",
            )

    @staticmethod
    def _normalise_run_terminal(
        recorder: RunRecorder,
        task_status: TaskStatus,
        failure: FailureInfo | None,
    ) -> RunResult:
        desired = {
            TaskStatus.SUCCEEDED: RunStatus.SUCCEEDED,
            TaskStatus.DEGRADED: RunStatus.DEGRADED,
            TaskStatus.FAILED: RunStatus.FAILED,
            TaskStatus.CANCELLED: RunStatus.ABANDONED,
            TaskStatus.TIMED_OUT: RunStatus.TIMED_OUT,
            TaskStatus.ABANDONED: RunStatus.ABANDONED,
        }[task_status]
        recorder.run.status = desired
        recorder.run.error = failure.message if failure else None
        recorder.run.metadata["task_terminal_status"] = task_status.value
        if task_status is TaskStatus.CANCELLED:
            if recorder.run.events and recorder.run.events[-1].type in {
                EventType.RUN_COMPLETED,
                EventType.RUN_FAILED,
            }:
                recorder.run.events.pop()
            recorder.event(
                EventType.CUSTOM,
                level=EventLevel.WARN,
                payload={"custom_type": "kernel.task.cancelled"},
            )
        elif task_status is TaskStatus.FAILED:
            for event in reversed(recorder.run.events):
                if event.type == EventType.RUN_FAILED:
                    event.payload = {"error": recorder.run.error or ""}
                    break
        return RunResult.from_run(recorder.run)

    def _claim_lease_seconds(self, task: Task) -> int:
        """Cover the remaining bounded Task duration plus reconciliation grace."""
        remaining = max(
            0,
            task.budget.wall_clock_seconds - task.budget_used.wall_clock_seconds,
        )
        grace = max(30, min(self.lease_seconds, 300))
        required = max(self.lease_seconds, remaining + grace)
        if required > 86_400:
            raise BudgetExceededError(
                "remaining Task wall-clock budget exceeds maximum claim lease"
            )
        return required

    @staticmethod
    def _bounded_usage(value: BudgetUsage, limit: BudgetLimit) -> BudgetUsage:
        return BudgetUsage(
            **{
                name: min(getattr(value, name), getattr(limit, name))
                for name in BudgetUsage.__dataclass_fields__
            }
        )

    @staticmethod
    def _runtime_budget_failure(
        *,
        current: BudgetUsage,
        reported: BudgetUsage,
        limit: BudgetLimit,
    ) -> tuple[BudgetUsage, AgentResult]:
        attempted = _add_usage(current, reported)
        exceeded = [
            name
            for name in BudgetUsage.__dataclass_fields__
            if getattr(attempted, name) > getattr(limit, name)
        ]
        details = {
            "exceeded_dimensions": exceeded,
            "reported_usage": _usage_dict(reported),
            "attempted_usage": _usage_dict(attempted),
            "budget_limit": _usage_dict(limit),
        }
        bounded = _ExecutionEngineBase._bounded_usage(attempted, limit)
        if "wall_clock_seconds" in exceeded:
            return bounded, AgentResult.timed_out(
                "Agent usage exceeded the Task wall-clock budget",
                usage=reported,
                details=details,
            )
        return bounded, AgentResult.failed(
            "Agent usage exceeded the remaining Task budget",
            category=FailureCategory.BUDGET_EXHAUSTION,
            code="budget_exhausted",
            usage=reported,
            details=details,
        )

    def _cancel_queued_task(
        self,
        task: Task,
        *,
        token: CancellationToken,
        correlation_id: str,
    ) -> ExecutionOutcome:
        result = AgentResult.cancelled(token.reason)
        recorder = RunRecorder(
            run_type="kernel.task",
            run_id=correlation_id,
            metadata={
                "task_id": task.task_id,
                "owner_agent_id": self.coordinator.coordinator.agent_id,
                "attempt": 1,
                "authority": "kernel.sqlite",
            },
        )
        with recorder:
            with recorder.step("coordinator.plan") as step:
                self._mark_step(step, result, result.checkpoint_summary())
        run_result = self._normalise_run_terminal(
            recorder, TaskStatus.CANCELLED, result.failure
        )
        final_task, _ = self.repository.transition_task(
            task.task_id,
            TaskStatus.CANCELLED,
            expected_version=task.record_version,
            run_id=correlation_id,
            current_step="coordinator.plan",
            budget_used=task.budget_used,
            failure=result.failure,
            checkpoint_payload={
                "event": "execution_cancelled_before_claim",
                "run_id": correlation_id,
                "task_status": TaskStatus.CANCELLED.value,
                "plan": None,
                "agent_result": result.checkpoint_summary(),
                "budget_used": _usage_dict(task.budget_used),
            },
            control_plane=True,
        )
        return ExecutionOutcome(
            task=final_task, run=run_result, plan=None, agent_result=result
        )
