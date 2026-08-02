"""Tests for modules.prompt.experiments (v1.5 P2)"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import experiments


@pytest.fixture
def temp_experiments_path(tmp_path, monkeypatch):
    """Redirect EXPERIMENTS_PATH to a temp dir for testing."""
    monkeypatch.setattr(experiments, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(experiments, "EXPERIMENTS_PATH", tmp_path / "experiments.jsonl")
    return tmp_path


# ---------- log_experiment ----------

def test_log_experiment_creates_file(temp_experiments_path):
    exp_id = experiments.log_experiment(
        task_type="coding",
        profile="default",
        task_description="test task",
    )
    assert experiments.EXPERIMENTS_PATH.exists()
    assert exp_id.startswith("exp-")


def test_log_experiment_returns_id(temp_experiments_path):
    exp_id = experiments.log_experiment(
        task_type="coding",
        profile="default",
        task_description="test",
    )
    assert exp_id.startswith("exp-")
    # B4 fix: ID format is now exp-{timestamp}-{4-hex} (uuid suffix for uniqueness)
    # Old format was exp-{timestamp} which collided for same-second logs
    suffix = exp_id[4:]
    parts = suffix.split("-")
    assert len(parts) == 2
    assert parts[0].isdigit()  # timestamp
    assert len(parts[1]) == 4  # 4-char hex
    int(parts[1], 16)  # valid hex


def test_log_experiment_appends(temp_experiments_path):
    experiments.log_experiment(task_type="coding", profile="default", task_description="t1")
    experiments.log_experiment(task_type="review", profile="terse", task_description="t2")
    entries = experiments.read_experiments()
    assert len(entries) == 2
    assert entries[0]["task_type"] == "coding"
    assert entries[1]["task_type"] == "review"


def test_log_experiment_with_all_fields(temp_experiments_path):
    exp_id = experiments.log_experiment(
        task_type="debugging",
        profile="detailed",
        task_description="fix crash on startup",
        contexts=["python"],
        examples=["python/bug-fix-pattern"],
        review_score=8.5,
        review_findings={"critical": 0, "major": 1, "minor": 3},
        duration_seconds=180,
        tokens_used=12500,
        user_satisfied=True,
        user_feedback="good work",
    )
    entries = experiments.read_experiments()
    assert len(entries) == 1
    e = entries[0]
    assert e["experiment_id"] == exp_id
    assert e["task_type"] == "debugging"
    assert e["profile"] == "detailed"
    assert e["task_description"] == "fix crash on startup"
    assert e["contexts"] == ["python"]
    assert e["examples"] == ["python/bug-fix-pattern"]
    assert e["outcome"]["review_score"] == 8.5
    assert e["outcome"]["review_findings_critical"] == 0
    assert e["outcome"]["review_findings_major"] == 1
    assert e["outcome"]["review_findings_minor"] == 3
    assert e["outcome"]["duration_seconds"] == 180
    assert e["outcome"]["tokens_used"] == 12500
    assert e["outcome"]["user_satisfied"] is True
    assert e["user_feedback"] == "good work"


def test_log_experiment_defaults(temp_experiments_path):
    """Minimal call — defaults should populate correctly."""
    experiments.log_experiment(
        task_type="coding",
        profile="default",
        task_description="minimal",
    )
    e = experiments.read_experiments()[0]
    assert e["contexts"] == []
    assert e["examples"] == []
    assert e["outcome"]["review_score"] is None
    assert e["outcome"]["review_findings_critical"] == 0
    assert e["outcome"]["review_findings_major"] == 0
    assert e["outcome"]["review_findings_minor"] == 0
    assert e["outcome"]["duration_seconds"] is None
    assert e["outcome"]["tokens_used"] is None
    assert e["outcome"]["user_satisfied"] is None
    # PII redaction: None feedback is normalized to "" (empty string) by _redact_pii_in_text
    assert e["user_feedback"] == ""


def test_log_experiment_includes_timestamp_and_version(temp_experiments_path):
    experiments.log_experiment(
        task_type="coding",
        profile="default",
        task_description="t",
    )
    e = experiments.read_experiments()[0]
    assert "timestamp" in e
    assert e["prompt_version"] == "1.5.0"


# ---------- read_experiments ----------

def test_read_experiments_empty(temp_experiments_path):
    assert experiments.read_experiments() == []


def test_read_experiments_limit(temp_experiments_path):
    for i in range(5):
        experiments.log_experiment(
            task_type="coding",
            profile="default",
            task_description=f"task {i}",
        )
    recent = experiments.read_experiments(limit=3)
    assert len(recent) == 3
    # Most recent 3 (indices 2, 3, 4)
    assert recent[-1]["task_description"] == "task 4"


def test_read_experiments_skips_invalid_json(temp_experiments_path):
    experiments.EXPERIMENTS_PATH.write_text(
        '{"valid": true}\ninvalid json\n{"also_valid": true}\n',
        encoding="utf-8",
    )
    entries = experiments.read_experiments()
    assert len(entries) == 2


def test_read_experiments_skips_empty_lines(temp_experiments_path):
    experiments.EXPERIMENTS_PATH.write_text(
        '{"valid": true}\n\n\n',
        encoding="utf-8",
    )
    entries = experiments.read_experiments()
    assert len(entries) == 1


def test_read_experiments_limit_zero_returns_empty(temp_experiments_path):
    """Regression: read_experiments(limit=0) must return empty list, not all entries.
    Previously, `entries[-0:]` == `entries[0:]` == all entries (BUG)."""
    for i in range(5):
        experiments.log_experiment(
            task_type="coding", profile="default", task_description=f"t{i}",
        )
    assert experiments.read_experiments(limit=0) == []


def test_read_experiments_limit_negative_returns_empty(temp_experiments_path):
    """limit < 0 should also return empty list (defensive)."""
    for i in range(3):
        experiments.log_experiment(
            task_type="coding", profile="default", task_description=f"t{i}",
        )
    assert experiments.read_experiments(limit=-1) == []


# ---------- compute_stats ----------

def test_compute_stats_empty(temp_experiments_path):
    stats = experiments.compute_stats()
    assert stats["total"] == 0
    assert stats["with_score"] == 0
    assert stats["overall_avg_score"] == 0.0
    assert stats["by_task_type"] == {}
    assert stats["by_profile"] == {}
    assert stats["satisfaction_rate"] == 0.0
    assert stats["failure_count"] == 0


def test_compute_stats_with_scores(temp_experiments_path):
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        review_score=8.0,
    )
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t2",
        review_score=6.0,  # failure (score < 7)
    )
    experiments.log_experiment(
        task_type="review", profile="terse", task_description="t3",
        review_score=9.0,
    )
    stats = experiments.compute_stats()
    assert stats["total"] == 3
    assert stats["with_score"] == 3
    assert stats["overall_avg_score"] == round((8.0 + 6.0 + 9.0) / 3, 2)
    assert stats["failure_count"] == 1
    assert "coding" in stats["by_task_type"]
    assert "review" in stats["by_task_type"]
    assert stats["by_task_type"]["coding"]["count"] == 2
    assert stats["by_task_type"]["coding"]["avg_score"] == 7.0
    assert stats["by_task_type"]["coding"]["min_score"] == 6.0
    assert stats["by_task_type"]["coding"]["max_score"] == 8.0
    assert stats["by_profile"]["default"]["count"] == 2
    assert stats["by_profile"]["terse"]["count"] == 1


def test_compute_stats_without_score(temp_experiments_path):
    """Experiments without score don't affect averages."""
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="no score",
        # review_score=None (default)
    )
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="with score",
        review_score=9.0,
    )
    stats = experiments.compute_stats()
    assert stats["total"] == 2
    assert stats["with_score"] == 1
    assert stats["overall_avg_score"] == 9.0  # only 1 entry with score


