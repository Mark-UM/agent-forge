"""Framework-neutral typed contracts for the Agent Forge 2.0 kernel."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

MAX_TEXT = 50_000
MAX_ITEMS = 1_000
_SECRET_KEYS = frozenset(
    {
        "api_key", "apikey", "access_token", "refresh_token", "authorization",
        "cookie", "cookies", "password", "passwd", "secret", "client_secret",
        "private_key", "bearer", "credential", "credentials",
    }
)
_SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY", "SERPER_API_KEY", "SILICONFLOW_API_KEY",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "BROWSER_USE_API_KEY",
    "GITHUB_TOKEN",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class KernelContractError(ValueError):
    pass


class SensitiveValueError(KernelContractError):
    pass


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    HANDOFF_PENDING = "handoff_pending"
    REVIEWING = "reviewing"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    WORKER = "worker"
    REVIEWER_TESTER = "reviewer_tester"


class HandoffStatus(str, Enum):
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    CONSUMED = "consumed"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    TIMED_OUT = "timed_out"


class ArtifactStatus(str, Enum):
    DECLARED = "declared"
    MATERIALIZED = "materialized"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEGRADED = "degraded"
    INVALIDATED = "invalidated"
    SUPERSEDED = "superseded"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CONSUMED = "consumed"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class FailureCategory(str, Enum):
    VALIDATION = "validation_failure"
    CONFIGURATION = "configuration_failure"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EXECUTION = "execution_failure"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    APPROVAL_DENIED = "approval_denied"
    ABANDONED = "abandoned_work"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    PROJECT = "project"
    PRIVATE = "private"
    SECRET_PROHIBITED = "secret_prohibited"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_utc_timestamp(value: str | None, *, field_name: str, allow_none: bool = True) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise KernelContractError(f"{field_name} is required")
    if not isinstance(value, str) or not value.strip():
        raise KernelContractError(f"{field_name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise KernelContractError(f"{field_name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KernelContractError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise KernelContractError("identifier prefix is invalid")
    return f"{prefix}_{uuid.uuid4().hex}"


def _name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KernelContractError(f"{field_name} must be a non-empty string")
    result = value.strip()
    if len(result) > 256 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", result):
        raise KernelContractError(f"{field_name} has invalid characters or length")
    return result


def _text(value: str, field_name: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise KernelContractError(f"{field_name} must be a string")
    result = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not result or len(result) > limit:
        raise KernelContractError(f"{field_name} is empty or exceeds {limit} characters")
    _check_sensitive(result)
    return result


def _check_sensitive(text: str) -> None:
    for name in _SECRET_ENV_NAMES:
        secret = os.environ.get(name, "").strip()
        if len(secret) >= 6 and secret in text:
            raise SensitiveValueError("value contains a known environment secret")
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        raise SensitiveValueError("value contains a prohibited secret pattern")


def normalise_json_value(value: Any, *, field_name: str = "value", depth: int = 0) -> Any:
    if depth > 12:
        raise KernelContractError(f"{field_name} exceeds nesting depth")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise KernelContractError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise KernelContractError(f"{field_name} contains an oversized string")
        _check_sensitive(value)
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, Mapping):
        if len(value) > MAX_ITEMS:
            raise KernelContractError(f"{field_name} contains too many items")
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise KernelContractError(f"{field_name} keys must be strings")
            key = raw_key.strip()
            if key.lower() in _SECRET_KEYS:
                raise SensitiveValueError(f"{field_name} contains prohibited key {key!r}")
            if key in result:
                raise KernelContractError(f"{field_name} has duplicate normalized keys")
            result[key] = normalise_json_value(raw_value, field_name=f"{field_name}.{key}", depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ITEMS:
            raise KernelContractError(f"{field_name} contains too many items")
        return [normalise_json_value(item, field_name=field_name, depth=depth + 1) for item in value]
    raise KernelContractError(f"{field_name} is not JSON-compatible")


def canonical_json(value: Any) -> str:
    return json.dumps(normalise_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class BudgetLimit:
    steps: int = 20
    tokens: int = 100_000
    wall_clock_seconds: int = 1_800
    retries: int = 2
    tool_calls: int = 50
    agent_count: int = 3
    child_tasks: int = 8

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KernelContractError(f"budget {name} must be a non-negative integer")

    def contains(self, other: "BudgetLimit") -> bool:
        return all(getattr(other, name) <= getattr(self, name) for name in self.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    steps: int = 0
    tokens: int = 0
    wall_clock_seconds: int = 0
    retries: int = 0
    tool_calls: int = 0
    agent_count: int = 0
    child_tasks: int = 0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KernelContractError(f"usage {name} must be a non-negative integer")

    def within(self, limit: BudgetLimit) -> bool:
        return all(getattr(self, name) <= getattr(limit, name) for name in self.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class FailureInfo:
    category: FailureCategory
    message: str
    retryable: bool = False
    code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.category, FailureCategory):
            raise KernelContractError("failure category must be FailureCategory")
        object.__setattr__(self, "message", _text(self.message, "failure message", 4_000))
        object.__setattr__(self, "details", normalise_json_value(self.details, field_name="failure details"))
        if self.code is not None:
            object.__setattr__(self, "code", _name(self.code, "failure code"))


@dataclass(frozen=True, slots=True)
class DegradedInfo:
    summary: str
    missing_capabilities: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _text(self.summary, "degraded summary", 4_000))
        object.__setattr__(self, "missing_capabilities", tuple(_name(x, "capability") for x in self.missing_capabilities))
        object.__setattr__(self, "details", normalise_json_value(self.details, field_name="degraded details"))


@dataclass(frozen=True, slots=True)
class PermissionScope:
    workspace_roots: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    network_read: bool = False
    network_write: bool = False
    subprocess: bool = False
    private_memory: bool = False
    git_write: bool = False

    def __post_init__(self) -> None:
        roots: list[str] = []
        for root in self.workspace_roots:
            value = str(root).replace("\\", "/").strip()
            if not value or value.startswith("/") or ".." in value.split("/") or re.match(r"^[A-Za-z]:", value):
                raise KernelContractError("workspace roots must be project-relative and non-escaping")
            roots.append(value)
        object.__setattr__(self, "workspace_roots", tuple(dict.fromkeys(roots)))
        object.__setattr__(self, "allowed_tools", tuple(dict.fromkeys(_name(x, "tool") for x in self.allowed_tools)))


@dataclass(frozen=True, slots=True)
class WorkspaceBinding:
    """Trusted caller's directory identity, persisted by Kernel with a Task."""

    workspace_id: str
    root: str
    device: int
    inode: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _name(self.workspace_id, "workspace_id"))
        if not isinstance(self.root, str) or not Path(self.root).is_absolute():
            raise KernelContractError("workspace root must be an absolute path")
        for name in ("device", "inode"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise KernelContractError(f"workspace {name} must be a non-negative integer")

    @classmethod
    def capture(cls, workspace_id: str, root: str | Path) -> "WorkspaceBinding":
        try:
            resolved = Path(root).resolve(strict=True)
            if not resolved.is_dir():
                raise KernelContractError("workspace root must be a directory")
            identity = resolved.stat()
        except (OSError, RuntimeError) as exc:
            raise KernelContractError("workspace root is unavailable") from exc
        return cls(workspace_id, str(resolved), identity.st_dev, identity.st_ino)

    def validate_current(self) -> None:
        try:
            resolved = Path(self.root).resolve(strict=True)
            identity = resolved.stat()
        except (OSError, RuntimeError) as exc:
            raise KernelContractError("workspace root is unavailable") from exc
        if (
            not resolved.is_dir()
            or str(resolved) != self.root
            or (identity.st_dev, identity.st_ino) != (self.device, self.inode)
        ):
            raise KernelContractError("workspace root identity changed")

    def to_dict(self) -> dict[str, str | int]:
        return {
            "workspace_id": self.workspace_id,
            "root": self.root,
            "device": self.device,
            "inode": self.inode,
        }


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    objective: str
    normalized_input: Mapping[str, Any]
    status: TaskStatus = TaskStatus.QUEUED
    parent_task_id: str | None = None
    owner_agent_id: str | None = None
    required_capabilities: tuple[str, ...] = ()
    budget: BudgetLimit = field(default_factory=BudgetLimit)
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    current_step: str | None = None
    artifact_ids: tuple[str, ...] = ()
    approval_ids: tuple[str, ...] = ()
    run_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    failure: FailureInfo | None = None
    degraded: DegradedInfo | None = None
    idempotency_scope: str = "default"
    idempotency_key: str = ""
    record_version: int = 0
    claim_owner: str | None = None
    claim_token: str | None = None
    claim_expires_at: str | None = None
    workspace: WorkspaceBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, TaskStatus):
            raise KernelContractError("status must be TaskStatus")
        object.__setattr__(self, "task_id", _name(self.task_id, "task_id"))
        object.__setattr__(self, "objective", _text(self.objective, "objective", 4_000))
        object.__setattr__(self, "normalized_input", normalise_json_value(self.normalized_input, field_name="normalized_input"))
        if self.workspace is not None and not isinstance(self.workspace, WorkspaceBinding):
            raise KernelContractError("workspace must be WorkspaceBinding")
        object.__setattr__(self, "idempotency_scope", _name(self.idempotency_scope, "idempotency_scope"))
        object.__setattr__(self, "idempotency_key", _name(self.idempotency_key, "idempotency_key"))
        for name in ("parent_task_id", "owner_agent_id", "current_step", "run_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _name(value, name))
        object.__setattr__(self, "required_capabilities", tuple(_name(x, "capability") for x in self.required_capabilities))
        object.__setattr__(self, "artifact_ids", tuple(_name(x, "artifact_id") for x in self.artifact_ids))
        object.__setattr__(self, "approval_ids", tuple(_name(x, "approval_id") for x in self.approval_ids))
        if self.parent_task_id == self.task_id:
            raise KernelContractError("task cannot be its own parent")
        if not self.budget_used.within(self.budget):
            raise KernelContractError("budget usage exceeds task budget")
        if isinstance(self.record_version, bool) or not isinstance(self.record_version, int) or self.record_version < 0:
            raise KernelContractError("record_version must be non-negative")
        for name in ("created_at", "updated_at", "started_at", "finished_at", "claim_expires_at"):
            object.__setattr__(self, name, normalise_utc_timestamp(getattr(self, name), field_name=name, allow_none=name not in {"created_at", "updated_at"}))
        created, updated = datetime.fromisoformat(self.created_at), datetime.fromisoformat(self.updated_at)
        if updated < created:
            raise KernelContractError("updated_at cannot be before created_at")
        if self.finished_at and datetime.fromisoformat(self.finished_at) < datetime.fromisoformat(self.started_at or self.created_at):
            raise KernelContractError("finished_at cannot be before task start")
        failure_states = {TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.CANCELLED, TaskStatus.ABANDONED}
        if self.status in failure_states and self.failure is None:
            raise KernelContractError("failure terminal state requires failure information")
        if self.status is TaskStatus.DEGRADED and self.degraded is None:
            raise KernelContractError("degraded task requires degraded information")
        if self.status in {TaskStatus.SUCCEEDED, TaskStatus.DEGRADED, *failure_states} and self.finished_at is None:
            raise KernelContractError("terminal task requires finished_at")
        if self.claim_owner is not None:
            object.__setattr__(self, "claim_owner", _name(self.claim_owner, "claim_owner"))
            if not self.claim_token or not self.claim_expires_at:
                raise KernelContractError("claim owner requires token and expiry")
            if datetime.fromisoformat(self.claim_expires_at) <= updated:
                raise KernelContractError("claim expiry must be after updated_at")
        elif self.claim_token is not None or self.claim_expires_at is not None:
            raise KernelContractError("claim token/expiry require owner")

    def to_dict(self, *, include_claim_token: bool = False) -> dict[str, Any]:
        data = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"budget", "budget_used", "failure", "degraded", "claim_token", "workspace"}
        }
        data["workspace"] = self.workspace.to_dict() if self.workspace else None
        data["status"] = self.status.value
        data["budget"] = {name: getattr(self.budget, name) for name in self.budget.__dataclass_fields__}
        data["budget_used"] = {name: getattr(self.budget_used, name) for name in self.budget_used.__dataclass_fields__}
        data["failure"] = None if self.failure is None else {
            "category": self.failure.category.value, "message": self.failure.message,
            "retryable": self.failure.retryable, "code": self.failure.code,
            "details": dict(self.failure.details),
        }
        data["degraded"] = None if self.degraded is None else {
            "summary": self.degraded.summary,
            "missing_capabilities": list(self.degraded.missing_capabilities),
            "details": dict(self.degraded.details),
        }
        data["claim_token"] = self.claim_token if include_claim_token else None
        data["claim_token_fingerprint"] = hashlib.sha256(self.claim_token.encode()).hexdigest()[:16] if self.claim_token else None
        return data


