from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from modules.kernel import KernelExecutionEngine, LifecycleRepository, TaskRepository
from modules.registry.discovery import discover_modules_report


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_TABLES = {
    "kernel_schema_migrations",
    "kernel_tasks",
    "kernel_task_checkpoints",
    "kernel_records_schema_migrations",
    "kernel_handoffs",
    "kernel_artifacts",
    "kernel_approvals",
    "kernel_domain_audit",
}


def test_kernel_is_a_formal_registry_module() -> None:
    report = discover_modules_report(strict=True)
    assert report.valid, [issue.to_dict() for issue in report.errors]
    manifests = {manifest.name: manifest for manifest in report.manifests}
    assert "kernel" in manifests
    manifest = manifests["kernel"]
    assert manifest.version == "2.0.0"
    assert {capability.name for capability in manifest.capabilities} == {
        "kernel.contracts",
        "kernel.task_authority",
        "kernel.execution",
        "kernel.lifecycle_authority",
    }
    assert len(manifest.storage) == 1
    assert manifest.storage[0].path == "_runtime/kernel/kernel.db"
    assert set(manifest.storage[0].tables) == EXPECTED_TABLES


def test_kernel_execution_and_lifecycle_are_importable_without_framework_or_provider() -> None:
    assert KernelExecutionEngine is not None
    assert LifecycleRepository is not None


def test_kernel_schema_has_a_versioned_checksum(tmp_path: Path) -> None:
    db_path = tmp_path / "kernel.db"
    TaskRepository(db_path).initialize()
    with sqlite3.connect(str(db_path)) as connection:
        row = connection.execute(
            "SELECT version, checksum FROM kernel_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == 1
    assert len(row[1]) == 64
    int(row[1], 16)


def test_kernel_records_schema_has_an_independent_versioned_checksum(tmp_path: Path) -> None:
    db_path = tmp_path / "kernel.db"
    LifecycleRepository(db_path).initialize()
    with sqlite3.connect(str(db_path)) as connection:
        row = connection.execute(
            "SELECT version, checksum FROM kernel_records_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == 1
    assert len(row[1]) == 64
    int(row[1], 16)


def test_kernel_manifest_matches_declared_schema_tables() -> None:
    raw = json.loads(
        (PROJECT_ROOT / "modules" / "kernel" / "manifest.json").read_text(encoding="utf-8")
    )
    assert set(raw["storage"][0]["tables"]) == EXPECTED_TABLES
    assert not (PROJECT_ROOT / "modules" / "_kernel").exists()
