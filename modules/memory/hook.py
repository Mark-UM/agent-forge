"""Private local memory helpers.

This module provides explicit, atomic writes for lessons and architecture
decisions plus a read-only review of open tasks. It never prints memory content
and it is not an automatic telemetry or conversation-capture hook.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = _PROJECT_ROOT / "_data" / "memory"
LESSONS_FILE = MEMORY_DIR / "lessons.md"
DECISIONS_FILE = MEMORY_DIR / "decisions.md"
REPORTS_DIR = _PROJECT_ROOT / "_runtime" / "reports"
_ADR_COUNTER_FILE = _PROJECT_ROOT / "_runtime" / "memory" / "adr_counter.txt"
_COUNTER_LOCK = threading.Lock()
_APPEND_LOCK = threading.RLock()

_OPEN_CHECKBOX = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.+?)\s*$")
_TODO_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:TODO|待办)\s*[:：]\s*(.+?)\s*$", re.I)
_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _ensure_dirs() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _ADR_COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)


def _atomic_write(file_path: Path, content: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(file_path.parent), suffix=".tmp", prefix=f"{file_path.stem}_"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, file_path)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _atomic_append(file_path: Path, content: str) -> None:
    """Append an entry while preserving the optional memory-hook marker."""
    _ensure_dirs()
    with _APPEND_LOCK:
        lock_path = _acquire_file_lock(
            _PROJECT_ROOT / "_runtime" / "memory" / "locks" / f"{file_path.name}.lock"
        )
        try:
            existing = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
            marker = "<!-- memory hook"
            if marker in existing:
                before, _, after = existing.partition(marker)
                updated = f"{before.rstrip()}\n\n{content}\n\n{marker}{after}"
            else:
                prefix = f"{existing.rstrip()}\n\n" if existing.strip() else ""
                updated = f"{prefix}{content}\n"
            _atomic_write(file_path, updated)
        finally:
            lock_path.unlink(missing_ok=True)


def _sanitize(text: str) -> str:
    """Remove control characters and cap one field at 2000 characters."""
    if not isinstance(text, str):
        raise TypeError("Memory fields must be strings")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text).strip()
    if len(cleaned) > 2_000:
        cleaned = cleaned[:1_997] + "..."
    return cleaned


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _acquire_file_lock(lock_path: Path, timeout: float = 5.0) -> Path:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            return lock_path
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the ADR counter lock")
            time.sleep(0.02)


def _next_adr_id() -> str:
    """Allocate an ADR ID without trusting ignored runtime state alone."""
    _ensure_dirs()
    with _COUNTER_LOCK:
        lock_path = _acquire_file_lock(_ADR_COUNTER_FILE.with_suffix(".lock"))
        try:
            try:
                current = int(_ADR_COUNTER_FILE.read_text(encoding="utf-8").strip())
            except (FileNotFoundError, ValueError):
                current = 0
            # The counter is ignored runtime state and may be missing after a
            # cleanup or on a new machine.  Reconcile it with durable local
            # decisions so recovery cannot silently reuse an existing ADR ID.
            try:
                existing = DECISIONS_FILE.read_text(encoding="utf-8")
                durable_max = max(
                    (int(value) for value in re.findall(r"^## ADR-(\d+)\s*\|", existing, re.M)),
                    default=0,
                )
            except (FileNotFoundError, OSError, UnicodeError):
                durable_max = 0
            next_id = max(current, durable_max) + 1
            _atomic_write(_ADR_COUNTER_FILE, str(next_id))
        finally:
            lock_path.unlink(missing_ok=True)
    return f"ADR-{next_id:03d}"


def append_lesson(
    title: str,
    trigger: str,
    lesson: str,
    source: str,
    action: Optional[str] = None,
) -> str:
    """Append one dated lesson and return the exact Markdown entry."""
    values = [_sanitize(value) for value in (title, trigger, lesson, source)]
    title, trigger, lesson, source = values
    action = _sanitize(action) if action else None
    entry = (
        f"### {_today()} | {title}\n"
        f"- **Trigger**: {trigger}\n"
        f"- **Lesson**: {lesson}\n"
        f"- **Source**: {source}"
    )
    if action:
        entry += f"\n- **Action**: {action}"
    _atomic_append(LESSONS_FILE, entry)
    return entry


def append_decision(
    title: str,
    background: str,
    decision: str,
    alternatives: str,
    rationale: str,
    impact: str,
    status: str = "accepted",
    adr_id: Optional[str] = None,
) -> str:
    """Append one architecture decision and return the Markdown entry."""
    identifier = _sanitize(adr_id) if adr_id else _next_adr_id()
    fields = [
        _sanitize(value)
        for value in (title, background, decision, alternatives, rationale, impact, status)
    ]
    title, background, decision, alternatives, rationale, impact, status = fields
    entry = (
        f"## {identifier} | {_today()} | {title}\n\n"
        f"- **Background**: {background}\n"
        f"- **Decision**: {decision}\n"
        f"- **Alternatives**: {alternatives}\n"
        f"- **Rationale**: {rationale}\n"
        f"- **Impact**: {impact}\n"
        f"- **Status**: {status}"
    )
    _atomic_append(DECISIONS_FILE, entry)
    return entry


def review_memory(output_path: str | Path | None = None) -> dict:
    """Write a report containing only explicit unchecked tasks/TODO lines."""
    actions: list[dict[str, str | int | None]] = []
    if MEMORY_DIR.is_dir():
        for source in sorted(MEMORY_DIR.glob("*.md")):
            if source.name in {"MEMORY.example.md", "README-INIT.md"}:
                continue
            try:
                lines = source.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                match = _OPEN_CHECKBOX.match(line) or _TODO_LINE.match(line)
                if not match:
                    continue
                task = _sanitize(match.group(1))
                if not task:
                    continue
                due = _DATE.search(task)
                actions.append(
                    {
                        "source": source.name,
                        "line": line_number,
                        "text": task,
                        "due_date": due.group(1) if due else None,
                    }
                )

    target = Path(output_path).expanduser().resolve() if output_path else REPORTS_DIR / (
        f"memory-review-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    )
    if target.suffix.lower() != ".md":
        raise ValueError("Memory review output must use the .md extension")
    if _is_relative_to(target, MEMORY_DIR.resolve()):
        raise ValueError("Memory review output must not overwrite private Memory sources")
    allowed_roots = (
        (_PROJECT_ROOT / "_runtime").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    )
    if not any(_is_relative_to(target, root) for root in allowed_roots):
        raise ValueError(
            "Memory review output must be inside _runtime or the OS temporary directory"
        )
    rows = [
        "# Memory Review",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Open actions: {len(actions)}",
        "",
    ]
    for action in actions:
        due = f"; due {action['due_date']}" if action["due_date"] else ""
        rows.append(
            f"- [ ] {action['text']} (`{action['source']}:{action['line']}`{due})"
        )
    if not actions:
        rows.append("No explicit open actions found.")
    _atomic_write(target, "\n".join(rows) + "\n")
    return {
        "success": True,
        "action_count": len(actions),
        "actions": actions,
        "report_path": str(target),
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def check_memory_health() -> dict:
    """Return structural health without returning private memory contents."""
    expected = [
        "MEMORY.md",
        "user-profile.md",
        "user-tech-stack.md",
        "user-personality.md",
        "user-career.md",
        "user-real-life.md",
        "user-reply-preferences.md",
        "lessons.md",
        "decisions.md",
    ]
    missing = [name for name in expected if not (MEMORY_DIR / name).exists()]

    def dates_in(path: Path, pattern: str) -> list[str]:
        if not path.exists():
            return []
        try:
            return re.findall(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
        except (OSError, UnicodeError):
            return []

    lesson_dates = dates_in(LESSONS_FILE, r"^### (\d{4}-\d{2}-\d{2}) \|")
    decision_dates = dates_in(
        DECISIONS_FILE, r"^## ADR-\d{3} \| (\d{4}-\d{2}-\d{2}) \|"
    )
    return {
        "files_present": not missing,
        "missing_files": missing,
        "total_lessons": len(lesson_dates),
        "total_decisions": len(decision_dates),
        "last_lesson_date": max(lesson_dates) if lesson_dates else None,
        "last_decision_date": max(decision_dates) if decision_dates else None,
    }


def main(argv: list[str] | None = None) -> int:
    """Expose non-destructive health/review operations for operators."""
    import argparse

    parser = argparse.ArgumentParser(description="Local memory maintenance")
    parser.add_argument("operation", choices=("health", "review"))
    parser.add_argument("--output", help="Review report path")
    args = parser.parse_args(argv)
    result = (
        check_memory_health()
        if args.operation == "health"
        else review_memory(args.output)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
