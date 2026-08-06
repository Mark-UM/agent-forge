from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from modules.memory.candidates import (
    CandidateStateError,
    SensitiveMemoryError,
    add_candidate,
    approve_candidate,
    expire_candidates,
    get_candidate,
    list_candidates,
    reject_candidate,
)


@pytest.fixture()
def paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "runtime" / "memory.db",
        tmp_path / "memory" / "approved-candidates.md",
        tmp_path / "runtime" / "candidate.lock",
    )


def test_sensitive_candidate_is_not_persisted(paths) -> None:
    db_path, _, _ = paths
    with pytest.raises(SensitiveMemoryError, match="not persisted"):
        add_candidate(
            "Remember API key=very-secret-value",
            db_path=db_path,
            env={},
        )
    assert not db_path.exists()


def test_known_environment_secret_is_not_persisted(paths) -> None:
    db_path, _, _ = paths
    with pytest.raises(SensitiveMemoryError):
        add_candidate(
            "The token is exact-secret-token-value",
            db_path=db_path,
            env={"DEEPSEEK_API_KEY": "exact-secret-token-value"},
        )
    assert not db_path.exists()


def test_duplicate_pending_candidate_returns_existing_row(paths) -> None:
    db_path, _, _ = paths
    first, created_first = add_candidate(
        "Prefer concise implementation notes.",
        kind="preference",
        source="session-1",
        db_path=db_path,
        env={},
    )
    second, created_second = add_candidate(
        "  Prefer concise   implementation notes. ",
        kind="preference",
        source="session-2",
        db_path=db_path,
        env={},
    )

    assert created_first is True
    assert created_second is False
    assert second.candidate_id == first.candidate_id
    assert len(list_candidates(db_path=db_path)) == 1


def test_pending_candidate_does_not_write_formal_memory(paths) -> None:
    db_path, memory_path, _ = paths
    candidate, _ = add_candidate(
        "Use UTC timestamps for persisted records.",
        kind="lesson",
        db_path=db_path,
        env={},
    )
    assert candidate.status == "pending"
    assert not memory_path.exists()


def test_approval_writes_formal_memory_and_is_idempotent(paths) -> None:
    db_path, memory_path, lock_path = paths
    candidate, _ = add_candidate(
        "Use UTC timestamps for persisted records.",
        kind="lesson",
        source="test-session",
        metadata={"project": "agent-forge"},
        db_path=db_path,
        env={},
    )

    approved = approve_candidate(
        candidate.candidate_id,
        note="confirmed",
        db_path=db_path,
        memory_path=memory_path,
        lock_path=lock_path,
    )
    approved_again = approve_candidate(
        candidate.candidate_id,
        note="ignored second approval",
        db_path=db_path,
        memory_path=memory_path,
        lock_path=lock_path,
    )

    text = memory_path.read_text(encoding="utf-8")
    marker = f"<!-- memory-candidate:{candidate.candidate_id} -->"
    assert approved.status == "approved"
    assert approved.review_note == "confirmed"
    assert approved_again.status == "approved"
    assert text.count(marker) == 1
    assert "Use UTC timestamps" in text
    assert "test-session" in text


def test_rejected_candidate_cannot_be_approved(paths) -> None:
    db_path, memory_path, lock_path = paths
    candidate, _ = add_candidate(
        "Candidate that should be rejected.",
        db_path=db_path,
        env={},
    )
    rejected = reject_candidate(
        candidate.candidate_id,
        note="not durable",
        db_path=db_path,
    )

    assert rejected.status == "rejected"
    assert rejected.review_note == "not durable"
    assert not memory_path.exists()
    with pytest.raises(CandidateStateError, match="rejected"):
        approve_candidate(
            candidate.candidate_id,
            db_path=db_path,
            memory_path=memory_path,
            lock_path=lock_path,
        )


def test_expired_candidate_is_not_approved(paths) -> None:
    db_path, memory_path, lock_path = paths
    candidate, _ = add_candidate(
        "Short lived candidate.",
        ttl_days=1,
        db_path=db_path,
        env={},
    )
    future = datetime.now(timezone.utc) + timedelta(days=2)
    assert expire_candidates(now=future, db_path=db_path) == 1
    expired = get_candidate(candidate.candidate_id, db_path=db_path)
    assert expired is not None
    assert expired.status == "expired"

    with pytest.raises(CandidateStateError, match="expired"):
        approve_candidate(
            candidate.candidate_id,
            db_path=db_path,
            memory_path=memory_path,
            lock_path=lock_path,
        )
    assert not memory_path.exists()


def test_list_can_return_all_statuses(paths) -> None:
    db_path, _, _ = paths
    pending, _ = add_candidate("Pending memory.", db_path=db_path, env={})
    rejected_candidate, _ = add_candidate(
        "Rejected memory.", db_path=db_path, env={}
    )
    reject_candidate(rejected_candidate.candidate_id, db_path=db_path)

    all_candidates = list_candidates(status=None, db_path=db_path)
    assert {candidate.candidate_id for candidate in all_candidates} == {
        pending.candidate_id,
        rejected_candidate.candidate_id,
    }
    assert {candidate.status for candidate in all_candidates} == {
        "pending",
        "rejected",
    }


def test_database_schema_uses_explicit_statuses(paths) -> None:
    db_path, _, _ = paths
    add_candidate("Schema candidate.", db_path=db_path, env={})
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status, content_hash FROM memory_candidates"
        ).fetchone()
    assert row[0] == "pending"
    assert len(row[1]) == 64