@dataclass(frozen=True, slots=True)
class AgentSpec:
    agent_id: str
    role: AgentRole
    capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    model_policy: Mapping[str, Any]
    permission_scope: PermissionScope
    max_concurrency: int = 1
    budget_limits: BudgetLimit = field(default_factory=BudgetLimit)
    input_contract: Mapping[str, Any] = field(default_factory=dict)
    output_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.role, AgentRole):
            raise KernelContractError("role must be AgentRole")
        object.__setattr__(self, "agent_id", _name(self.agent_id, "agent_id"))
        object.__setattr__(self, "capabilities", tuple(_name(x, "capability") for x in self.capabilities))
        object.__setattr__(self, "allowed_tools", tuple(_name(x, "tool") for x in self.allowed_tools))
        for name in ("model_policy", "input_contract", "output_contract"):
            object.__setattr__(self, name, normalise_json_value(getattr(self, name), field_name=name))
        if not 1 <= self.max_concurrency <= 64:
            raise KernelContractError("max_concurrency must be between 1 and 64")


@dataclass(frozen=True, slots=True)
class Handoff:
    handoff_id: str
    task_id: str
    source_agent_id: str
    target_agent_id: str
    reason: str
    bounded_context: Mapping[str, Any]
    expected_artifact: str
    acceptance_criteria: tuple[str, ...]
    transferred_budget: BudgetLimit
    status: HandoffStatus = HandoffStatus.REQUESTED
    idempotency_key: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.status, HandoffStatus):
            raise KernelContractError("handoff status must be HandoffStatus")
        for name in ("handoff_id", "task_id", "source_agent_id", "target_agent_id", "expected_artifact", "idempotency_key"):
            object.__setattr__(self, name, _name(getattr(self, name), name))
        object.__setattr__(self, "reason", _text(self.reason, "handoff reason", 4_000))
        object.__setattr__(self, "bounded_context", normalise_json_value(self.bounded_context, field_name="handoff context"))
        criteria = tuple(_text(x, "acceptance criterion", 2_000) for x in self.acceptance_criteria)
        if not criteria:
            raise KernelContractError("handoff requires acceptance criteria")
        object.__setattr__(self, "acceptance_criteria", criteria)
        object.__setattr__(self, "created_at", normalise_utc_timestamp(self.created_at, field_name="created_at", allow_none=False))


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    task_id: str
    producer_agent_id: str
    artifact_type: str
    reference: str
    digest: str
    provenance: Mapping[str, Any]
    validation_status: ArtifactStatus = ArtifactStatus.DECLARED
    sensitivity: Sensitivity = Sensitivity.PROJECT
    retention: str = "task_lifetime"
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.validation_status, ArtifactStatus) or not isinstance(self.sensitivity, Sensitivity):
            raise KernelContractError("artifact enums are invalid")
        for name in ("artifact_id", "task_id", "producer_agent_id", "artifact_type", "retention"):
            object.__setattr__(self, name, _name(getattr(self, name), name))
        reference = self.reference.replace("\\", "/").strip()
        if not reference or reference.startswith("/") or ".." in reference.split("/") or re.match(r"^[A-Za-z]:", reference):
            raise KernelContractError("artifact reference must be relative and non-escaping")
        object.__setattr__(self, "reference", reference)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest):
            raise KernelContractError("artifact digest must be sha256")
        if self.sensitivity is Sensitivity.SECRET_PROHIBITED:
            raise SensitiveValueError("secret-prohibited artifact cannot be registered")
        object.__setattr__(self, "provenance", normalise_json_value(self.provenance, field_name="provenance"))
        object.__setattr__(self, "created_at", normalise_utc_timestamp(self.created_at, field_name="created_at", allow_none=False))

    @staticmethod
    def digest_bytes(content: bytes) -> str:
        return "sha256:" + hashlib.sha256(content).hexdigest()

    @staticmethod
    def digest_file(path: str | Path) -> str:
        hasher = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return "sha256:" + hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class Approval:
    approval_id: str
    task_id: str
    requested_action: Mapping[str, Any]
    reason: str
    scope: Mapping[str, Any]
    requester_agent_id: str
    decision: ApprovalStatus = ApprovalStatus.PENDING
    decision_maker: str | None = None
    requested_at: str = field(default_factory=utc_now_iso)
    expires_at: str | None = None
    audit_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ApprovalStatus):
            raise KernelContractError("decision must be ApprovalStatus")
        for name in ("approval_id", "task_id", "requester_agent_id"):
            object.__setattr__(self, name, _name(getattr(self, name), name))
        object.__setattr__(self, "requested_action", normalise_json_value(self.requested_action, field_name="requested_action"))
        object.__setattr__(self, "scope", normalise_json_value(self.scope, field_name="scope"))
        object.__setattr__(self, "reason", _text(self.reason, "approval reason", 4_000))
        object.__setattr__(self, "requested_at", normalise_utc_timestamp(self.requested_at, field_name="requested_at", allow_none=False))
        object.__setattr__(self, "expires_at", normalise_utc_timestamp(self.expires_at, field_name="expires_at"))
        if self.decision is not ApprovalStatus.PENDING and self.decision_maker is None:
            raise KernelContractError("decided approval requires decision_maker")
        if self.decision_maker is not None:
            object.__setattr__(self, "decision_maker", _name(self.decision_maker, "decision_maker"))
        if self.audit_reference is not None:
            object.__setattr__(self, "audit_reference", _name(self.audit_reference, "audit_reference"))
        if self.expires_at and datetime.fromisoformat(self.expires_at) <= datetime.fromisoformat(self.requested_at):
            raise KernelContractError("expires_at must be after requested_at")
