"""
Prompt Experiments — A/B testing & effectiveness quantification (v1.5 P2)

Data sources:
- _runtime/prompt/experiments.jsonl  (experiment records)
- _runtime/prompt/composition_log.jsonl  (composition records)

Integration:
- /review output's Score is written to experiments via log_experiment()
- /prompt stats reads experiments.jsonl and computes statistics

CLI:
    python -m modules.prompt.experiments --log --task coding --profile default \\
        --score 8.5 --findings-critical 0 --findings-major 1 --findings-minor 3 \\
        --description "Implement fibonacci function"
    python -m modules.prompt.experiments --stats
    python -m modules.prompt.experiments --recent 10
"""
import argparse
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

RUNTIME_DIR = Path(__file__).resolve().parent.parent.parent / "_runtime" / "prompt"
EXPERIMENTS_PATH = RUNTIME_DIR / "experiments.jsonl"
VERSION = "1.5.0"


def _redact_pii_in_text(text: Optional[str]) -> tuple:
    """对 experiments 记录中的自由文本做 PII 脱敏。

    复用 modules/search/privacy.py 的 7-pattern 规则（如果可用）。
    不可用时退化为最小化内联规则（email + phone）。

    Returns:
        tuple: (redacted_text, patterns_matched_list)
    """
    if not text or not isinstance(text, str):
        return text or "", []

    # 优先复用 search 模块的 privacy.redact_outbound
    try:
        import os
        _search_dir = str(Path(__file__).resolve().parent.parent / "search")
        if _search_dir not in sys.path:
            sys.path.insert(0, _search_dir)
        from privacy import redact_outbound  # type: ignore
        redacted, metadata = redact_outbound(text)
        return redacted, metadata.get("patterns_matched", [])
    except Exception:
        pass

    # 退化路径：内联最小规则（email + 中国手机号）
    import re
    patterns_matched = []
    redacted = text
    email_re = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
    if email_re.search(redacted):
        redacted = email_re.sub('[REDACTED-EMAIL]', redacted)
        patterns_matched.append('email')
    phone_re = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
    if phone_re.search(redacted):
        redacted = phone_re.sub('[REDACTED-PHONE]', redacted)
        patterns_matched.append('phone_cn')
    return redacted, patterns_matched


