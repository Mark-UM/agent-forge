from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from modules.kernel.contracts import (
    BudgetLimit,
    SensitiveValueError,
)
from modules.kernel.lifecycle import (
    LifecycleRepository,
    LifecycleRepositoryError,
)
from modules.kernel.tests._lifecycle_test_support import (
    NOW,
    handoff_request,
    running_task,
)


def test_records_schema_is_versioned_and_reopens(tmp_path: Path) -> None:
    db = tmp_path / "kernel.db"
    repo = LifecycleRepository(db)
    repo.initialize()
    repo.initialize()
    with sqlite3.connect(db) as connection:
        version, checksum = connection.execute(
            "SELECT version, checksum FROM kernel_records_schema_migrations"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kernel_%'"
            )
        }
    assert version == 1
    assert len(checksum) == 64
    assert {
        "kernel_handoffs",
        "kernel_artifacts",
        "kernel_approvals",
        "kernel_domain_audit",
    }.issubset(tables)

def test_secret_shaped_lifecycle_metadata_is_rejected(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    with pytest.raises(SensitiveValueError):
        repo.request_handoff(
            task_id=task.task_id,
            source_agent_id="coordinator",
            target_agent_id="worker.text",
            reason="Use sk-abcdefghijklmnop for this work",
            bounded_context={"ref": "task.input"},
            expected_artifact="text.patch.v1",
            acceptance_criteria=("bounded",),
            transferred_budget=BudgetLimit(),
            idempotency_key="secret-handoff",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )
    with pytest.raises(SensitiveValueError):
        repo.request_approval(
            task_id=task.task_id,
            requested_action={"operation": "workspace.write", "target": "note.txt", "api_key": "not-allowed"},
            reason="Protected action",
            scope={"workspace_write": ["note.txt"]},
            requester_agent_id="coordinator",
            idempotency_key="secret-approval",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )

def test_audit_events_are_append_only_and_restart_visible(tmp_path: Path) -> None:
    db = tmp_path / "kernel.db"
    repo = LifecycleRepository(db)
    task = running_task(repo)
    requested, _, _ = handoff_request(repo, task)
    accepted = repo.accept_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        expected_version=0,
        now=NOW + timedelta(seconds=1),
    )
    repo.consume_handoff(
        accepted.handoff_id,
        target_agent_id="worker.text",
        expected_version=accepted.record_version,
        consume_idempotency_key="audit-consume",
        now=NOW + timedelta(seconds=2),
    )
    reopened = LifecycleRepository(db)
    events = reopened.list_audit_events(record_type="handoff", record_id=requested.handoff_id)
    assert [event.event for event in events] == ["requested", "accepted", "consumed"]
    assert all(event.task_id == task.task_id for event in events)

def test_records_schema_tamper_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "kernel.db"
    repo = LifecycleRepository(db)
    repo.initialize()
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE kernel_domain_audit")
        connection.commit()
    with pytest.raises(LifecycleRepositoryError, match="missing table"):
        repo.initialize()

def test_task_scoped_lists_and_database_handles_are_closed(tmp_path: Path) -> None:
    db = tmp_path / "kernel.db"
    repo = LifecycleRepository(db)
    task = running_task(repo)
    handoff, _, _ = handoff_request(repo, task)
    assert [item.handoff_id for item in repo.list_handoffs(task.task_id)] == [handoff.handoff_id]
    assert repo.list_artifacts(task.task_id) == []
    assert repo.list_approvals(task.task_id) == []
    # On Windows this unlink fails if a SQLite handle leaked from the read APIs.
    db.unlink()
    assert not db.exists()

def test_rejected_secret_is_absent_from_database_files(tmp_path: Path) -> None:
    db = tmp_path / "kernel.db"
    repo = LifecycleRepository(db)
    task = running_task(repo)
    canary = "sk-abcdefghijklmnopqrstuv"
    with pytest.raises(SensitiveValueError):
        repo.request_handoff(
            task_id=task.task_id,
            source_agent_id="coordinator",
            target_agent_id="worker.text",
            reason=f"Never persist {canary}",
            bounded_context={"ref": "task.input"},
            expected_artifact="text.patch.v1",
            acceptance_criteria=("bounded",),
            transferred_budget=BudgetLimit(),
            idempotency_key="canary-rejected",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )
    for candidate in (db, Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if candidate.exists():
            assert canary.encode() not in candidate.read_bytes()

def test_records_migration_ledger_inconsistency_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "kernel.db"
    repo = LifecycleRepository(db)
    repo.initialize()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT INTO kernel_records_schema_migrations(version, checksum, applied_at) VALUES (0, ?, ?)",
            ("0" * 64, NOW.isoformat()),
        )
        connection.commit()
    with pytest.raises(LifecycleRepositoryError, match="ledger is inconsistent"):
        repo.initialize()

def test_secret_shaped_lookup_identifier_is_rejected_without_echo(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    repo.initialize()
    secret = "sk-abcdefghijklmnopqrstuv"
    with pytest.raises(SensitiveValueError) as captured:
        repo.get_handoff(secret)
    assert secret not in str(captured.value)
