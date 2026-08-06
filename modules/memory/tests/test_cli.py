from __future__ import annotations

import json
from pathlib import Path

from modules.memory.cli import main


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--db-path",
        str(tmp_path / "memory.db"),
        "--approved-memory-path",
        str(tmp_path / "approved.md"),
        "--lock-path",
        str(tmp_path / "memory.lock"),
    ]


def test_candidate_add_list_and_approve(
    tmp_path: Path,
    capsys,
) -> None:
    args = _base_args(tmp_path)
    assert main(args + [
        "candidates", "add", "Prefer compact deterministic adapters.",
        "--kind", "lesson", "--source", "test",
    ]) == 0
    added = json.loads(capsys.readouterr().out)
    candidate_id = added["candidate"]["candidate_id"]
    assert added["created"] is True

    assert main(args + ["candidates", "list", "--status", "pending"]) == 0
    pending = json.loads(capsys.readouterr().out)
    assert [item["candidate_id"] for item in pending] == [candidate_id]

    assert main(args + ["candidates", "approve", candidate_id]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["status"] == "approved"
    assert candidate_id in (tmp_path / "approved.md").read_text(encoding="utf-8")


def test_candidate_reject_and_all_listing(
    tmp_path: Path,
    capsys,
) -> None:
    args = _base_args(tmp_path)
    main(args + ["candidates", "add", "Temporary preference", "--source", "test"])
    candidate_id = json.loads(capsys.readouterr().out)["candidate"]["candidate_id"]

    assert main(args + ["candidates", "reject", candidate_id, "--note", "obsolete"]) == 0
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["status"] == "rejected"

    main(args + ["candidates", "list", "--status", "all"])
    listing = json.loads(capsys.readouterr().out)
    assert listing[0]["status"] == "rejected"