def log_experiment(
    task_type: str,
    profile: str,
    task_description: str,
    contexts: Optional[list] = None,
    examples: Optional[list] = None,
    review_score: Optional[float] = None,
    review_findings: Optional[dict] = None,
    duration_seconds: Optional[int] = None,
    tokens_used: Optional[int] = None,
    user_satisfied: Optional[bool] = None,
    user_feedback: Optional[str] = None,
) -> str:
    """
    Append an experiment record to experiments.jsonl.

    Args:
        task_type: coding | review | research | debugging | planning | writing | automation
        profile: default | terse | detailed | socratic
        task_description: short description (PII redaction applied — see _redact_pii_in_text)
        contexts: list of context names loaded (e.g., ["python", "secrets"])
        examples: list of example names loaded (e.g., ["python/test-driven"])
        review_score: 0.0-10.0 from /review pipeline
        review_findings: {"critical": int, "major": int, "minor": int}
        duration_seconds: wall-clock time for the task
        tokens_used: total tokens consumed (prompt + completion)
        user_satisfied: True/False/None (None = not collected)
        user_feedback: free-text feedback (optional)

    Returns:
        experiment_id (e.g., "exp-1700000000-3f2a")
    """
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    # B4 fix: 用 uuid4 后缀消除 second-granularity collision
    # （原实现同一秒内多次 log 共享同一 experiment_id，导致 stats 去重错误）
    timestamp_int = int(datetime.now().timestamp())
    uuid_suffix = uuid.uuid4().hex[:4]
    experiment_id = f"exp-{timestamp_int}-{uuid_suffix}"

    # PII redaction on free-text fields
    redacted_description, desc_pii_patterns = _redact_pii_in_text(task_description)
    redacted_feedback, fb_pii_patterns = _redact_pii_in_text(user_feedback)

    findings = review_findings or {}

    entry = {
        "experiment_id": experiment_id,
        "timestamp": datetime.now().isoformat(),
        "prompt_version": VERSION,
        "task_type": task_type,
        "profile": profile,
        "contexts": contexts or [],
        "examples": examples or [],
        "task_description": redacted_description,
        "pii_redaction": {
            "description_patterns": desc_pii_patterns,
            "feedback_patterns": fb_pii_patterns,
        },
        "outcome": {
            "review_score": review_score,
            "review_findings_critical": findings.get("critical", 0),
            "review_findings_major": findings.get("major", 0),
            "review_findings_minor": findings.get("minor", 0),
            "duration_seconds": duration_seconds,
            "tokens_used": tokens_used,
            "user_satisfied": user_satisfied,
        },
        "user_feedback": redacted_feedback,
    }

    with open(EXPERIMENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return experiment_id


def read_experiments(limit: Optional[int] = None) -> list:
    """
    Read experiment records.

    Args:
        limit: if provided, return only the most recent N records

    Returns:
        list of experiment dicts (chronological order)
    """
    if not EXPERIMENTS_PATH.exists():
        return []

    entries = []
    with open(EXPERIMENTS_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if limit is not None:
        if limit <= 0:
            return []
        return entries[-limit:]
    return entries


def compute_stats() -> dict:
    """
    Compute aggregate statistics from experiments.

    Returns:
        {
            "total": int,
            "with_score": int,
            "overall_avg_score": float,
            "by_task_type": {task: {count, avg_score, min_score, max_score}},
            "by_profile": {profile: {count, avg_score}},
            "satisfaction_rate": float,  # 0.0-1.0
            "failure_count": int,  # score < 7
        }
    """
    entries = read_experiments()

    if not entries:
        return {
            "total": 0,
            "with_score": 0,
            "overall_avg_score": 0.0,
            "by_task_type": {},
            "by_profile": {},
            "satisfaction_rate": 0.0,
            "failure_count": 0,
        }

    # Group by task_type and profile
    by_task = defaultdict(list)
    by_profile = defaultdict(list)
    all_scores = []
    satisfied_count = 0
    satisfied_total = 0
    failure_count = 0

    for e in entries:
        outcome = e.get("outcome", {})
        score = outcome.get("review_score")
        task = e.get("task_type", "unknown")
        profile = e.get("profile", "unknown")

        if score is not None:
            by_task[task].append(score)
            by_profile[profile].append(score)
            all_scores.append(score)
            if score < 7:
                failure_count += 1

        satisfied = outcome.get("user_satisfied")
        if satisfied is not None:
            satisfied_total += 1
            if satisfied:
                satisfied_count += 1

    stats = {
        "total": len(entries),
        "with_score": len(all_scores),
        "overall_avg_score": round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0,
        "by_task_type": {},
        "by_profile": {},
        "satisfaction_rate": round(satisfied_count / satisfied_total, 2) if satisfied_total > 0 else 0.0,
        "failure_count": failure_count,
    }

    for task, scores in by_task.items():
        stats["by_task_type"][task] = {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 2),
            "min_score": min(scores),
            "max_score": max(scores),
        }

    for profile, scores in by_profile.items():
        stats["by_profile"][profile] = {
            "count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 2),
        }

    return stats


def render_stats_markdown() -> str:
    """Render statistics as Markdown (for /prompt stats command)."""
    stats = compute_stats()

    if stats["total"] == 0:
        return "No experiments recorded yet. Run `/review` to start collecting data."

    lines = [
        "# Prompt Experiment Stats (v1.5 P2)",
        "",
        f"**Total experiments**: {stats['total']}",
        f"**Experiments with score**: {stats['with_score']}",
        f"**Overall average score**: {stats['overall_avg_score']}/10",
        f"**Satisfaction rate**: {stats['satisfaction_rate'] * 100:.0f}%",
        f"**Failure count (score < 7)**: {stats['failure_count']}",
        "",
        "## By Task Type",
        "",
        "| Task | Count | Avg Score | Min | Max |",
        "|------|-------|-----------|-----|-----|",
    ]

    for task in sorted(stats["by_task_type"].keys()):
        s = stats["by_task_type"][task]
        lines.append(
            f"| {task} | {s['count']} | {s['avg_score']} | {s['min_score']} | {s['max_score']} |"
        )

    lines.extend([
        "",
        "## By Profile",
        "",
        "| Profile | Count | Avg Score |",
        "|---------|-------|-----------|",
    ])

    for profile in sorted(stats["by_profile"].keys()):
        s = stats["by_profile"][profile]
        lines.append(f"| {profile} | {s['count']} | {s['avg_score']} |")

    lines.extend([
        "",
        f"_Generated at: {datetime.now().isoformat()}_",
    ])

    return "\n".join(lines)


def render_recent_markdown(limit: int = 10) -> str:
    """Render recent N experiments as Markdown (for /prompt history)."""
    entries = read_experiments(limit=limit)

    if not entries:
        return "No experiments recorded yet."

    lines = [
        f"# Recent {len(entries)} Experiments",
        "",
        "| Time | Task | Profile | Score | Satisfied | Description |",
        "|------|------|---------|-------|-----------|-------------|",
    ]

    for e in entries:
        ts = e.get("timestamp", "")[:19]
        task = e.get("task_type", "?")
        profile = e.get("profile", "?")
        outcome = e.get("outcome", {})
        score = outcome.get("review_score")
        score_str = f"{score}/10" if score is not None else "-"
        satisfied = outcome.get("user_satisfied")
        sat_str = "yes" if satisfied is True else ("no" if satisfied is False else "-")
        desc = (e.get("task_description") or "")[:50]
        lines.append(f"| {ts} | {task} | {profile} | {score_str} | {sat_str} | {desc} |")

    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prompt Experiments (v1.5 P2)")
    parser.add_argument("--log", action="store_true",
                        help="Log a new experiment")
    parser.add_argument("--stats", action="store_true",
                        help="Compute and print statistics")
    parser.add_argument("--recent", type=int, default=None,
                        help="Print recent N experiments (default 10)")
    # --log options
    parser.add_argument("--task", type=str, default="coding",
                        help="Task type")
    parser.add_argument("--profile", type=str, default="default",
                        help="Profile")
    parser.add_argument("--description", type=str, default="",
                        help="Task description")
    parser.add_argument("--context", action="append", default=[],
                        help="Contexts loaded (repeatable)")
    parser.add_argument("--example", action="append", default=[],
                        help="Examples loaded (repeatable)")
    parser.add_argument("--score", type=float, default=None,
                        help="Review score (0.0-10.0)")
    parser.add_argument("--findings-critical", type=int, default=0)
    parser.add_argument("--findings-major", type=int, default=0)
    parser.add_argument("--findings-minor", type=int, default=0)
    parser.add_argument("--duration", type=int, default=None,
                        help="Duration in seconds")
    parser.add_argument("--tokens", type=int, default=None,
                        help="Tokens used")
    parser.add_argument("--satisfied", choices=["yes", "no"], default=None)
    parser.add_argument("--feedback", type=str, default=None)
    args = parser.parse_args(argv)

    if args.stats:
        print(render_stats_markdown())
        return 0

    if args.log:
        satisfied = None
        if args.satisfied == "yes":
            satisfied = True
        elif args.satisfied == "no":
            satisfied = False

        exp_id = log_experiment(
            task_type=args.task,
            profile=args.profile,
            task_description=args.description,
            contexts=args.context or None,
            examples=args.example or None,
            review_score=args.score,
            review_findings={
                "critical": args.findings_critical,
                "major": args.findings_major,
                "minor": args.findings_minor,
            },
            duration_seconds=args.duration,
            tokens_used=args.tokens,
            user_satisfied=satisfied,
            user_feedback=args.feedback,
        )
        print(f"[OK] Logged experiment: {exp_id}")
        return 0

    # Default behavior (no --log, no --stats): show recent 10
    limit = args.recent if args.recent is not None else 10
    print(render_recent_markdown(limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