def test_compute_stats_satisfaction_rate(temp_experiments_path):
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        user_satisfied=True,
    )
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t2",
        user_satisfied=True,
    )
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t3",
        user_satisfied=False,
    )
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t4",
        # user_satisfied=None
    )
    stats = experiments.compute_stats()
    # 2 satisfied out of 3 with explicit feedback
    assert stats["satisfaction_rate"] == round(2 / 3, 2)


# ---------- render_stats_markdown ----------

def test_render_stats_markdown_empty(temp_experiments_path):
    md = experiments.render_stats_markdown()
    assert "No experiments recorded" in md


def test_render_stats_markdown_with_data(temp_experiments_path):
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        review_score=8.0,
    )
    experiments.log_experiment(
        task_type="review", profile="terse", task_description="t2",
        review_score=9.0,
    )
    md = experiments.render_stats_markdown()
    assert "# Prompt Experiment Stats" in md
    assert "Total experiments" in md
    assert "coding" in md
    assert "review" in md
    assert "default" in md
    assert "terse" in md
    assert "| Task |" in md  # table header
    assert "| Profile |" in md  # table header


def test_render_stats_markdown_includes_failure_count(temp_experiments_path):
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        review_score=5.0,  # failure
    )
    md = experiments.render_stats_markdown()
    assert "Failure count" in md


