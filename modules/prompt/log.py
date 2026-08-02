"""
Prompt Log — composition logging + version snapshot (v1.5 P0)

Log files:
- _runtime/prompt/composition_log.jsonl  (one entry per composition)
- _runtime/prompt/version.json           (current version snapshot)
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

RUNTIME_DIR = Path(__file__).resolve().parent.parent.parent / "_runtime" / "prompt"
LOG_PATH = RUNTIME_DIR / "composition_log.jsonl"
VERSION_PATH = RUNTIME_DIR / "version.json"


def log_composition(metadata: dict) -> None:
    """Append a composition record to the JSONL log."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    entry = {**metadata, "timestamp": datetime.now().isoformat()}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_version_snapshot(
    sources: list,
    profile: str,
    task: Optional[str] = None,
) -> None:
    """Write the current version snapshot."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "version": "1.5.0",
        "snapshot_at": datetime.now().isoformat(),
        "sources": sources,
        "profile": profile,
        "task": task,
    }
    VERSION_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def read_version_snapshot() -> dict:
    """Read the current version snapshot."""
    if not VERSION_PATH.exists():
        return {}
    return json.loads(VERSION_PATH.read_text(encoding="utf-8"))


def read_recent(limit: int = 20) -> list:
    """Read the most recent N composition log entries.

    Args:
        limit: max number of entries to return. If None or negative, returns all.
               limit=0 returns an empty list (defensive — `entries[-0:]` would
               otherwise return the entire list, which is never what callers want).

    Returns:
        list of dict entries, chronological order (oldest first, newest last).
    """
    if not LOG_PATH.exists():
        return []
    if limit is not None and limit <= 0:
        return []
    entries = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is None:
        return entries
    return entries[-limit:]
