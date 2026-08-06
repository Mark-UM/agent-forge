from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import sqlite3
import threading

import pytest

from modules.kernel.contracts import (
    ArtifactStatus,
    BudgetLimit,
    HandoffStatus,
    KernelContractError,
    TaskStatus,
)
from modules.kernel.lifecycle import (
    LifecycleConflictError,
    LifecycleRepository,
)
from modules.kernel.repository import ConcurrencyConflictError
from modules.kernel.tests._lifecycle_test_support import (
    NOW,
    handoff_request,
    provenance,
    running_task,
)


def test_handoff_rejects_same_source_and_target(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    with pytest.raises(KernelContractError, match="differ"):
        repo.request_handoff(
            task_id=task.task_id,
            source_agent_id="coordinator",
            target_agent_id="coordinator",
            reason="Invalid self transfer",
            bounded_context={"ref": "task.input"},
            expected_artifact="text.patch.v1",
            acceptance_criteria=("bounded",),
            transferred_budget=BudgetLimit(steps=1),
            idempotency_key="self-handoff",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )


def test_handoff_transfers_ownership_only_when_consumed(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    requested, pending, created = handoff_request(repo, task)
    assert created
    assert requested.status is HandoffStatus.REQUESTED
    assert pending.status is TaskStatus.HANDOFF_PENDING
    assert pending.owner_agent_id == "coordinator"
    assert pending.claim_token is None

    accepted = repo.accept_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        expected_version=requested.record_version,
        now=NOW + timedelta(seconds=1),
    )
    still_pending = repo.get_task(task.task_id)
    assert accepted.status is HandoffStatus.ACCEPTED
    assert still_pending is not None
    assert still_pending.owner_agent_id == "coordinator"

    consumed, transferred, first = repo.consume_handoff(
        accepted.handoff_id,
        target_agent_id="worker.text",
        expected_version=accepted.record_version,
        consume_idempotency_key="consume-1",
        lease_seconds=600,
        now=NOW + timedelta(seconds=2),
    )
    assert first
    assert consumed.status is HandoffStatus.CONSUMED
    assert transferred.status is TaskStatus.RUNNING
    assert transferred.owner_agent_id == "worker.text"
    assert transferred.claim_owner == "worker.text"
    assert transferred.claim_token

def test_duplicate_handoff_consumption_is_safe_replay(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    requested, _, _ = handoff_request(repo, task)
    accepted = repo.accept_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        expected_version=0,
        now=NOW + timedelta(seconds=1),
    )
    first, first_task, first_created = repo.consume_handoff(
        accepted.handoff_id,
        target_agent_id="worker.text",
        expected_version=accepted.record_version,
        consume_idempotency_key="consume-safe",
        now=NOW + timedelta(seconds=2),
    )
    replay, replay_task, replay_created = repo.consume_handoff(
        accepted.handoff_id,
        target_agent_id="worker.text",
        expected_version=accepted.record_version,
        consume_idempotency_key="consume-safe",
        now=NOW + timedelta(seconds=3),
    )
    assert first_created and not replay_created
    assert replay == first
    assert replay_task.record_version == first_task.record_version
    assert replay_task.claim_token == first_task.claim_token

def test_concurrent_handoff_consumers_cannot_transfer_twice(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    requested, _, _ = handoff_request(repo, task)
    accepted = repo.accept_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        expected_version=0,
        now=NOW + timedelta(seconds=1),
    )
    barrier = threading.Barrier(2)

    def consume(key: str):
        barrier.wait()
        try:
            return repo.consume_handoff(
                accepted.handoff_id,
                target_agent_id="worker.text",
                expected_version=accepted.record_version,
                consume_idempotency_key=key,
                now=NOW + timedelta(seconds=2),
            )[2]
        except (ConcurrencyConflictError, LifecycleConflictError):
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, ["consumer-a", "consumer-b"]))
    assert results.count(True) == 1
    assert results.count("conflict") == 1
    current = repo.get_task(task.task_id)
    assert current is not None and current.owner_agent_id == "worker.text"

def test_handoff_rejection_resumes_source_with_new_claim(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    requested, _, _ = handoff_request(repo, task)
    rejected, resumed = repo.reject_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        reason="Required tool is unavailable",
        expected_version=requested.record_version,
        now=NOW + timedelta(seconds=1),
    )
    assert rejected.status is HandoffStatus.REJECTED
    assert resumed.status is TaskStatus.RUNNING
    assert resumed.owner_agent_id == "coordinator"
    assert resumed.claim_token and resumed.claim_token != task.claim_token