# ---------- render_recent_markdown ----------

def test_render_recent_markdown_empty(temp_experiments_path):
    md = experiments.render_recent_markdown()
    assert "No experiments recorded" in md


def test_render_recent_markdown_with_data(temp_experiments_path):
    for i in range(5):
        experiments.log_experiment(
            task_type="coding", profile="default",
            task_description=f"task {i}",
            review_score=8.0,
        )
    md = experiments.render_recent_markdown(limit=3)
    assert "Recent 3 Experiments" in md
    assert "task 2" in md
    assert "task 4" in md
    assert "task 0" not in md  # not in last 3


def test_render_recent_markdown_table_format(temp_experiments_path):
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        review_score=8.0, user_satisfied=True,
    )
    md = experiments.render_recent_markdown()
    assert "| Time | Task | Profile | Score |" in md
    assert "8.0/10" in md
    assert "yes" in md


# ---------- main (CLI) ----------

def test_main_stats_empty(temp_experiments_path, capsys):
    rc = experiments.main(["--stats"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "No experiments recorded" in captured.out


def test_main_stats_with_data(temp_experiments_path, capsys):
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        review_score=8.0,
    )
    rc = experiments.main(["--stats"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Total experiments" in captured.out


def test_main_log(temp_experiments_path, capsys):
    rc = experiments.main([
        "--log",
        "--task", "coding",
        "--profile", "default",
        "--description", "test task",
        "--score", "8.5",
        "--findings-critical", "0",
        "--findings-major", "1",
        "--findings-minor", "3",
        "--satisfied", "yes",
        "--feedback", "good",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[OK] Logged experiment:" in captured.out
    entries = experiments.read_experiments()
    assert len(entries) == 1
    assert entries[0]["task_type"] == "coding"
    assert entries[0]["outcome"]["review_score"] == 8.5
    assert entries[0]["outcome"]["user_satisfied"] is True
    assert entries[0]["user_feedback"] == "good"


def test_main_recent_default(temp_experiments_path, capsys):
    for i in range(15):
        experiments.log_experiment(
            task_type="coding", profile="default", task_description=f"t{i}",
        )
    rc = experiments.main([])  # default: --recent 10
    captured = capsys.readouterr()
    assert rc == 0
    assert "Recent 10 Experiments" in captured.out


def test_main_recent_n(temp_experiments_path, capsys):
    for i in range(5):
        experiments.log_experiment(
            task_type="coding", profile="default", task_description=f"t{i}",
        )
    rc = experiments.main(["--recent", "3"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "Recent 3 Experiments" in captured.out


def test_main_log_takes_precedence_over_recent(temp_experiments_path, capsys):
    """Regression: --log must take precedence over --recent (P2 audit fix).
    Previously, `--log --recent 5` would show recent instead of logging."""
    rc = experiments.main([
        "--log",
        "--recent", "5",
        "--task", "coding",
        "--profile", "default",
        "--description", "regression test",
        "--score", "9.0",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[OK] Logged experiment:" in captured.out
    # Verify it actually logged (not showed recent)
    entries = experiments.read_experiments()
    assert len(entries) == 1
    assert entries[0]["task_description"] == "regression test"


def test_main_log_takes_precedence_over_stats(temp_experiments_path, capsys):
    """--log must take precedence over default stats behavior."""
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="prior",
    )
    rc = experiments.main([
        "--log",
        "--task", "review",
        "--profile", "terse",
        "--description", "new exp",
        "--score", "8.0",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[OK] Logged experiment:" in captured.out
    entries = experiments.read_experiments()
    assert len(entries) == 2  # prior + new
    assert entries[-1]["task_type"] == "review"


# ---------- integration with composer version snapshot ----------

def test_experiment_includes_prompt_version(temp_experiments_path):
    """All experiments must record the prompt version for A/B comparison."""
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t",
    )
    e = experiments.read_experiments()[0]
    assert e["prompt_version"] == "1.5.0"


def test_score_threshold_for_failure(temp_experiments_path):
    """Score exactly 7 is NOT a failure (failure is score < 7)."""
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t1",
        review_score=7.0,
    )
    experiments.log_experiment(
        task_type="coding", profile="default", task_description="t2",
        review_score=6.99,
    )
    stats = experiments.compute_stats()
    assert stats["failure_count"] == 1  # only the 6.99 one
