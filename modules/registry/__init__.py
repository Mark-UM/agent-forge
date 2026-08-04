"""Capability Registry — unified module discovery and health checking.

Public API:
    CapabilityRegistry     — in-memory registry of module manifests
    CapabilityManifest     — normalized manifest dataclass
    discover_modules()     — scan modules/*/manifest.json
    get_registry()         — module-level singleton

Usage:
    from modules.registry import get_registry, discover_modules

    registry = get_registry()
    for mod in registry.all():
        print(f"{mod.name}: {mod.version}")
"""
from modules.registry.schema import (
    CapabilityManifest,
    Capability,
    EntryPoint,
    Dependency,
    CredentialRef,
    HealthCheck,
    StorageRef,
    validate_manifest,
    ManifestValidationError,
)
from modules.registry.registry import CapabilityRegistry, get_registry
from modules.registry.discovery import discover_modules
from modules.registry.health import HealthStatus, run_health_checks

__all__ = [
    "CapabilityRegistry",
    "CapabilityManifest",
    "Capability",
    "EntryPoint",
    "Dependency",
    "CredentialRef",
    "HealthCheck",
    "StorageRef",
    "validate_manifest",
    "ManifestValidationError",
    "get_registry",
    "discover_modules",
    "HealthStatus",
    "run_health_checks",
]
