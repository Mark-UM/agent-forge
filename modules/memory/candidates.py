#!/usr/bin/env python3
"""Explicit approval queue for durable Agent Forge memory.

No model or workflow may write durable memory through this module without an
explicit approval call. Candidate text is checked for secrets before SQLite
persistence. Approval appends an idempotent, auditable entry to the private
``_data/memory/approved-candidates.md`` file and only then marks the database
row approved.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Iterable, Mapping, Optional
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "_runtime" / "mcp-sqlite.db"
APPROVED_MEMORY_PATH = PROJECT_ROOT / "_data" / "memory" / "approved-candidates.md"
LOCK_PATH = PROJECT_ROOT / "_runtime" / "memory" / "candidates.lock"
VALID_STATUSES = frozenset({"pending", "approved", "rejected", "expired"})
VALID_KINDS = frozenset(
    {"general", "lesson", "decision", "preference", "project", "style", "people"}
)
MAX_CONTENT_CHARS = 50_000
MAX_SOURCE_CHARS = 500
DEFAULT_TTL_DAYS = 30
_SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "SERPER_API_KEY",
    "SILICONFLOW_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "BROWSER_USE_API_KEY",
    "GITHUB_TOKEN",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    re.compile(
        r"(?i)\b(api[\s_-]*key|access[\s_-]*token|secret|password)\b"
        r"\s*[:=]\s*([^\s,;]{6,})"
    ),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
        r"[A-Za-z0-9_-]{10,}\b"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class MemoryCandidateError(RuntimeError):
    pass


class SensitiveMemoryError(MemoryCandidateError):
    pass


class CandidateStateError(MemoryCandidateError):
    pass


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    kind: str
    content: str
    content_hash: str
    source: str
    status: str
    created_at: str
    expires_at: str
    reviewed_at: Optional[str]
    review_note: Optional[str]
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "content": self.content,
            "content_hash": self.content_hash,
            "source": self.source,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "reviewed_at": self.reviewed_at,
            "review_note": self.review_note,
            "metadata": dict(self.metadata),
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def init_candidate_store(db_path: Path = DB_PATH) -> None:
    with _connection(db_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS memory_candidates (
                candidate_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_note TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_memory_candidates_status_created
               ON memory_candidates(status, created_at)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_memory_candidates_hash
               ON memory_candidates(kind, content_hash, status)"""
        )
        connection.commit()


def _known_secrets(env: Mapping[str, str]) -> list[str]:
    return sorted(
        {
            value.strip()
            for name in _SECRET_ENV_NAMES
            if (value := env.get(name, "")).strip() and len(value.strip()) >= 6
        },
        key=len,
        reverse=True,
    )


