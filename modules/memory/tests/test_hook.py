"""Behavioral coverage for the recovered local memory helper."""

from pathlib import Path

import modules.memory.hook as hook


def _redirect_storage(monkeypatch, tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    monkeypatch.setattr(hook, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(hook, "LESSONS_FILE", memory_dir / "lessons.md")
    monkeypatch.setattr(hook, "DECISIONS_FILE", memory_dir / "decisions.md")
    monkeypatch.setattr(
        hook,
        "_ADR_COUNTER_FILE",
        tmp_path / "_runtime" / "memory" / "adr_counter.txt",
    )
    monkeypatch.setattr(hook, "_PROJECT_ROOT", tmp_path)


def test_append_lesson_preserves_marker(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)
    hook.MEMORY_DIR.mkdir(parents=True)
    hook.LESSONS_FILE.write_text(
        "# Lessons\n\n<!-- memory hook marker -->\n", encoding="utf-8"
    )

    entry = hook.append_lesson("Title", "Bug fix", "Lesson", "source.py")

    content = hook.LESSONS_FILE.read_text(encoding="utf-8")
    assert entry in content
    assert content.rstrip().endswith("<!-- memory hook marker -->")


def test_append_decision_allocates_sequential_ids(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)
    first = hook.append_decision("One", "B", "D", "A", "R", "I")
    second = hook.append_decision("Two", "B", "D", "A", "R", "I")

    assert "ADR-001" in first
    assert "ADR-002" in second
    assert hook._ADR_COUNTER_FILE.read_text(encoding="utf-8") == "2"


def test_sanitize_removes_control_characters_and_bounds_length():
    cleaned = hook._sanitize("a\x00b" + "x" * 2500)
    assert "\x00" not in cleaned
    assert len(cleaned) == 2000
    assert cleaned.endswith("...")


def test_memory_health_reports_missing_files(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)
    hook.MEMORY_DIR.mkdir(parents=True)
    hook.LESSONS_FILE.write_text("### 2026-01-01 | One\n", encoding="utf-8")
    hook.DECISIONS_FILE.write_text(
        "## ADR-001 | 2026-01-02 | One\n", encoding="utf-8"
    )

    result = hook.check_memory_health()

    assert result["files_present"] is False
    assert result["total_lessons"] == 1
    assert result["total_decisions"] == 1
    assert result["last_lesson_date"] == "2026-01-01"
    assert result["last_decision_date"] == "2026-01-02"
