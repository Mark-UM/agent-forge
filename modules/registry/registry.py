"""R2-B.1: CapabilityRegistry — in-memory registry of module manifests.

Generalized from modules/search/providers.py ProviderRegistry pattern.
Provides lookup by name, capability, and entrypoint kind.
"""
from __future__ import annotations

from typing import Optional

from modules.registry.schema import CapabilityManifest


class CapabilityRegistry:
    """Registry of available module manifests.

    Modules are registered explicitly; discovery is handled by
    modules.registry.discovery.discover_modules(). The registry does not
    auto-discover — this keeps the dependency graph explicit and testable.
    """

    def __init__(self) -> None:
        self._modules: dict[str, CapabilityManifest] = {}

    def register(self, manifest: CapabilityManifest) -> "CapabilityRegistry":
        """Register a module manifest. Chainable.

        Raises:
            ValueError: If a module with the same name is already registered.
        """
        if manifest.name in self._modules:
            raise ValueError(
                f"Module {manifest.name!r} is already registered. "
                f"Use unregister() first if intentional."
            )
        self._modules[manifest.name] = manifest
        return self

    def unregister(self, name: str) -> Optional[CapabilityManifest]:
        """Remove a module from the registry. Returns the removed manifest or None."""
        return self._modules.pop(name, None)

    def get(self, name: str) -> Optional[CapabilityManifest]:
        """Look up a module by name. Returns None if not found."""
        return self._modules.get(name)

    def all(self) -> list[CapabilityManifest]:
        """Return all registered modules, sorted by name."""
        return sorted(self._modules.values(), key=lambda m: m.name)

    def names(self) -> list[str]:
        """Return all registered module names, sorted."""
        return sorted(self._modules.keys())

    def by_capability(self, capability_name: str) -> list[CapabilityManifest]:
        """Return all modules that provide a given capability."""
        return [
            m for m in self._modules.values()
            if any(c.name == capability_name for c in m.capabilities)
        ]

    def by_entrypoint(self, kind: str) -> list[CapabilityManifest]:
        """Return all modules that have an entrypoint of the given kind."""
        return [m for m in self._modules.values() if kind in m.entrypoints]

    def experimental(self) -> list[CapabilityManifest]:
        """Return all experimental modules."""
        return [m for m in self._modules.values() if m.experimental]

    def stable(self) -> list[CapabilityManifest]:
        """Return all non-experimental modules."""
        return [m for m in self._modules.values() if not m.experimental]

    def __len__(self) -> int:
        return len(self._modules)

    def __contains__(self, name: str) -> bool:
        return name in self._modules

    def __repr__(self) -> str:
        return f"CapabilityRegistry(modules={len(self._modules)})"


# ── Module-level singleton ───────────────────────────────────

_registry_singleton: Optional[CapabilityRegistry] = None


def get_registry() -> CapabilityRegistry:
    """Return the module-level singleton registry.

    On first call, discovers all modules under modules/ and registers them.
    Subsequent calls return the cached registry.
    """
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = CapabilityRegistry()
        # Auto-discover on first access
        from modules.registry.discovery import discover_modules
        for manifest in discover_modules():
            try:
                _registry_singleton.register(manifest)
            except ValueError:
                # Skip duplicates silently on auto-discovery
                pass
    return _registry_singleton


def reset_registry() -> None:
    """Reset the singleton. Primarily for testing."""
    global _registry_singleton
    _registry_singleton = None
