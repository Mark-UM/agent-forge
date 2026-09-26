from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import os
import sqlite3
import subprocess

import pytest

from modules.kernel.contracts import (
    ArtifactStatus,
    KernelContractError,
)
from modules.kernel.lifecycle import (
    ArtifactIntegrityError,
    LifecycleConflictError,
    LifecycleRepository,
)
from modules.kernel.tests._lifecycle_test_support import (
    NOW,
    provenance,
    running_task,
)


def test_artifact_registration_verifies_path_digest_and_task_reference(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    (workspace / "work").mkdir(parents=True)
    file_path = workspace / "work" / "note.txt"
    file_path.write_bytes(b"hello\n")

    artifact, updated_task, created = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="work/note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="artifact-1",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        expected_digest="sha256:" + __import__("hashlib").sha256(b"hello\n").hexdigest(),
        now=NOW,
    )
    assert created
    assert artifact.validation_status is ArtifactStatus.MATERIALIZED
    assert artifact.artifact_id in updated_task.artifact_ids
    assert repo.verify_artifact(artifact.artifact_id, workspace_root=workspace)

    validating = repo.transition_artifact(
        artifact.artifact_id,
        ArtifactStatus.VALIDATING,
        expected_version=artifact.record_version,
        workspace_root=workspace,
        validator_agent_id="validator.integrity",
        validation={"check": "digest"},
        now=NOW + timedelta(seconds=1),
    )
    accepted = repo.transition_artifact(
        artifact.artifact_id,
        ArtifactStatus.ACCEPTED,
        expected_version=validating.record_version,
        workspace_root=workspace,
        validator_agent_id="validator.integrity",
        validation={"checks": ["digest", "path"], "result": "accepted"},
        now=NOW + timedelta(seconds=2),
    )
    assert accepted.validation_status is ArtifactStatus.ACCEPTED

def test_artifact_path_escape_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(Exception):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="../outside.txt",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="escape",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )

    link = workspace / "linked.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ArtifactIntegrityError, match="escapes"):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="linked.txt",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="symlink-escape",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )

def test_artifact_case_variant_kernel_storage_is_rejected(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    protected = workspace / "_RUNTIME" / "KERNEL" / "authority.db"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"protected")

    with pytest.raises(KernelContractError, match="Kernel authority storage"):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="_RUNTIME/KERNEL/authority.db",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="case-variant-kernel-storage",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )


def test_artifact_internal_symlink_to_git_is_rejected(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    protected = workspace / ".git" / "config"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"protected")
    alias = workspace / "alias"
    try:
        alias.symlink_to(protected.parent, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("directory symlink creation is unavailable")
        # Windows junctions do not require the symlink privilege.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(protected.parent)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("directory junction creation is unavailable")

    with pytest.raises(ArtifactIntegrityError, match="protected"):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="alias/config",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="internal-git-alias",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("root_parts", "reference"),
    [((".git",), "config"), (("_runtime",), "kernel/kernel.db")],
)
def test_artifact_reselected_protected_root_is_rejected(
    tmp_path: Path, root_parts: tuple[str, ...], reference: str,
) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    protected_root = (tmp_path / "workspace").joinpath(*root_parts)
    protected_file = protected_root / reference
    protected_file.parent.mkdir(parents=True)
    protected_file.write_bytes(b"protected")

    with pytest.raises(ArtifactIntegrityError, match="protected"):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=protected_root,
            reference=reference,
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="reselected-protected-root",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )


def test_artifact_mutation_is_detected_before_acceptance(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.txt"
    file_path.write_text("v1", encoding="utf-8")
    artifact, _, _ = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="mutation",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        now=NOW,
    )
    file_path.write_text("v2", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="changed"):
        repo.transition_artifact(
            artifact.artifact_id,
            ArtifactStatus.VALIDATING,
            expected_version=artifact.record_version,
            workspace_root=workspace,
            validator_agent_id="validator.integrity",
            now=NOW + timedelta(seconds=1),
        )

