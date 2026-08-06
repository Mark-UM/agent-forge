#!/usr/bin/env python3
"""Canonical command-line interface for private local Memory.

Durable lesson/decision writes remain explicit Python APIs. This CLI exposes
non-destructive health/review operations and the full candidate approval queue.
No command prints hidden environment secrets; candidate content is printed only
when the operator explicitly lists or mutates the private local queue.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Optional

from modules.memory import candidates, hook


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Forge local memory")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=candidates.DB_PATH,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--approved-memory-path",
        type=Path,
        default=candidates.APPROVED_MEMORY_PATH,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=candidates.LOCK_PATH,
        help=argparse.SUPPRESS,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Check private Memory structure")
    review = sub.add_parser("review", help="Create a read-only open-action report")
    review.add_argument("--output", type=Path, default=None)

    candidate = sub.add_parser("candidates", help="Manage approval candidates")
    candidate_sub = candidate.add_subparsers(dest="candidate_command", required=True)

    add = candidate_sub.add_parser("add", help="Add a pending candidate")
    add.add_argument("content")
    add.add_argument("--kind", choices=sorted(candidates.VALID_KINDS), default="general")
    add.add_argument("--source", default="manual")
    add.add_argument("--ttl-days", type=int, default=candidates.DEFAULT_TTL_DAYS)

    listing = candidate_sub.add_parser("list", help="List candidates")
    listing.add_argument(
        "--status",
        choices=[*sorted(candidates.VALID_STATUSES), "all"],
        default="pending",
    )
    listing.add_argument("--limit", type=int, default=100)

    approve = candidate_sub.add_parser("approve", help="Approve durable memory")
    approve.add_argument("candidate_id")
    approve.add_argument("--note", default=None)

    reject = candidate_sub.add_parser("reject", help="Reject a candidate")
    reject.add_argument("candidate_id")
    reject.add_argument("--note", default=None)

    candidate_sub.add_parser("expire", help="Expire overdue pending candidates")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "health":
        _print(hook.check_memory_health())
        return 0
    if args.command == "review":
        _print(hook.review_memory(args.output))
        return 0

    db_path = args.db_path
    if args.candidate_command == "add":
        candidate, created = candidates.add_candidate(
            args.content,
            kind=args.kind,
            source=args.source,
            ttl_days=args.ttl_days,
            db_path=db_path,
        )
        _print({"created": created, "candidate": candidate.to_dict()})
        return 0
    if args.candidate_command == "list":
        status = None if args.status == "all" else args.status
        _print([
            candidate.to_dict()
            for candidate in candidates.list_candidates(
                status=status,
                limit=args.limit,
                db_path=db_path,
            )
        ])
        return 0
    if args.candidate_command == "approve":
        _print(candidates.approve_candidate(
            args.candidate_id,
            note=args.note,
            db_path=db_path,
            memory_path=args.approved_memory_path,
            lock_path=args.lock_path,
        ).to_dict())
        return 0
    if args.candidate_command == "reject":
        _print(candidates.reject_candidate(
            args.candidate_id,
            note=args.note,
            db_path=db_path,
        ).to_dict())
        return 0
    _print({"expired": candidates.expire_candidates(db_path=db_path)})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, candidates.MemoryCandidateError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
