"""
Memory Hook — append lessons/decisions to _data/memory/ automatically.

Triggers (called by other modules):
    1. Review FAIL        → append_lesson()
    2. Bug fix            → append_lesson()
    3. Architecture decision → append_decision()

Design:
    - stdlib only (complies with ONBOARDING §1.5)
    - Append-only, never overwrite (per memory.md context rules)
    - Atomic write (tmp + os.replace)
    - Date-stamped entries
    - No PII logging to stdout

Usage:
    from modules.memory.hook import append_lesson, append_decision

    append_lesson(
        title="Phase 0 B1 PII bypass",
        trigger="Bug fix",
        lesson="_redact_pii_impl must validate tuple unpacking before .get()",
        source="modules/search/orchestrator.py:240",
    )

    append_decision(
        title="v1.8 clone strategy",
        background="...",
        decision="...",
        alternatives="...",
        rationale="...",
        impact="...",
        status="accepted",
    )
"""
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MEMORY_DIR = _PROJECT_ROOT / "_data" / "memory"
LESSONS_FILE = MEMORY_DIR / "lessons.md"
DECISIONS_FILE = MEMORY_DIR / "decisions.md"

# ── Adr counter ────────────────────────────────────────────────
_ADR_COUNTER_FILE = _PROJECT_ROOT / "_runtime" / "memory" / "adr_counter.txt"


def _ensure_dirs() -> None:
    """Ensure memory and runtime directories exist."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    (_PROJECT_ROOT / "_runtime" / "memory").mkdir(parents=True, exist_ok=True)


def _atomic_append(file_path: Path, content: str) -> None:
    """Append content to file atomically (tmp + os.replace).

    For append operations, we read existing content, write to tmp,
    then atomically replace. This avoids partial writes on crash.
    """
    _ensure_dirs()
    existing = ""
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")

    # Append new content before the trailing comment marker if present
    marker = "<!-- memory hook"
    if marker in existing:
        before, _, after = existing.partition(marker)
        new_content = before.rstrip() + "\n\n" + content + "\n\n" + marker + after
    else:
        new_content = existing.rstrip() + "\n\n" + content + "\n"

    # Atomic write via tmp + os.replace
    fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent), suffix=".tmp", prefix=file_path.stem + "_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, str(file_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _sanitize(text: str) -> str:
    """Sanitize input to prevent markdown injection and PII leakage."""
    # Strip control chars except newlines
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Limit length to prevent abuse
    if len(text) > 2000:
        text = text[:1997] + "..."
    return text.strip()


def _today() -> str:
    """Return today's date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


def _next_adr_id() -> str:
    """Get next ADR ID, persisted to counter file."""
    _ensure_dirs()
    current = 0
    if _ADR_COUNTER_FILE.exists():
        try:
            current = int(_ADR_COUNTER_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            current = 0
    next_id = current + 1
    _ADR_COUNTER_FILE.write_text(str(next_id), encoding="utf-8")
    return f"ADR-{next_id:03d}"


def append_lesson(
    title: str,
    trigger: str,
    lesson: str,
    source: str,
    action: Optional[str] = None,
) -> str:
    """Append a lesson to lessons.md.

    Args:
        title: Short title (1 line)
        trigger: What caused this — "Review FAIL" / "Bug fix" / "Architecture decision"
        lesson: One-line summary of the lesson
        source: File path or PR link that triggered this
        action: Optional — how to avoid in future

    Returns:
        The formatted entry string that was appended.
    """
    title = _sanitize(title)
    trigger = _sanitize(trigger)
    lesson = _sanitize(lesson)
    source = _sanitize(source)
    action = _sanitize(action) if action else None

    entry = f"""### {_today()} | {title}
- **触发**：{trigger}
- **教训**：{lesson}
- **来源**：{source}"""
    if action:
        entry += f"\n- **行动**：{action}"

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
    """Append an architectural decision to decisions.md.

    Args:
        title: Decision title
        background: Why this decision is needed
        decision: What was chosen
        alternatives: What was considered and rejected
        rationale: Why the current option was chosen
        impact: Which modules/versions are affected
        status: proposed / accepted / superseded by ADR-XXX
        adr_id: Optional explicit ADR ID; auto-generated if None

    Returns:
        The formatted entry string that was appended.
    """
    if adr_id is None:
        adr_id = _next_adr_id()

    title = _sanitize(title)
    background = _sanitize(background)
    decision = _sanitize(decision)
    alternatives = _sanitize(alternatives)
    rationale = _sanitize(rationale)
    impact = _sanitize(impact)
    status = _sanitize(status)

    entry = f"""## {adr_id} | {_today()} | {title}

- **背景**：{background}
- **决策**：{decision}
- **备选**：{alternatives}
- **理由**：{rationale}
- **影响**：{impact}
- **状态**：{status}"""

    _atomic_append(DECISIONS_FILE, entry)
    return entry


def check_memory_health() -> dict:
    """Check memory file health. Called by /doctor command.

    Returns dict with:
        - files_present: bool (all 9 expected files exist)
        - missing_files: list[str]
        - total_lessons: int
        - total_decisions: int
        - last_lesson_date: str | None
        - last_decision_date: str | None
    """
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
    missing = [f for f in expected if not (MEMORY_DIR / f).exists()]

    # Count lessons and decisions
    total_lessons = 0
    last_lesson_date = None
    if LESSONS_FILE.exists():
        content = LESSONS_FILE.read_text(encoding="utf-8")
        # Match "### YYYY-MM-DD | ..."
        dates = re.findall(r"^### (\d{4}-\d{2}-\d{2}) \|", content, re.MULTILINE)
        total_lessons = len(dates)
        if dates:
            last_lesson_date = max(dates)

    total_decisions = 0
    last_decision_date = None
    if DECISIONS_FILE.exists():
        content = DECISIONS_FILE.read_text(encoding="utf-8")
        # Match "## ADR-XXX | YYYY-MM-DD | ..."
        dates = re.findall(r"^## ADR-\d{3} \| (\d{4}-\d{2}-\d{2}) \|", content, re.MULTILINE)
        total_decisions = len(dates)
        if dates:
            last_decision_date = max(dates)

    return {
        "files_present": len(missing) == 0,
        "missing_files": missing,
        "total_lessons": total_lessons,
        "total_decisions": total_decisions,
        "last_lesson_date": last_lesson_date,
        "last_decision_date": last_decision_date,
    }
