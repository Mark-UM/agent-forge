"""Behavioral coverage for the recovered local memory helper."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

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
    monkeypatch.setattr(hook, "REPORTS_DIR", tmp_path / "_runtime" / "reports")


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


def test_append_decision_reconciles_missing_counter_with_existing_decisions(
    monkeypatch, tmp_path
):
    _redirect_storage(monkeypatch, tmp_path)
    hook.MEMORY_DIR.mkdir(parents=True)
    hook.DECISIONS_FILE.write_text(
        "## ADR-007 | 2026-07-01 | Existing\n", encoding="utf-8"
    )

    entry = hook.append_decision("Next", "B", "D", "A", "R", "I")

    assert "ADR-008" in entry
    assert hook._ADR_COUNTER_FILE.read_text(encoding="utf-8") == "8"


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


def test_review_memory_extracts_only_open_actions(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)
    hook.MEMORY_DIR.mkdir(parents=True)
    source = hook.MEMORY_DIR / "user-career.md"
    source.write_text(
        "# Career\n\n- [ ] Submit application by 2026-08-10\n"
        "- [x] Completed item\nTODO: prepare interview notes\n",
        encoding="utf-8",
    )

    result = hook.review_memory()

    assert result["success"] is True
    assert result["action_count"] == 2
    assert result["actions"][0]["source"] == "user-career.md"
    assert Path(result["report_path"]).is_file()
    assert "Completed item" not in Path(result["report_path"]).read_text(encoding="utf-8")


def test_review_memory_does_not_modify_private_sources(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)
    hook.MEMORY_DIR.mkdir(parents=True)
    source = hook.MEMORY_DIR / "lessons.md"
    original = "# Lessons\n\n- [ ] Follow up\n"
    source.write_text(original, encoding="utf-8")

    hook.review_memory()

    assert source.read_text(encoding="utf-8") == original


def test_review_memory_rejects_output_over_private_source(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)
    hook.MEMORY_DIR.mkdir(parents=True)

    with pytest.raises(ValueError, match="must not overwrite"):
        hook.review_memory(hook.MEMORY_DIR / "lessons.md")


def test_concurrent_lesson_appends_do_not_lose_updates(monkeypatch, tmp_path):
    _redirect_storage(monkeypatch, tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: hook.append_lesson(
                    f"Title {index}", "test", f"Lesson {index}", "test_hook.py"
                ),
                range(20),
            )
        )

    content = hook.LESSONS_FILE.read_text(encoding="utf-8")
    assert content.count("### ") == 20
