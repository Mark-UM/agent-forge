from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from modules.kernel.contracts import BudgetLimit
from modules.kernel.lifecycle import LifecycleRepository


NOW = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)


def running_task(repo: LifecycleRepository, *, owner: str = "coordinator"):
    # Task captures utc_now_iso as its default factory. Freeze that clock so
    # creation and the subsequent claim share the same logical time.
    with patch("modules.kernel.contracts.datetime", wraps=datetime) as clock:
        clock.now.return_value = NOW
        task, created = repo.create_task(
            objective="Maintain one bounded file",
            normalized_input={"path": "work/note.txt"},
            idempotency_key=f"create-{owner}",
            owner_agent_id=owner,
        )
    assert created
    claimed, _ = repo.claim_task(
        task.task_id,
        claimant=owner,
        expected_version=task.record_version,
        lease_seconds=3600,
        now=NOW,
    )
    return claimed


def handoff_request(repo: LifecycleRepository, task, *, expires_at=None):
    return repo.request_handoff(
        task_id=task.task_id,
        source_agent_id="coordinator",
        target_agent_id="worker.text",
        reason="Transfer bounded text maintenance ownership",
        bounded_context={"input_ref": "task.input", "required_keys": ["path"]},
        expected_artifact="text.patch.v1",
        acceptance_criteria=("Only the authorized file changes", "Digest is registered"),
        transferred_budget=BudgetLimit(
            steps=5, tokens=5000, wall_clock_seconds=300, retries=1,
            tool_calls=3, agent_count=1, child_tasks=0,
        ),
        idempotency_key="handoff-1",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        expires_at=expires_at,
        now=NOW,
    )


def approval_request(repo: LifecycleRepository, task, *, expires_at=None):
    return repo.request_approval(
        task_id=task.task_id,
        requested_action={
            "operation": "workspace.write",
            "target": "work/note.txt",
            "artifact_digest": "sha256:" + "a" * 64,
        },
        reason="The protected file write requires a scoped human decision",
        scope={"workspace_write": ["work/note.txt"]},
        requester_agent_id=task.owner_agent_id,
        idempotency_key="approval-1",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        expires_at=expires_at,
        now=NOW,
    )


def provenance():
    return {
        "source_inputs": ["task.input"],
        "step_refs": ["step.worker"],
        "producer_version": "worker.v1",
    }