def sensitive_matches(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    environment = os.environ if env is None else env
    matches: list[str] = []
    for secret in _known_secrets(environment):
        if secret in text:
            matches.append("known_environment_secret")
            break
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            matches.append("secret_pattern")
    return tuple(dict.fromkeys(matches))


def _normalise_content(content: str) -> str:
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    value = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    if not value:
        raise ValueError("content must not be empty")
    if len(value) > MAX_CONTENT_CHARS:
        raise ValueError(f"content exceeds {MAX_CONTENT_CHARS} characters")
    return value


def _normalise_kind(kind: str) -> str:
    value = str(kind).strip().lower()
    if value not in VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
    return value


def _normalise_source(source: str) -> str:
    value = str(source or "unspecified").strip()
    if not value:
        value = "unspecified"
    if len(value) > MAX_SOURCE_CHARS:
        raise ValueError(f"source exceeds {MAX_SOURCE_CHARS} characters")
    return value


def _content_hash(kind: str, content: str) -> str:
    canonical = re.sub(r"\s+", " ", content).strip().lower()
    return hashlib.sha256(f"{kind}:{canonical}".encode("utf-8")).hexdigest()


def _row_to_candidate(row: sqlite3.Row | Mapping[str, Any]) -> MemoryCandidate:
    metadata_raw = row["metadata_json"]
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except json.JSONDecodeError:
        metadata = {}
    return MemoryCandidate(
        candidate_id=str(row["candidate_id"]),
        kind=str(row["kind"]),
        content=str(row["content"]),
        content_hash=str(row["content_hash"]),
        source=str(row["source"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        expires_at=str(row["expires_at"]),
        reviewed_at=(str(row["reviewed_at"]) if row["reviewed_at"] else None),
        review_note=(str(row["review_note"]) if row["review_note"] else None),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def add_candidate(
    content: str,
    *,
    kind: str = "general",
    source: str = "unspecified",
    ttl_days: int = DEFAULT_TTL_DAYS,
    metadata: Optional[Mapping[str, Any]] = None,
    env: Mapping[str, str] | None = None,
    db_path: Path = DB_PATH,
) -> tuple[MemoryCandidate, bool]:
    """Create a pending candidate or return the existing pending/approved row."""

    value = _normalise_content(content)
    candidate_kind = _normalise_kind(kind)
    candidate_source = _normalise_source(source)
    if not 1 <= int(ttl_days) <= 365:
        raise ValueError("ttl_days must be between 1 and 365")
    metadata_dict = dict(metadata or {})
    try:
        metadata_json = json.dumps(metadata_dict, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON serializable") from exc
    # Content, provenance and metadata all become durable. Validate the complete
    # persistence envelope before opening SQLite so rejected candidates leave no
    # database artefact behind.
    sensitive_envelope = "\n".join((value, candidate_source, metadata_json))
    matches = sensitive_matches(sensitive_envelope, env=env)
    if matches:
        raise SensitiveMemoryError(
            "candidate contains sensitive material and was not persisted: "
            + ", ".join(matches)
        )
    candidate_hash = _content_hash(candidate_kind, value)
    now = _utc_now()
    expires = now + timedelta(days=int(ttl_days))
    init_candidate_store(db_path)
    with _connection(db_path) as connection:
        row = connection.execute(
            """SELECT * FROM memory_candidates
               WHERE kind = ? AND content_hash = ?
                 AND status IN ('pending', 'approved')
               ORDER BY created_at DESC LIMIT 1""",
            (candidate_kind, candidate_hash),
        ).fetchone()
        if row is not None:
            return _row_to_candidate(row), False
        candidate_id = f"mem_{uuid.uuid4().hex[:16]}"
        connection.execute(
            """INSERT INTO memory_candidates
               (candidate_id, kind, content, content_hash, source, status,
                created_at, expires_at, reviewed_at, review_note, metadata_json)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, NULL, ?)""",
            (
                candidate_id,
                candidate_kind,
                value,
                candidate_hash,
                candidate_source,
                now.isoformat(),
                expires.isoformat(),
                metadata_json,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    return _row_to_candidate(row), True


def get_candidate(
    candidate_id: str,
    *,
    db_path: Path = DB_PATH,
) -> Optional[MemoryCandidate]:
    init_candidate_store(db_path)
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    return _row_to_candidate(row) if row is not None else None


def list_candidates(
    *,
    status: Optional[str] = "pending",
    limit: int = 100,
    db_path: Path = DB_PATH,
) -> list[MemoryCandidate]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)} or None")
    if not 1 <= int(limit) <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    expire_candidates(db_path=db_path)
    with _connection(db_path) as connection:
        if status is None:
            rows = connection.execute(
                "SELECT * FROM memory_candidates ORDER BY created_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM memory_candidates
                   WHERE status = ? ORDER BY created_at ASC LIMIT ?""",
                (status, int(limit)),
            ).fetchall()
    return [_row_to_candidate(row) for row in rows]


def expire_candidates(
    *,
    now: Optional[datetime] = None,
    db_path: Path = DB_PATH,
) -> int:
    init_candidate_store(db_path)
    current = (now or _utc_now()).astimezone(timezone.utc).isoformat()
    with _connection(db_path) as connection:
        cursor = connection.execute(
            """UPDATE memory_candidates
               SET status = 'expired', reviewed_at = ?,
                   review_note = COALESCE(review_note, 'expired automatically')
               WHERE status = 'pending' AND expires_at <= ?""",
            (current, current),
        )
        connection.commit()
        return max(0, cursor.rowcount)


@contextmanager
def _file_lock(path: Path = LOCK_PATH, timeout: float = 10.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor: Optional[int] = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out acquiring memory lock: {path}")
            time.sleep(0.05)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _approval_marker(candidate_id: str) -> str:
    return f"<!-- memory-candidate:{candidate_id} -->"


def _format_approved_entry(candidate: MemoryCandidate, approved_at: str) -> str:
    metadata = json.dumps(dict(candidate.metadata), ensure_ascii=False, sort_keys=True)
    return (
        f"{_approval_marker(candidate.candidate_id)}\n"
        f"## {candidate.kind.title()} — {approved_at}\n\n"
        f"- Candidate ID: `{candidate.candidate_id}`\n"
        f"- Source: {candidate.source}\n"
        f"- Metadata: `{metadata}`\n\n"
        f"{candidate.content}\n"
    )


def _append_approved_memory(
    candidate: MemoryCandidate,
    *,
    approved_at: str,
    memory_path: Path = APPROVED_MEMORY_PATH,
    lock_path: Path = LOCK_PATH,
) -> None:
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(lock_path):
        existing = (
            memory_path.read_text(encoding="utf-8")
            if memory_path.is_file()
            else "# Approved Memory Candidates\n\n"
        )
        if _approval_marker(candidate.candidate_id) in existing:
            return
        updated = existing.rstrip() + "\n\n" + _format_approved_entry(
            candidate, approved_at
        )
        temporary = memory_path.with_suffix(
            memory_path.suffix + f".{os.getpid()}.tmp"
        )
        try:
            temporary.write_text(updated, encoding="utf-8", newline="\n")
            os.replace(temporary, memory_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def approve_candidate(
    candidate_id: str,
    *,
    note: Optional[str] = None,
    db_path: Path = DB_PATH,
    memory_path: Path = APPROVED_MEMORY_PATH,
    lock_path: Path = LOCK_PATH,
) -> MemoryCandidate:
    candidate = get_candidate(candidate_id, db_path=db_path)
    if candidate is None:
        raise KeyError(f"candidate not found: {candidate_id}")
    if candidate.status == "approved":
        return candidate
    if candidate.status != "pending":
        raise CandidateStateError(
            f"candidate {candidate_id} is {candidate.status}, not pending"
        )
    if datetime.fromisoformat(candidate.expires_at) <= _utc_now():
        expire_candidates(db_path=db_path)
        raise CandidateStateError(f"candidate {candidate_id} is expired")

    reviewed_at = _utc_now().isoformat()
    _append_approved_memory(
        candidate,
        approved_at=reviewed_at,
        memory_path=memory_path,
        lock_path=lock_path,
    )
    with _connection(db_path) as connection:
        cursor = connection.execute(
            """UPDATE memory_candidates
               SET status = 'approved', reviewed_at = ?, review_note = ?
               WHERE candidate_id = ? AND status = 'pending'""",
            (reviewed_at, note, candidate_id),
        )
        connection.commit()
        if cursor.rowcount not in (0, 1):
            raise MemoryCandidateError("unexpected approval update count")
    approved = get_candidate(candidate_id, db_path=db_path)
    if approved is None:
        raise MemoryCandidateError("approved candidate disappeared")
    return approved


def reject_candidate(
    candidate_id: str,
    *,
    note: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> MemoryCandidate:
    candidate = get_candidate(candidate_id, db_path=db_path)
    if candidate is None:
        raise KeyError(f"candidate not found: {candidate_id}")
    if candidate.status == "rejected":
        return candidate
    if candidate.status != "pending":
        raise CandidateStateError(
            f"candidate {candidate_id} is {candidate.status}, not pending"
        )
    reviewed_at = _utc_now().isoformat()
    with _connection(db_path) as connection:
        connection.execute(
            """UPDATE memory_candidates
               SET status = 'rejected', reviewed_at = ?, review_note = ?
               WHERE candidate_id = ? AND status = 'pending'""",
            (reviewed_at, note, candidate_id),
        )
        connection.commit()
    rejected = get_candidate(candidate_id, db_path=db_path)
    if rejected is None:
        raise MemoryCandidateError("rejected candidate disappeared")
    return rejected


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Memory candidate approval queue")
    sub = parser.add_subparsers(dest="command", required=True)

    add_parser = sub.add_parser("add")
    add_parser.add_argument("content")
    add_parser.add_argument("--kind", default="general", choices=sorted(VALID_KINDS))
    add_parser.add_argument("--source", default="manual")
    add_parser.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--status", default="pending", choices=sorted(VALID_STATUSES))
    list_parser.add_argument("--limit", type=int, default=100)

    approve_parser = sub.add_parser("approve")
    approve_parser.add_argument("candidate_id")
    approve_parser.add_argument("--note", default=None)

    reject_parser = sub.add_parser("reject")
    reject_parser.add_argument("candidate_id")
    reject_parser.add_argument("--note", default=None)

    sub.add_parser("expire")
    args = parser.parse_args(argv)

    if args.command == "add":
        candidate, created = add_candidate(
            args.content,
            kind=args.kind,
            source=args.source,
            ttl_days=args.ttl_days,
        )
        _print_json({"created": created, "candidate": candidate.to_dict()})
        return 0
    if args.command == "list":
        _print_json(
            [
                candidate.to_dict()
                for candidate in list_candidates(
                    status=args.status, limit=args.limit
                )
            ]
        )
        return 0
    if args.command == "approve":
        _print_json(
            approve_candidate(args.candidate_id, note=args.note).to_dict()
        )
        return 0
    if args.command == "reject":
        _print_json(
            reject_candidate(args.candidate_id, note=args.note).to_dict()
        )
        return 0
    print(expire_candidates())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, MemoryCandidateError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
