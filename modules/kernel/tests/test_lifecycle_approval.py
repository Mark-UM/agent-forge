from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from modules.kernel.contracts import (
    ApprovalStatus,
    TaskStatus,
)
from modules.kernel.lifecycle import (
    ApprovalAuthorizationError,
    LifecycleRepository,
)
from modules.kernel.repository import (
    ConcurrencyConflictError,
    IdempotencyConflictError,
)
from modules.kernel.tests._lifecycle_test_support import (
    NOW,
    approval_request,
    running_task,
)


def test_approval_side_effect_gate_requires_approved_exact_action(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    approval, waiting, created = approval_request(repo, task)
    assert created
    assert waiting.status is TaskStatus.WAITING_APPROVAL
    assert waiting.claim_token is None

    approved, task_result = repo.decide_approval(
        approval.approval_id,
        ApprovalStatus.APPROVED,
        decision_maker="human.owner",
        audit_reference="review.pr5",
        expected_version=approval.record_version,
        now=NOW + timedelta(seconds=1),
    )
    assert task_result is None
    assert approved.decision is ApprovalStatus.APPROVED
    assert repo.get_task(task.task_id).status is TaskStatus.WAITING_APPROVAL

    with pytest.raises(ApprovalAuthorizationError, match="changed"):
        repo.consume_approval(
            approved.approval_id,
            requester_agent_id="coordinator",
            requested_action={"operation": "workspace.write", "target": "other.txt", "artifact_digest": "sha256:" + "b" * 64},
            expected_version=approved.record_version,
            consume_idempotency_key="consume-altered",
            now=NOW + timedelta(seconds=2),
        )

    authorization, consumed, resumed, first = repo.consume_approval(
        approved.approval_id,
        requester_agent_id="coordinator",
        requested_action=approved.requested_action,
        expected_version=approved.record_version,
        consume_idempotency_key="consume-exact",
        now=NOW + timedelta(seconds=2),
    )
    assert first
    assert consumed.decision is ApprovalStatus.CONSUMED
    assert authorization.requested_action == approved.requested_action
    assert authorization.scope == approved.scope
    assert resumed.status is TaskStatus.RUNNING
    assert resumed.owner_agent_id == "coordinator"
    assert resumed.claim_token

def test_denied_approval_fails_task_and_cannot_be_consumed(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    approval, _, _ = approval_request(repo, task)
    denied, failed = repo.decide_approval(
        approval.approval_id,
        ApprovalStatus.DENIED,
        decision_maker="human.owner",
        audit_reference="review.denied",
        expected_version=approval.record_version,
        now=NOW + timedelta(seconds=1),
    )
    assert denied.decision is ApprovalStatus.DENIED
    assert failed is not None and failed.status is TaskStatus.FAILED
    with pytest.raises(Exception):
        repo.consume_approval(
            denied.approval_id,
            requester_agent_id="coordinator",
            requested_action=denied.requested_action,
            expected_version=denied.record_version,
            consume_idempotency_key="denied-consume",
            now=NOW + timedelta(seconds=2),
        )

def test_expired_approval_times_out_task(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    expires = (NOW + timedelta(seconds=5)).isoformat()
    approval, _, _ = approval_request(repo, task, expires_at=expires)
    expired, timed_out = repo.expire_approval(
        approval.approval_id,
        expected_version=approval.record_version,
        now=NOW + timedelta(seconds=6),
    )
    assert expired.decision is ApprovalStatus.EXPIRED
    assert timed_out.status is TaskStatus.TIMED_OUT

def test_cancelled_approval_cancels_task(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    approval, _, _ = approval_request(repo, task)
    cancelled, cancelled_task = repo.cancel_approval(
        approval.approval_id,
        expected_version=approval.record_version,
        decision_maker="human.owner",
        audit_reference="review.cancelled",
        now=NOW + timedelta(seconds=1),
    )
    assert cancelled.decision is ApprovalStatus.CANCELLED
    assert cancelled_task.status is TaskStatus.CANCELLED

def test_duplicate_approval_request_replays_and_changed_request_conflicts(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    first, waiting, created = approval_request(repo, task)
    assert created
    replay, replay_task, replay_created = repo.request_approval(
        task_id=task.task_id,
        requested_action=first.requested_action,
        reason=first.reason,
        scope=first.scope,
        requester_agent_id="coordinator",
        idempotency_key="approval-1",
        claim_token="unused-on-replay",
        expected_task_version=task.record_version,
        now=NOW,
    )
    assert not replay_created and replay.approval_id == first.approval_id
    assert replay_task.record_version == waiting.record_version
    with pytest.raises(IdempotencyConflictError):
        repo.request_approval(
            task_id=task.task_id,
            requested_action={"operation": "workspace.write", "target": "different.txt", "artifact_digest": "sha256:" + "a" * 64},
            reason=first.reason,
            scope={"workspace_write": ["different.txt"]},
            requester_agent_id="coordinator",
            idempotency_key="approval-1",
            claim_token="unused",
            expected_task_version=task.record_version,
            now=NOW,
        )

def test_duplicate_approval_decision_is_conflict(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    approval, _, _ = approval_request(repo, task)
    approved, _ = repo.decide_approval(
        approval.approval_id,
        ApprovalStatus.APPROVED,
        decision_maker="human.owner",
        audit_reference="review.approved",
        expected_version=approval.record_version,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ConcurrencyConflictError):
        repo.decide_approval(
            approval.approval_id,
            ApprovalStatus.DENIED,
            decision_maker="other.human",
            audit_reference="review.changed",
            expected_version=approval.record_version,
            now=NOW + timedelta(seconds=2),
        )
    assert repo.get_approval(approval.approval_id) == approved

def test_approval_transaction_failure_rolls_back_record_and_task(tmp_path: Path) -> None:
    class BrokenRepository(LifecycleRepository):
        def _update_task(self, connection, task, expected_version):
            raise sqlite3.OperationalError("injected Task write failure")

    db = tmp_path / "kernel.db"
    good = LifecycleRepository(db)
    task = running_task(good)
    broken = BrokenRepository(db)
    with pytest.raises(sqlite3.OperationalError):
        approval_request(broken, task)
    assert good.get_task(task.task_id).status is TaskStatus.RUNNING
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM kernel_approvals").fetchone()[0] == 0

def test_approval_consumption_safe_replay_returns_same_authorization(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo)
    approval, _, _ = approval_request(repo, task)
    approved, _ = repo.decide_approval(
        approval.approval_id,
        ApprovalStatus.APPROVED,
        decision_maker="human.owner",
        audit_reference="review.safe-replay",
        expected_version=approval.record_version,
        now=NOW + timedelta(seconds=1),
    )
    first_auth, consumed, first_task, created = repo.consume_approval(
        approved.approval_id,
        requester_agent_id="coordinator",
        requested_action=approved.requested_action,
        expected_version=approved.record_version,
        consume_idempotency_key="approval-consume-safe",
        now=NOW + timedelta(seconds=2),
    )
    replay_auth, replay, replay_task, replay_created = repo.consume_approval(
        approved.approval_id,
        requester_agent_id="coordinator",
        requested_action=approved.requested_action,
        expected_version=approved.record_version,
        consume_idempotency_key="approval-consume-safe",
        now=NOW + timedelta(seconds=3),
    )
    assert created and not replay_created
    assert replay == consumed
    assert replay_auth == first_auth
    assert replay_task.record_version == first_task.record_version
