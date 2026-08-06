"""Pure AgentSpec permission checks for Kernel Slice 2 execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .contracts import AgentSpec, KernelContractError
from .execution_support import PermissionDeniedError, _identifier


def _project_relative_path(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise KernelContractError(f"{field_name} must be a string")
    normalized = value.replace("\\", "/").strip()
    if any(ord(character) < 32 for character in normalized):
        raise KernelContractError(f"{field_name} contains control characters")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or (len(normalized) >= 2 and normalized[1] == ":")
    ):
        raise KernelContractError(f"{field_name} must be project-relative and non-escaping")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Typed permission request evaluated before any runtime side effect."""

    workspace_path: str | None = None
    network_read: bool = False
    network_write: bool = False
    subprocess: bool = False
    private_memory: bool = False
    git_write: bool = False

    def __post_init__(self) -> None:
        for name in (
            "network_read",
            "network_write",
            "subprocess",
            "private_memory",
            "git_write",
        ):
            if type(getattr(self, name)) is not bool:
                raise KernelContractError(f"permission request {name} must be boolean")
        if self.workspace_path is not None:
            object.__setattr__(
                self,
                "workspace_path",
                _project_relative_path(self.workspace_path, "workspace_path"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_path": self.workspace_path,
            "network_read": self.network_read,
            "network_write": self.network_write,
            "subprocess": self.subprocess,
            "private_memory": self.private_memory,
            "git_write": self.git_write,
        }


class PermissionAuthorizer:
    """Pure AgentSpec authorization; it performs no side effect."""

    @staticmethod
    def authorize(
        spec: AgentSpec,
        *,
        required_capabilities: tuple[str, ...],
        tool_name: str | None,
        request: PermissionRequest,
    ) -> None:
        if not isinstance(request, PermissionRequest):
            raise KernelContractError("permission request must be PermissionRequest")
        missing = sorted(set(required_capabilities) - set(spec.capabilities))
        if missing:
            raise PermissionDeniedError(
                f"agent {spec.agent_id} lacks capabilities: {', '.join(missing)}"
            )

        if tool_name is not None:
            tool = _identifier(tool_name, "tool_name")
            if tool not in spec.allowed_tools or tool not in spec.permission_scope.allowed_tools:
                raise PermissionDeniedError(
                    f"agent {spec.agent_id} is not allowed to invoke tool {tool}"
                )

        scope = spec.permission_scope
        if request.workspace_path is not None:
            requested = PurePosixPath(request.workspace_path)
            roots = tuple(
                PurePosixPath(_project_relative_path(root, "workspace root"))
                for root in scope.workspace_roots
            )
            allowed = any(
                root == PurePosixPath(".")
                or requested == root
                or root in requested.parents
                for root in roots
            )
            if not allowed:
                raise PermissionDeniedError(
                    f"agent {spec.agent_id} cannot access requested workspace path"
                )
        for name in (
            "network_read",
            "network_write",
            "subprocess",
            "private_memory",
            "git_write",
        ):
            if type(getattr(scope, name)) is not bool:
                raise KernelContractError(f"permission scope {name} must be boolean")
            if getattr(request, name) and not getattr(scope, name):
                raise PermissionDeniedError(
                    f"agent {spec.agent_id} lacks permission {name}"
                )
