"""R2-B.1: Manifest schema — normalized contract for all module manifests.

Every module under modules/ must have a manifest.json matching this schema.
The schema is declarative (metadata only, no logic) and validated at load time.

Schema fields:
    name            — module name, must match directory name
    version         — semantic version string
    description     — human-readable summary
    entrypoints     — dict of {kind: command/python-path}
    capabilities    — list of capability declarations
    dependencies    — required and optional packages
    credentials     — environment variables needed (names only, never values)
    health_checks   — shell commands to verify module health
    storage         — storage backends used by this module
    experimental    — whether this module is experimental
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Semantic version regex (simplified) ──────────────────────
_SEMVER_RE = re.compile(
    r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)'
    r'(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))'
    r'(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?'
    r'(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$'
)

# Valid entrypoint kinds
VALID_ENTRYPOINT_KINDS = frozenset({'mcp', 'cli', 'python', 'daemon'})


class ManifestValidationError(ValueError):
    """Raised when a manifest does not conform to the schema."""


# ── Dataclasses ──────────────────────────────────────────────

@dataclass(frozen=True)
class Capability:
    """A single capability provided by a module."""
    name: str           # e.g. "search.pipeline"
    version: str = "1.0"  # capability version, not module version
    description: str = ""


@dataclass(frozen=True)
class EntryPoint:
    """An entry point for invoking a module."""
    kind: str           # "mcp" | "cli" | "python" | "daemon"
    command: str        # shell command or python import path

    def __post_init__(self):
        if self.kind not in VALID_ENTRYPOINT_KINDS:
            raise ManifestValidationError(
                f"Invalid entrypoint kind: {self.kind}. "
                f"Valid: {sorted(VALID_ENTRYPOINT_KINDS)}"
            )


@dataclass(frozen=True)
class Dependency:
    """A package dependency declaration."""
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CredentialRef:
    """A reference to a required credential (env var name only)."""
    env: str            # environment variable name, e.g. "SERPER_API_KEY"
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class HealthCheck:
    """A health check command."""
    command: str        # shell command to execute
    description: str = ""
    timeout: int = 30   # seconds


@dataclass(frozen=True)
class StorageRef:
    """A storage backend reference."""
    type: str           # "sqlite" | "json" | "chromadb" | "markdown" | "none"
    path: str = ""      # relative path (from project root)
    tables: list[str] = field(default_factory=list)
    description: str = ""


@dataclass(frozen=True)
class CapabilityManifest:
    """Normalized manifest for a single module.

    This is the canonical in-memory representation. All manifest.json files
    are loaded into this dataclass after validation.
    """
    name: str
    version: str
    description: str
    entrypoints: dict[str, EntryPoint] = field(default_factory=dict)
    capabilities: list[Capability] = field(default_factory=list)
    dependencies: Dependency = field(default_factory=Dependency)
    credentials: list[CredentialRef] = field(default_factory=list)
    health_checks: list[HealthCheck] = field(default_factory=list)
    storage: list[StorageRef] = field(default_factory=list)
    experimental: bool = False

    def __post_init__(self):
        # Validate name (must be a valid module name)
        if not self.name or not re.match(r'^[a-z][a-z0-9_]*$', self.name):
            raise ManifestValidationError(
                f"Invalid module name: {self.name!r}. "
                f"Must be lowercase, start with a letter, contain only [a-z0-9_]."
            )
        # Validate version
        if not _SEMVER_RE.match(self.version):
            raise ManifestValidationError(
                f"Invalid version: {self.version!r}. Must be semantic version."
            )


# ── Validation ───────────────────────────────────────────────

def validate_manifest(raw: dict[str, Any]) -> CapabilityManifest:
    """Validate a raw dict manifest and return a normalized CapabilityManifest.

    Args:
        raw: Parsed JSON dict from manifest.json.

    Returns:
        CapabilityManifest: Normalized manifest.

    Raises:
        ManifestValidationError: If the manifest is invalid.
    """
    if not isinstance(raw, dict):
        raise ManifestValidationError(f"Manifest must be a dict, got {type(raw)}")

    # Required fields
    name = raw.get('name') or raw.get('module')
    if not name:
        raise ManifestValidationError("Manifest missing required field: name (or module)")

    version = raw.get('version')
    if not version:
        raise ManifestValidationError(f"Module {name}: missing required field: version")

    description = raw.get('description', '')

    # Parse entrypoints
    entrypoints: dict[str, EntryPoint] = {}
    raw_eps = raw.get('entrypoints', {})

    # Legacy: single entry_point string
    if not raw_eps and 'entry_point' in raw:
        raw_eps = {'daemon': raw['entry_point']}

    if isinstance(raw_eps, dict):
        for kind, cmd in raw_eps.items():
            if kind not in VALID_ENTRYPOINT_KINDS:
                raise ManifestValidationError(
                    f"Module {name}: invalid entrypoint kind {kind!r}. "
                    f"Valid: {sorted(VALID_ENTRYPOINT_KINDS)}"
                )
            if not isinstance(cmd, str) or not cmd:
                raise ManifestValidationError(
                    f"Module {name}: entrypoint {kind} must be a non-empty string"
                )
            entrypoints[kind] = EntryPoint(kind=kind, command=cmd)
    elif isinstance(raw_eps, str):
        entrypoints['cli'] = EntryPoint(kind='cli', command=raw_eps)

    # Parse capabilities
    capabilities: list[Capability] = []
    for cap in raw.get('capabilities', []):
        if isinstance(cap, str):
            capabilities.append(Capability(name=cap))
        elif isinstance(cap, dict):
            capabilities.append(Capability(
                name=cap.get('name', ''),
                version=cap.get('version', '1.0'),
                description=cap.get('description', ''),
            ))
        else:
            raise ManifestValidationError(
                f"Module {name}: capability must be string or dict, got {type(cap)}"
            )

    # Parse dependencies
    raw_deps = raw.get('dependencies', {})
    if isinstance(raw_deps, list):
        # Legacy: flat list → all required
        deps = Dependency(required=raw_deps)
    elif isinstance(raw_deps, dict):
        deps = Dependency(
            required=raw_deps.get('required', []),
            optional=raw_deps.get('optional', []),
        )
    else:
        deps = Dependency()

    # Parse credentials
    credentials: list[CredentialRef] = []
    for cred in raw.get('credentials', []):
        if isinstance(cred, str):
            credentials.append(CredentialRef(env=cred))
        elif isinstance(cred, dict):
            credentials.append(CredentialRef(
                env=cred.get('env', ''),
                required=cred.get('required', True),
                description=cred.get('description', ''),
            ))

    # Parse health_checks
    health_checks: list[HealthCheck] = []
    for hc in raw.get('health_checks', []):
        if isinstance(hc, str):
            health_checks.append(HealthCheck(command=hc))
        elif isinstance(hc, dict):
            health_checks.append(HealthCheck(
                command=hc.get('command', ''),
                description=hc.get('description', ''),
                timeout=hc.get('timeout', 30),
            ))

    # Parse storage
    storage: list[StorageRef] = []
    raw_storage = raw.get('storage', [])
    if isinstance(raw_storage, dict):
        raw_storage = [raw_storage]
    for s in raw_storage:
        if isinstance(s, dict):
            storage.append(StorageRef(
                type=s.get('type', 'none'),
                path=s.get('path', ''),
                tables=s.get('tables', []),
                description=s.get('description', ''),
            ))

    experimental = bool(raw.get('experimental', False))

    return CapabilityManifest(
        name=name,
        version=version,
        description=description,
        entrypoints=entrypoints,
        capabilities=capabilities,
        dependencies=deps,
        credentials=credentials,
        health_checks=health_checks,
        storage=storage,
        experimental=experimental,
    )


def manifest_to_dict(manifest: CapabilityManifest) -> dict[str, Any]:
    """Serialize a CapabilityManifest back to a dict for JSON output."""
    return {
        'name': manifest.name,
        'version': manifest.version,
        'description': manifest.description,
        'entrypoints': {
            kind: ep.command for kind, ep in manifest.entrypoints.items()
        },
        'capabilities': [
            {'name': c.name, 'version': c.version, 'description': c.description}
            for c in manifest.capabilities
        ],
        'dependencies': {
            'required': list(manifest.dependencies.required),
            'optional': list(manifest.dependencies.optional),
        },
        'credentials': [
            {'env': c.env, 'required': c.required, 'description': c.description}
            for c in manifest.credentials
        ],
        'health_checks': [
            {'command': hc.command, 'description': hc.description, 'timeout': hc.timeout}
            for hc in manifest.health_checks
        ],
        'storage': [
            {'type': s.type, 'path': s.path, 'tables': list(s.tables), 'description': s.description}
            for s in manifest.storage
        ],
        'experimental': manifest.experimental,
    }
