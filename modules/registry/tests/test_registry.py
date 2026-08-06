"""R2-B.7: Contract tests for the Capability Registry.

Verifies:
- All modules under modules/ have a valid manifest.json
- All manifests conform to the schema
- Discovery finds all expected modules
- Registry operations (register, get, by_capability) work correctly
- Health check executor returns structured results
- Legacy manifest schemas are normalized correctly
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from modules.registry.schema import (
    CapabilityManifest,
    Capability,
    EntryPoint,
    Dependency,
    CredentialRef,
    HealthCheck,
    StorageRef,
    validate_manifest,
    manifest_to_dict,
    ManifestValidationError,
    VALID_ENTRYPOINT_KINDS,
)
from modules.registry.registry import CapabilityRegistry, get_registry, reset_registry
from modules.registry.discovery import discover_modules, find_missing_manifests
from modules.registry.health import (
    HealthStatus,
    HealthCheckResult,
    check_module_health,
    run_health_checks,
    check_credentials,
)


# ── Schema validation tests ─────────────────────────────────

class TestSchemaValidation:
    """Tests for manifest schema validation."""

    def test_valid_minimal_manifest(self):
        """A manifest with only required fields should validate."""
        raw = {"name": "testmod", "version": "1.0.0", "description": "Test"}
        manifest = validate_manifest(raw)
        assert manifest.name == "testmod"
        assert manifest.version == "1.0.0"
        assert manifest.experimental is False
        assert len(manifest.entrypoints) == 0
        assert len(manifest.capabilities) == 0

    def test_valid_full_manifest(self):
        """A manifest with all fields should validate."""
        raw = {
            "name": "testmod",
            "version": "2.1.3",
            "description": "Full test module",
            "entrypoints": {
                "mcp": "python -m modules.testmod serve",
                "cli": "python -m modules.testmod",
            },
            "capabilities": [
                {"name": "test.run", "version": "1.0", "description": "Run tests"},
                "test.check",
            ],
            "dependencies": {
                "required": ["requests"],
                "optional": ["chromadb"],
            },
            "credentials": [
                {"env": "API_KEY", "required": True, "description": "API key"},
                "OPTIONAL_KEY",
            ],
            "health_checks": [
                {"command": "python -c 'import testmod'", "description": "Import check"},
                "echo ok",
            ],
            "storage": [
                {"type": "sqlite", "path": "_runtime/test.db", "tables": ["runs"]},
            ],
            "experimental": True,
        }
        manifest = validate_manifest(raw)
        assert manifest.name == "testmod"
        assert manifest.version == "2.1.3"
        assert len(manifest.entrypoints) == 2
        assert manifest.entrypoints["mcp"].command == "python -m modules.testmod serve"
        assert len(manifest.capabilities) == 2
        assert manifest.capabilities[0].name == "test.run"
        assert manifest.capabilities[1].name == "test.check"
        assert manifest.dependencies.required == ["requests"]
        assert manifest.dependencies.optional == ["chromadb"]
        assert len(manifest.credentials) == 2
        assert manifest.credentials[0].env == "API_KEY"
        assert manifest.credentials[1].env == "OPTIONAL_KEY"
        assert len(manifest.health_checks) == 2
        assert len(manifest.storage) == 1
        assert manifest.storage[0].type == "sqlite"
        assert manifest.experimental is True

    def test_legacy_module_field_accepted(self):
        """Legacy manifests using 'module' instead of 'name' should work."""
        raw = {"module": "legacy", "version": "1.0.0"}
        manifest = validate_manifest(raw)
        assert manifest.name == "legacy"

    def test_legacy_entry_point_accepted(self):
        """Legacy manifests using 'entry_point' string should work."""
        raw = {
            "name": "legacy",
            "version": "1.0.0",
            "entry_point": "python -m modules.legacy",
        }
        manifest = validate_manifest(raw)
        assert "daemon" in manifest.entrypoints
        assert manifest.entrypoints["daemon"].command == "python -m modules.legacy"

    def test_legacy_dependencies_list_accepted(self):
        """Legacy flat list dependencies should be treated as all required."""
        raw = {
            "name": "legacy",
            "version": "1.0.0",
            "dependencies": ["lib1", "lib2"],
        }
        manifest = validate_manifest(raw)
        assert manifest.dependencies.required == ["lib1", "lib2"]
        assert manifest.dependencies.optional == []

    def test_invalid_name_rejected(self):
        """Names with invalid characters should be rejected."""
        with pytest.raises(ManifestValidationError, match="Invalid module name"):
            validate_manifest({"name": "Test-Mod", "version": "1.0.0"})

    def test_invalid_version_rejected(self):
        """Non-semver versions should be rejected."""
        with pytest.raises(ManifestValidationError, match="Invalid version"):
            validate_manifest({"name": "test", "version": "latest"})

    def test_missing_name_rejected(self):
        """Manifests without name or module should be rejected."""
        with pytest.raises(ManifestValidationError, match="missing required field: name"):
            validate_manifest({"version": "1.0.0"})

    def test_missing_version_rejected(self):
        """Manifests without version should be rejected."""
        with pytest.raises(ManifestValidationError, match="missing required field: version"):
            validate_manifest({"name": "test"})

    def test_invalid_entrypoint_kind_rejected(self):
        """Invalid entrypoint kinds should be rejected."""
        raw = {
            "name": "test",
            "version": "1.0.0",
            "entrypoints": {"rest": "http://..."},
        }
        with pytest.raises(ManifestValidationError, match="invalid entrypoint kind"):
            validate_manifest(raw)

    def test_manifest_to_dict_roundtrip(self):
        """manifest_to_dict should produce valid output for JSON serialization."""
        manifest = validate_manifest({
            "name": "roundtrip",
            "version": "1.2.3",
            "entrypoints": {"cli": "python -m roundtrip"},
        })
        d = manifest_to_dict(manifest)
        assert d["name"] == "roundtrip"
        assert d["version"] == "1.2.3"
        assert d["entrypoints"]["cli"] == "python -m roundtrip"
        json.dumps(d)


# ── Registry tests ──────────────────────────────────────────

class TestCapabilityRegistry:
    """Tests for CapabilityRegistry class."""

    def _make_manifest(self, name="test", caps=None):
        return CapabilityManifest(
            name=name,
            version="1.0.0",
            description="Test module",
            capabilities=caps or [],
        )

    def test_register_and_get(self):
        reg = CapabilityRegistry()
        m = self._make_manifest("alpha")
        reg.register(m)
        assert reg.get("alpha") is m
        assert "alpha" in reg
        assert len(reg) == 1

    def test_register_duplicate_raises(self):
        reg = CapabilityRegistry()
        reg.register(self._make_manifest("alpha"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register(self._make_manifest("alpha"))

    def test_unregister(self):
        reg = CapabilityRegistry()
        reg.register(self._make_manifest("alpha"))
        removed = reg.unregister("alpha")
        assert removed is not None
        assert removed.name == "alpha"
        assert reg.get("alpha") is None
        assert reg.unregister("nonexistent") is None

    def test_all_sorted_by_name(self):
        reg = CapabilityRegistry()
        reg.register(self._make_manifest("zeta"))
        reg.register(self._make_manifest("alpha"))
        reg.register(self._make_manifest("mid"))
        names = [m.name for m in reg.all()]
        assert names == ["alpha", "mid", "zeta"]

    def test_by_capability(self):
        reg = CapabilityRegistry()
        reg.register(self._make_manifest("search", caps=[Capability(name="search.pipeline")]))
        reg.register(self._make_manifest("scheduler", caps=[Capability(name="scheduler.cron")]))
        reg.register(self._make_manifest("vision", caps=[Capability(name="search.pipeline")]))

        results = reg.by_capability("search.pipeline")
        assert len(results) == 2
        assert {m.name for m in results} == {"search", "vision"}

    def test_by_entrypoint(self):
        reg = CapabilityRegistry()
        m1 = CapabilityManifest(
            name="m1", version="1.0.0", description="",
            entrypoints={"mcp": EntryPoint(kind="mcp", command="python -m m1 serve")},
        )
        m2 = CapabilityManifest(
            name="m2", version="1.0.0", description="",
            entrypoints={"cli": EntryPoint(kind="cli", command="python -m m2")},
        )
        reg.register(m1)
        reg.register(m2)
        mcp_mods = reg.by_entrypoint("mcp")
        assert len(mcp_mods) == 1
        assert mcp_mods[0].name == "m1"

    def test_experimental_stable(self):
        reg = CapabilityRegistry()
        reg.register(CapabilityManifest(name="stable1", version="1.0.0", description=""))
        reg.register(CapabilityManifest(name="exp1", version="1.0.0", description="", experimental=True))
        assert len(reg.stable()) == 1
        assert len(reg.experimental()) == 1
        assert reg.stable()[0].name == "stable1"


# ── Discovery tests ──────────────────────────────────────────

class TestDiscovery:
    """Tests for module discovery."""

    def test_discover_finds_all_modules(self):
        """Discovery should find the complete current module inventory."""
        manifests = discover_modules()
        names = {m.name for m in manifests}
        expected = {
            "bootstrap", "browser", "common", "delivery", "dispatch",
            "integration_check", "kernel", "mcp", "memory", "orchestrator",
            "prompt", "registry", "runtime", "scheduler", "search",
            "ui_check", "vision",
        }
        assert names == expected, f"Missing: {expected - names}, Extra: {names - expected}"

    def test_no_missing_manifests(self):
        """No module directory should lack a manifest.json."""
        missing = find_missing_manifests()
        assert missing == [], f"Modules missing manifests: {missing}"

    def test_all_manifests_have_required_fields(self):
        """Every discovered manifest must have name and version."""
        for m in discover_modules():
            assert m.name, "Manifest missing name"
            assert m.version, f"Manifest {m.name} missing version"
            assert isinstance(m.description, str)

    def test_all_manifests_validated(self):
        """Every manifest must pass schema validation."""
        manifests = discover_modules()
        assert len(manifests) >= 17

    def test_discovery_returns_sorted(self):
        """Discovery results should be sorted by name."""
        manifests = discover_modules()
        names = [m.name for m in manifests]
        assert names == sorted(names)


# ── Health check tests ──────────────────────────────────────

class TestHealthChecks:
    """Tests for health check executor."""

    def test_check_module_health_no_checks(self):
        """A module with no health checks returns 'unknown' status."""
        manifest = CapabilityManifest(name="test", version="1.0.0", description="")
        status = check_module_health(manifest)
        assert status.overall == "unknown"
        assert len(status.checks) == 0
        assert status.error == "No health checks declared"

    def test_check_module_health_passing(self):
        """A module with a passing health check returns 'healthy'."""
        manifest = CapabilityManifest(
            name="test", version="1.0.0", description="",
            health_checks=[HealthCheck(command="exit 0", description="Always pass")],
        )
        status = check_module_health(manifest)
        assert status.overall == "healthy"
        assert status.passed_count == 1
        assert status.failed_count == 0

    def test_check_module_health_failing(self):
        """A module with a failing health check returns 'unhealthy'."""
        manifest = CapabilityManifest(
            name="test", version="1.0.0", description="",
            health_checks=[HealthCheck(command="exit 1", description="Always fail")],
        )
        status = check_module_health(manifest)
        assert status.overall == "unhealthy"
        assert status.passed_count == 0
        assert status.failed_count == 1

    def test_check_module_health_mixed(self):
        """A module with mixed results returns 'degraded'."""
        manifest = CapabilityManifest(
            name="test", version="1.0.0", description="",
            health_checks=[
                HealthCheck(command="exit 0", description="Pass"),
                HealthCheck(command="exit 1", description="Fail"),
            ],
        )
        status = check_module_health(manifest)
        assert status.overall == "degraded"
        assert status.passed_count == 1
        assert status.failed_count == 1

    def test_health_status_to_dict(self):
        """HealthStatus.to_dict should produce JSON-serializable output."""
        manifest = CapabilityManifest(
            name="test", version="1.0.0", description="",
            health_checks=[HealthCheck(command="exit 0")],
        )
        status = check_module_health(manifest)
        d = status.to_dict()
        assert d["module_name"] == "test"
        assert d["overall"] == "healthy"
        json.dumps(d)

    def test_check_credentials(self):
        """check_credentials should report env var presence."""
        manifest = CapabilityManifest(
            name="test", version="1.0.0", description="",
            credentials=[
                CredentialRef(env="PATH", required=True),
                CredentialRef(env="DEFINITELY_NOT_SET_VAR_XYZ", required=False),
            ],
        )
        results = check_credentials(manifest)
        assert len(results) == 2
        assert results[0]["env"] == "PATH"
        assert results[0]["present"] is True
        assert results[1]["present"] is False


# ── Integration: get_registry singleton ─────────────────────

class TestRegistrySingleton:
    """Tests for the module-level singleton."""

    def test_get_registry_returns_same_instance(self):
        """get_registry should return the same instance on repeated calls."""
        reset_registry()
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_get_registry_auto_discovers(self):
        """get_registry should auto-discover modules on first call."""
        reset_registry()
        r = get_registry()
        assert len(r) >= 17
        assert "kernel" in r
        assert "search" in r
        assert "scheduler" in r
        assert "runtime" in r

    def test_reset_registry(self):
        """reset_registry should clear the singleton."""
        reset_registry()
        r1 = get_registry()
        reset_registry()
        r2 = get_registry()
        assert r1 is not r2


# ── Cross-module consistency tests ──────────────────────────

class TestCrossModuleConsistency:
    """Tests that verify consistency across all module manifests."""

    def test_all_module_names_match_directory(self):
        """Each manifest name must match its parent directory name."""
        modules_dir = PROJECT_ROOT / "modules"
        for d in sorted(modules_dir.iterdir()):
            if not d.is_dir() or d.name.startswith('.') or d.name.startswith('_'):
                continue
            if d.name in {'tests', '__pycache__'}:
                continue
            manifest_path = d / "manifest.json"
            if not manifest_path.exists():
                continue
            with open(manifest_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            name = raw.get("name") or raw.get("module")
            assert name == d.name, f"Manifest name {name!r} != directory {d.name!r}"

    def test_all_versions_are_semver(self):
        """All manifest versions must be valid semantic versions."""
        for m in discover_modules():
            assert m.version.count('.') >= 2, f"{m.name}: version {m.version} not semver"

    def test_no_duplicate_capability_names_within_module(self):
        """No module should declare duplicate capability names."""
        for m in discover_modules():
            cap_names = [c.name for c in m.capabilities]
            duplicates = [n for n in cap_names if cap_names.count(n) > 1]
            assert not duplicates, f"{m.name}: duplicate capabilities {duplicates}"

    def test_entrypoint_kinds_valid(self):
        """All entrypoint kinds must be from the valid set."""
        for m in discover_modules():
            for kind in m.entrypoints:
                assert kind in VALID_ENTRYPOINT_KINDS, (
                    f"{m.name}: invalid entrypoint kind {kind!r}"
                )

    def test_health_check_commands_non_empty(self):
        """All health check commands must be non-empty strings."""
        for m in discover_modules():
            for hc in m.health_checks:
                assert hc.command, f"{m.name}: empty health check command"
                assert isinstance(hc.command, str)
