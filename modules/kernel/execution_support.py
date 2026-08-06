"""Shared errors, enums, and budget arithmetic for Kernel Slice 2 execution."""
from __future__ import annotations

from enum import Enum
import re

from .contracts import BudgetLimit, BudgetUsage, KernelContractError

class ExecutionError(RuntimeError):
    """Base error for the Slice 2 execution boundary."""


class DuplicateExecutionError(ExecutionError):
    """Raised when work is already claimed or terminal and must not repeat."""


class NoEligibleAgentError(ExecutionError):
    """Raised when no ready Agent can satisfy the Task contract."""


class PermissionDeniedError(ExecutionError):
    """Raised before an Agent runtime is invoked outside its declared scope."""


class BudgetExceededError(ExecutionError):
    """Raised when the next protected operation cannot fit the Task budget."""


class AgentBusyError(ExecutionError):
    """Raised when an AgentSpec max-concurrency slot is unavailable."""


class ExecutionCancelled(ExecutionError):
    """A deterministic runtime cancellation signal."""


class ExecutionMode(str, Enum):
    DIRECT = "direct"
    WORKER_AS_TOOL = "worker_as_tool"


class AgentResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KernelContractError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if len(result) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", result):
        raise KernelContractError(f"{field_name} has invalid characters or length")
    return result


def _usage_dict(value: BudgetUsage | BudgetLimit) -> dict[str, int]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def _add_usage(*values: BudgetUsage) -> BudgetUsage:
    return BudgetUsage(
        **{
            name: sum(getattr(value, name) for value in values)
            for name in BudgetUsage.__dataclass_fields__
        }
    )


def _remaining(limit: BudgetLimit, used: BudgetUsage) -> BudgetLimit:
    return BudgetLimit(
        **{
            name: max(0, getattr(limit, name) - getattr(used, name))
            for name in BudgetLimit.__dataclass_fields__
        }
    )


def _within_after(current: BudgetUsage, addition: BudgetUsage, limit: BudgetLimit) -> bool:
    return _add_usage(current, addition).within(limit)