def test_handoff_budget_cannot_exceed_task_remaining(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    with pytest.raises(LifecycleConflictError, match="budget"):
        repo.request_handoff(
            task_id=task.task_id,
            source_agent_id="coordinator",
            target_agent_id="worker.text",
            reason="Too much budget",
            bounded_context={"ref": "task.input"},
            expected_artifact="text.patch.v1",
            acceptance_criteria=("bounded",),
            transferred_budget=BudgetLimit(steps=999),
            idempotency_key="too-large",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )

def test_handoff_transaction_failure_rolls_back_record_and_task(tmp_path: Path) -> None:
    class BrokenRepository(LifecycleRepository):
        def _update_task(self, connection, task, expected_version):
            raise sqlite3.OperationalError("injected Task write failure")

    db = tmp_path / "kernel.db"
    good = LifecycleRepository(db)
    task = running_task(good)
    broken = BrokenRepository(db)
    with pytest.raises(sqlite3.OperationalError):
        handoff_request(broken, task)
    assert good.get_task(task.task_id).status is TaskStatus.RUNNING
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM kernel_handoffs").fetchone()[0] == 0

def test_handoff_expiry_and_participant_cancellation_resume_source(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    expiry = (NOW + timedelta(seconds=5)).isoformat()
    requested, _, _ = handoff_request(repo, task, expires_at=expiry)
    expired, resumed = repo.expire_handoff(
        requested.handoff_id,
        expected_version=requested.record_version,
        now=NOW + timedelta(seconds=6),
    )
    assert expired.status is HandoffStatus.EXPIRED
    assert resumed.status is TaskStatus.RUNNING
    assert resumed.owner_agent_id == "coordinator"

    requested2, _, _ = repo.request_handoff(
        task_id=resumed.task_id,
        source_agent_id="coordinator",
        target_agent_id="worker.text",
        reason="A second bounded transfer",
        bounded_context={"input_ref": "task.input"},
        expected_artifact="text.patch.v1",
        acceptance_criteria=("Only the authorized file changes",),
        transferred_budget=BudgetLimit(steps=2, tokens=100, wall_clock_seconds=30, retries=0, tool_calls=1, agent_count=1, child_tasks=0),
        idempotency_key="handoff-2",
        claim_token=resumed.claim_token,
        expected_task_version=resumed.record_version,
        now=NOW + timedelta(seconds=7),
    )
    cancelled, resumed2 = repo.cancel_handoff(
        requested2.handoff_id,
        actor_agent_id="coordinator",
        reason="The Task owner withdrew the transfer",
        expected_version=requested2.record_version,
        now=NOW + timedelta(seconds=8),
    )
    assert cancelled.status is HandoffStatus.CANCELLED
    assert resumed2.owner_agent_id == "coordinator"
    assert resumed2.claim_token

def test_handoff_completion_requires_linked_accepted_artifact(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    requested, _, _ = handoff_request(repo, task)
    accepted = repo.accept_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        expected_version=requested.record_version,
        now=NOW + timedelta(seconds=1),
    )
    consumed, transferred, _ = repo.consume_handoff(
        accepted.handoff_id,
        target_agent_id="worker.text",
        expected_version=accepted.record_version,
        consume_idempotency_key="complete-consume",
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(LifecycleConflictError, match="expected Artifact"):
        repo.complete_handoff(
            consumed.handoff_id,
            target_agent_id="worker.text",
            expected_version=consumed.record_version,
            claim_token=transferred.claim_token,
            expected_task_version=transferred.record_version,
            now=NOW + timedelta(seconds=3),
        )

    workspace = tmp_path / "workspace"
    (workspace / "work").mkdir(parents=True)
    (workspace / "work" / "note.patch").write_text("patch\n", encoding="utf-8")
    artifact, transferred, _ = repo.register_file_artifact(
        task_id=transferred.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="work/note.patch",
        artifact_type="text.patch.v1",
        provenance=provenance(),
        idempotency_key="handoff-artifact",
        claim_token=transferred.claim_token,
        expected_task_version=transferred.record_version,
        handoff_id=consumed.handoff_id,
        now=NOW + timedelta(seconds=4),
    )
    validating = repo.transition_artifact(
        artifact.artifact_id,
        ArtifactStatus.VALIDATING,
        expected_version=artifact.record_version,
        workspace_root=workspace,
        validator_agent_id="validator.integrity",
        validation={"check": "digest"},
        now=NOW + timedelta(seconds=5),
    )
    accepted_artifact = repo.transition_artifact(
        artifact.artifact_id,
        ArtifactStatus.ACCEPTED,
        expected_version=validating.record_version,
        workspace_root=workspace,
        validator_agent_id="validator.integrity",
        validation={"result": "accepted"},
        now=NOW + timedelta(seconds=6),
    )
    completed, completed_task = repo.complete_handoff(
        consumed.handoff_id,
        target_agent_id="worker.text",
        expected_version=consumed.record_version,
        claim_token=transferred.claim_token,
        expected_task_version=transferred.record_version,
        outcome={"summary": "bounded output produced"},
        now=NOW + timedelta(seconds=7),
    )
    assert completed.status is HandoffStatus.COMPLETED
    assert completed.outcome["artifact_id"] == accepted_artifact.artifact_id
    assert completed_task.current_step == accepted_artifact.artifact_id

def test_consumed_handoff_cancellation_requires_target_claim_and_cancels_task(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    requested, _, _ = handoff_request(repo, task)
    accepted = repo.accept_handoff(
        requested.handoff_id,
        target_agent_id="worker.text",
        expected_version=requested.record_version,
        now=NOW + timedelta(seconds=1),
    )
    consumed, transferred, _ = repo.consume_handoff(
        accepted.handoff_id,
        target_agent_id="worker.text",
        expected_version=accepted.record_version,
        consume_idempotency_key="cancel-consumed",
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(Exception):
        repo.cancel_handoff(
            consumed.handoff_id,
            actor_agent_id="coordinator",
            reason="Former owner cannot cancel transferred work",
            expected_version=consumed.record_version,
            claim_token=transferred.claim_token,
            expected_task_version=transferred.record_version,
            now=NOW + timedelta(seconds=3),
        )
    cancelled, terminal = repo.cancel_handoff(
        consumed.handoff_id,
        actor_agent_id="worker.text",
        reason="Target cancelled active work",
        expected_version=consumed.record_version,
        claim_token=transferred.claim_token,
        expected_task_version=transferred.record_version,
        now=NOW + timedelta(seconds=3),
    )
    assert cancelled.status is HandoffStatus.CANCELLED
    assert terminal.status is TaskStatus.CANCELLED