def test_artifact_changed_bytes_require_explicit_supersession(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "note.txt"
    file_path.write_text("v1", encoding="utf-8")
    first, task_after_first, _ = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="first",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        now=NOW,
    )
    validating = repo.transition_artifact(
        first.artifact_id,
        ArtifactStatus.VALIDATING,
        expected_version=0,
        workspace_root=workspace,
        validator_agent_id="validator.integrity",
        now=NOW + timedelta(seconds=1),
    )
    first = repo.transition_artifact(
        first.artifact_id,
        ArtifactStatus.ACCEPTED,
        expected_version=validating.record_version,
        workspace_root=workspace,
        validator_agent_id="validator.integrity",
        validation={"result": "accepted"},
        now=NOW + timedelta(seconds=2),
    )
    file_path.write_text("v2", encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError, match="supersedes"):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="note.txt",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="silent-overwrite",
            claim_token=task_after_first.claim_token,
            expected_task_version=task_after_first.record_version,
            now=NOW + timedelta(seconds=3),
        )
    second, _, created = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="explicit-supersession",
        claim_token=task_after_first.claim_token,
        expected_task_version=task_after_first.record_version,
        supersedes_artifact_id=first.artifact_id,
        expected_previous_digest=first.digest,
        now=NOW + timedelta(seconds=4),
    )
    assert created
    assert second.supersedes_artifact_id == first.artifact_id
    assert repo.get_artifact(first.artifact_id).validation_status is ArtifactStatus.SUPERSEDED

def test_artifact_idempotent_replay_and_duplicate_identity_rejection(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("same", encoding="utf-8")
    first, changed_task, created = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="artifact-replay",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        now=NOW,
    )
    replay, replay_task, replay_created = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="artifact-replay",
        claim_token="unused-on-replay",
        expected_task_version=task.record_version,
        now=NOW,
    )
    assert created and not replay_created
    assert replay == first
    assert replay_task.record_version == changed_task.record_version
    with pytest.raises(LifecycleConflictError, match="already registered"):
        repo.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="note.txt",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="artifact-new-identity",
            claim_token=changed_task.claim_token,
            expected_task_version=changed_task.record_version,
            now=NOW + timedelta(seconds=1),
        )

def test_artifact_registration_transaction_failure_rolls_back(tmp_path: Path) -> None:
    class BrokenRepository(LifecycleRepository):
        def _update_task(self, connection, task, expected_version):
            raise sqlite3.OperationalError("injected Task write failure")

    db = tmp_path / "kernel.db"
    good = LifecycleRepository(db)
    task = running_task(good, owner="worker.text")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("content", encoding="utf-8")
    broken = BrokenRepository(db)
    with pytest.raises(sqlite3.OperationalError):
        broken.register_file_artifact(
            task_id=task.task_id,
            producer_agent_id="worker.text",
            workspace_root=workspace,
            reference="note.txt",
            artifact_type="text.file.v1",
            provenance=provenance(),
            idempotency_key="artifact-rollback",
            claim_token=task.claim_token,
            expected_task_version=task.record_version,
            now=NOW,
        )
    assert good.get_task(task.task_id).artifact_ids == ()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM kernel_artifacts").fetchone()[0] == 0

def test_artifact_cannot_be_marked_superseded_without_replacement(tmp_path: Path) -> None:
    repo = LifecycleRepository(tmp_path / "kernel.db")
    task = running_task(repo, owner="worker.text")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("content", encoding="utf-8")
    artifact, _, _ = repo.register_file_artifact(
        task_id=task.task_id,
        producer_agent_id="worker.text",
        workspace_root=workspace,
        reference="note.txt",
        artifact_type="text.file.v1",
        provenance=provenance(),
        idempotency_key="orphan-superseded",
        claim_token=task.claim_token,
        expected_task_version=task.record_version,
        now=NOW,
    )
    with pytest.raises(KernelContractError, match="replacement"):
        repo.transition_artifact(
            artifact.artifact_id,
            ArtifactStatus.SUPERSEDED,
            expected_version=artifact.record_version,
            workspace_root=workspace,
            validator_agent_id="validator.integrity",
            now=NOW + timedelta(seconds=1),
        )
