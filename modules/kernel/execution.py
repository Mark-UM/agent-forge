"""Bounded Coordinator/Worker execution engine for Agent Forge 2.0 Slice 2.

The Coordinator remains Task owner. Worker execution is Agent-as-tool through
an injected deterministic ``AgentRuntime``; explicit ownership transfer remains
a later Handoff concern.
"""
from __future__ import annotations

from modules.common.run import RunRecorder

from ._engine_base import ExecutionOutcome, _ExecutionEngineBase
from .agents import AgentCommand, AgentResult, CancellationToken
from .contracts import BudgetUsage, FailureCategory, KernelContractError, TaskStatus, new_id
from .coordinator import CoordinatorPlan
from .execution_support import (
    AgentResultStatus,
    BudgetExceededError,
    DuplicateExecutionError,
    ExecutionError,
    ExecutionMode,
    _add_usage,
    _identifier,
    _remaining,
    _usage_dict,
    _within_after,
)
from .permissions import PermissionRequest
from .repository import (
    ClaimConflictError,
    ConcurrencyConflictError,
    TaskNotFoundError,
)
from .state import TASK_TERMINAL


class KernelExecutionEngine(_ExecutionEngineBase):
    """Single-attempt bounded Coordinator/Worker execution loop."""

    def execute_task(
        self,
        task_id: str,
        *,
        tool_name: str | None = None,
        permission_request: PermissionRequest | None = None,
        prefer_direct: bool = True,
        cancellation: CancellationToken | None = None,
        run_id: str | None = None,
    ) -> ExecutionOutcome:
        task = self.repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        if task.status in TASK_TERMINAL or task.status is not TaskStatus.QUEUED:
            raise DuplicateExecutionError(
                f"task {task.task_id} is already {task.status.value}; execution will not repeat"
            )

        token = cancellation or CancellationToken()
        request = permission_request or PermissionRequest()
        correlation_id = _identifier(run_id or task.run_id or f"run_{task.task_id}", "run_id")
        if token.cancelled:
            return self._cancel_queued_task(
                task, token=token, correlation_id=correlation_id
            )
        try:
            claimed, _ = self.repository.claim_task(
                task.task_id,
                claimant=self.coordinator.coordinator.agent_id,
                expected_version=task.record_version,
                lease_seconds=self.lease_seconds,
            )
        except (ClaimConflictError, ConcurrencyConflictError) as exc:
            raise DuplicateExecutionError(
                f"task {task.task_id} is already owned by another executor"
            ) from exc

        usage = claimed.budget_used
        plan: CoordinatorPlan | None = None
        agent_result: AgentResult | None = None
        last_step = "coordinator.plan"
        recorder = RunRecorder(
            run_type="kernel.task",
            run_id=correlation_id,
            metadata={
                "task_id": claimed.task_id,
                "owner_agent_id": self.coordinator.coordinator.agent_id,
                "attempt": 1,
                "authority": "kernel.sqlite",
            },
        )

        with recorder:
            with recorder.step("coordinator.plan") as step:
                if not _within_after(
                    usage,
                    BudgetUsage(steps=1, agent_count=1),
                    claimed.budget,
                ):
                    agent_result = AgentResult.failed(
                        "Task budget cannot start Coordinator planning",
                        category=FailureCategory.BUDGET_EXHAUSTION,
                        code="budget_exhausted",
                    )
                    self._mark_step(step, agent_result, agent_result.checkpoint_summary())
                else:
                    usage = _add_usage(usage, BudgetUsage(steps=1, agent_count=1))
                    try:
                        plan = self.coordinator.plan(
                            claimed,
                            available_agent_ids=frozenset(self.runtimes),
                            permission_request=request,
                            tool_name=tool_name,
                            prefer_direct=prefer_direct,
                        )
                    except (ExecutionError, KernelContractError) as exc:
                        agent_result = self._failure_result(exc)
                        self._mark_step(step, agent_result, agent_result.checkpoint_summary())
                    else:
                        recorder.run.metadata.update(
                            {
                                "execution_mode": plan.mode.value,
                                "selected_agent_id": plan.selected_agent_id,
                            }
                        )
                        step.succeed(data=plan.telemetry())

            if plan is not None and agent_result is None:
                last_step = "agent.execute"
                with recorder.step("agent.execute") as step:
                    selected = self._specs[plan.selected_agent_id]
                    structural = BudgetUsage(
                        steps=1,
                        tool_calls=1 if tool_name else 0,
                        agent_count=0 if plan.mode is ExecutionMode.DIRECT else 1,
                    )
                    if not _within_after(usage, structural, claimed.budget):
                        agent_result = AgentResult.failed(
                            "Task budget cannot start selected Agent operation",
                            category=FailureCategory.BUDGET_EXHAUSTION,
                            code="budget_exhausted",
                        )
                        self._mark_step(step, agent_result, agent_result.checkpoint_summary())
                    elif token.cancelled:
                        usage = _add_usage(usage, structural)
                        agent_result = AgentResult.cancelled(token.reason)
                        self._mark_step(step, agent_result, agent_result.checkpoint_summary())
                    else:
                        command = AgentCommand(
                            command_id=new_id("command"),
                            task_id=claimed.task_id,
                            agent_id=selected.agent_id,
                            objective=claimed.objective,
                            normalized_input=claimed.normalized_input,
                            required_capabilities=claimed.required_capabilities,
                            remaining_budget=_remaining(
                                claimed.budget, _add_usage(usage, structural)
                            ),
                            permission_request=request,
                            tool_name=tool_name,
                        )
                        runtime = self.runtimes[selected.agent_id]
                        try:
                            with self._agent_slot(selected):
                                runtime_result = runtime.execute(command, token)
                            if not isinstance(runtime_result, AgentResult):
                                raise TypeError("AgentRuntime must return AgentResult")
                            self.coordinator.validate_output(selected, runtime_result)
                            attempted_usage = _add_usage(usage, structural, runtime_result.usage)
                            if not attempted_usage.within(claimed.budget):
                                raise BudgetExceededError(
                                    "Agent usage exceeded the remaining Task budget"
                                )
                            usage = attempted_usage
                            agent_result = runtime_result
                        except BudgetExceededError as exc:
                            usage = _add_usage(usage, structural)
                            agent_result = self._failure_result(exc)
                        except KernelContractError as exc:
                            usage = _add_usage(usage, structural)
                            agent_result = self._failure_result(exc)
                        except BaseException as exc:
                            usage = _add_usage(usage, structural)
                            agent_result = self._safe_runtime_failure(exc)
                        self._mark_step(step, agent_result, agent_result.checkpoint_summary())

        assert agent_result is not None
        terminal = self._task_status(agent_result)
        run_result = self._normalise_run_terminal(
            recorder,
            terminal,
            agent_result.failure,
        )
        checkpoint_payload: dict[str, Any] = {
            "event": "execution_terminal",
            "run_id": correlation_id,
            "task_status": terminal.value,
            "plan": plan.telemetry() if plan else None,
            "agent_result": agent_result.checkpoint_summary(),
            "budget_used": _usage_dict(usage),
        }
        final_task, _ = self.repository.transition_task(
            claimed.task_id,
            terminal,
            expected_version=claimed.record_version,
            owner_agent_id=self.coordinator.coordinator.agent_id,
            current_step=last_step,
            budget_used=usage,
            failure=agent_result.failure,
            degraded=agent_result.degraded,
            checkpoint_payload=checkpoint_payload,
            claim_token=claimed.claim_token,
        )
        return ExecutionOutcome(
            task=final_task,
            run=run_result,
            plan=plan,
            agent_result=agent_result,
        )
