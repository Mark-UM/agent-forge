"""Capability manifest discovery with lossless error reporting.

``discover_modules`` preserves the legacy list-returning API.  The canonical
``discover_modules_report`` API also returns every parse/validation/missing
manifest issue so validation can never pass merely because a bad manifest was
silently skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Optional

from modules.registry.schema import (
    CapabilityManifest,
    ManifestValidationError,
    validate_manifest,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MODULES_DIR = _PROJECT_ROOT / "modules"
_CANONICAL_FIELDS = frozenset(
    {
        "name",
        "version",
        "description",
        "entrypoints",
        "capabilities",
        "dependencies",
        "credentials",
        "health_checks",
        "storage",
        "experimental",
    }
)
_SKIP_DIRECTORIES = frozenset({"tests", "__pycache__"})


@dataclass(frozen=True)
class DiscoveryIssue:
    module: str
    code: str
    message: str
    path: str
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


@dataclass
class DiscoveryReport:
    manifests: list[CapabilityManifest] = field(default_factory=list)
    errors: list[DiscoveryIssue] = field(default_factory=list)
    warnings: list[DiscoveryIssue] = field(default_factory=list)
    scanned_modules: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "scanned_modules": list(self.scanned_modules),
            "valid_manifest_count": len(self.manifests),
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def _is_module_directory(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith((".", "_")):
        return False
    return path.name not in _SKIP_DIRECTORIES


def _has_first_party_code(path: Path) -> bool:
    return any(
        candidate.is_file()
        for candidate in path.rglob("*.py")
        if "__pycache__" not in candidate.parts
    )


def _entrypoint_issue(command: str, project_root: Path) -> str | None:
    """Return an error when a first-party Python entrypoint does not exist."""

    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return "entrypoint command cannot be parsed"
    if len(parts) >= 3 and parts[0].lower().startswith("python") and parts[1] == "-m":
        module_name = parts[2]
        if not module_name.startswith("modules."):
            return None
        candidate = project_root.joinpath(*module_name.split("."))
        if candidate.with_suffix(".py").is_file() or (candidate / "__init__.py").is_file():
            return None
        return f"Python module does not exist: {module_name}"
    if len(parts) >= 2 and parts[0].lower().startswith("python"):
        script = parts[1].replace("\\", "/")
        if script.startswith("modules/") and not (project_root / script).is_file():
            return f"Python script does not exist: {script}"
    return None


def _strict_issues(
    *,
    raw: dict,
    manifest: CapabilityManifest,
    module_dir: Path,
    project_root: Path,
) -> list[DiscoveryIssue]:
    issues: list[DiscoveryIssue] = []
    path = str(module_dir / "manifest.json")
    missing = sorted(_CANONICAL_FIELDS - set(raw))
    if missing:
        issues.append(
            DiscoveryIssue(
                module=module_dir.name,
                code="missing_canonical_fields",
                message=f"missing canonical fields: {', '.join(missing)}",
                path=path,
            )
        )
    legacy = sorted(set(raw) & {"module", "entry_point", "components"})
    if legacy:
        issues.append(
            DiscoveryIssue(
                module=module_dir.name,
                code="legacy_manifest_fields",
                message=f"legacy fields are not allowed in strict mode: {', '.join(legacy)}",
                path=path,
            )
        )
    if manifest.name != module_dir.name:
        issues.append(
            DiscoveryIssue(
                module=module_dir.name,
                code="module_name_mismatch",
                message=f"manifest name {manifest.name!r} does not match directory {module_dir.name!r}",
                path=path,
            )
        )

    for kind, entrypoint in manifest.entrypoints.items():
        problem = _entrypoint_issue(entrypoint.command, project_root)
        if problem:
            issues.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="invalid_entrypoint",
                    message=f"{kind}: {problem}",
                    path=path,
                )
            )

    for storage in manifest.storage:
        if not storage.path:
            if storage.type != "none":
                issues.append(
                    DiscoveryIssue(
                        module=module_dir.name,
                        code="empty_storage_path",
                        message=f"storage type {storage.type!r} requires a path",
                        path=path,
                    )
                )
            continue
        storage_path = Path(storage.path)
        if storage_path.is_absolute():
            issues.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="absolute_storage_path",
                    message=f"storage path must be project-relative: {storage.path}",
                    path=path,
                )
            )
            continue
        resolved = (project_root / storage_path).resolve()
        try:
            resolved.relative_to(project_root.resolve())
        except ValueError:
            issues.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="storage_path_escape",
                    message=f"storage path escapes the project root: {storage.path}",
                    path=path,
                )
            )

    seen: set[str] = set()
    for capability in manifest.capabilities:
        if not capability.name or not re.match(r"^[a-z][a-z0-9_.-]*$", capability.name):
            issues.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="invalid_capability_name",
                    message=f"invalid capability name: {capability.name!r}",
                    path=path,
                )
            )
        if capability.name in seen:
            issues.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="duplicate_capability",
                    message=f"capability is declared more than once: {capability.name}",
                    path=path,
                )
            )
        seen.add(capability.name)
    return issues


def discover_modules_report(
    modules_dir: Optional[Path] = None,
    *,
    strict: bool = False,
) -> DiscoveryReport:
    scan_dir = (modules_dir or _MODULES_DIR).resolve()
    project_root = scan_dir.parent.resolve()
    report = DiscoveryReport()
    if not scan_dir.exists():
        report.errors.append(
            DiscoveryIssue(
                module="<registry>",
                code="modules_directory_missing",
                message=f"modules directory does not exist: {scan_dir}",
                path=str(scan_dir),
            )
        )
        return report

    capability_owners: dict[str, str] = {}
    for module_dir in sorted(scan_dir.iterdir()):
        if not _is_module_directory(module_dir):
            continue
        if not _has_first_party_code(module_dir):
            continue
        report.scanned_modules.append(module_dir.name)
        manifest_path = module_dir / "manifest.json"
        if not manifest_path.is_file():
            report.errors.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="manifest_missing",
                    message="module contains Python code but has no manifest.json",
                    path=str(manifest_path),
                )
            )
            continue

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.errors.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="manifest_json_invalid",
                    message=str(exc),
                    path=str(manifest_path),
                )
            )
            continue
        except OSError as exc:
            report.errors.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="manifest_read_failed",
                    message=str(exc),
                    path=str(manifest_path),
                )
            )
            continue

        try:
            manifest = validate_manifest(raw)
        except ManifestValidationError as exc:
            report.errors.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="manifest_schema_invalid",
                    message=str(exc),
                    path=str(manifest_path),
                )
            )
            continue
        except Exception as exc:
            report.errors.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="manifest_validation_failed",
                    message=f"{type(exc).__name__}: {exc}",
                    path=str(manifest_path),
                )
            )
            continue

        if manifest.name != module_dir.name:
            target = report.errors if strict else report.warnings
            target.append(
                DiscoveryIssue(
                    module=module_dir.name,
                    code="module_name_mismatch",
                    message=f"manifest name {manifest.name!r} does not match directory {module_dir.name!r}",
                    path=str(manifest_path),
                    severity="error" if strict else "warning",
                )
            )
        if strict:
            report.errors.extend(
                _strict_issues(
                    raw=raw,
                    manifest=manifest,
                    module_dir=module_dir,
                    project_root=project_root,
                )
            )

        for capability in manifest.capabilities:
            owner = capability_owners.get(capability.name)
            if capability.name and owner and owner != manifest.name:
                report.errors.append(
                    DiscoveryIssue(
                        module=module_dir.name,
                        code="capability_owner_conflict",
                        message=f"capability {capability.name!r} is already owned by {owner!r}",
                        path=str(manifest_path),
                    )
                )
            elif capability.name:
                capability_owners[capability.name] = manifest.name
        report.manifests.append(manifest)

    report.manifests.sort(key=lambda manifest: manifest.name)
    return report


def discover_modules(modules_dir: Optional[Path] = None) -> list[CapabilityManifest]:
    report = discover_modules_report(modules_dir)
    for issue in [*report.errors, *report.warnings]:
        print(
            f"[registry] {issue.severity.upper()}: {issue.module}: {issue.message}",
            file=sys.stderr,
        )
    return report.manifests


def discover_module_names(modules_dir: Optional[Path] = None) -> list[str]:
    return [manifest.name for manifest in discover_modules(modules_dir)]


def find_missing_manifests(modules_dir: Optional[Path] = None) -> list[str]:
    report = discover_modules_report(modules_dir)
    return [
        issue.module for issue in report.errors if issue.code == "manifest_missing"
    ]


__all__ = [
    "DiscoveryIssue",
    "DiscoveryReport",
    "discover_module_names",
    "discover_modules",
    "discover_modules_report",
    "find_missing_manifests",
]
