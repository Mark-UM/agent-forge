"""Pure AgentSpec permission checks for Kernel Slice 2 execution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .contracts import AgentSpec, KernelContractError
from .execution_support import PermissionDeniedError, _identifier

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
        if self.workspace_path is None:
            return
        value = str(self.workspace_path).replace("\\", "/").strip()
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or (len(value) >= 2 and value[1] == ":")
        ):
            raise KernelContractError("workspace_path must be project-relative and non-escaping")
        object.__setattr__(self, "workspace_path", value)

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
            allowed = any(
                request.workspace_path == root
                or request.workspace_path.startswith(root.rstrip("/") + "/")
                for root in scope.workspace_roots
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
            if getattr(request, name) and not getattr(scope, name):
                raise PermissionDeniedError(
                    f"agent {spec.agent_id} lacks permission {name}"
                )


