"""Dynamic prompt assembly with versioned automatic context loading.

Priority order, highest to lowest:
BASE > TASK > CONTEXT > PROFILE > EXAMPLE > EXTRA.

``--pre-session`` refreshes project context signals, validates their project
fingerprint, merges them with explicit ``--context`` values, and writes the
result to ``AGENTS_COMPOSED.md``. Explicit contexts are ordered first and all
context names are de-duplicated.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / ".opencode" / "prompts"
RUNTIME_DIR = PROJECT_ROOT / "_runtime" / "prompt"
CONTEXT_SIGNALS_PATH = RUNTIME_DIR / "context-signals.json"
AGENTS_COMPOSED = PROJECT_ROOT / "AGENTS_COMPOSED.md"

DEFAULT_PROFILE = "default"
DEFAULT_TASK = None
VERSION = "1.7.0"

PRIORITY_LADDER = [
    ("BASE", "Global invariants (constitutional, always loaded)"),
    ("TASK", "Current task workflow (coding/review/research/...)"),
    ("CONTEXT", "Project / framework / language context"),
    ("PROFILE", "Output profile (terse / detailed / socratic)"),
    ("EXAMPLE", "Few-shot examples"),
    ("EXTRA", "Local user profile (markconfig/profile.md)"),
]


def _render_priority_header() -> str:
    lines = [
        "# Prompt Priority Declaration",
        "",
        "Layers below are listed from HIGHEST to LOWEST priority. "
        "Lower-priority content MUST NOT contradict or override higher-priority "
        "content. When two layers conflict, the higher-priority layer wins.",
        "",
    ]
    for index, (label, description) in enumerate(PRIORITY_LADDER, start=1):
        lines.append(f"{index}. **{label}** — {description}")
    lines.extend(
        [
            "",
            "If you find a contradiction, resolve it in favor of the higher "
            "layer and surface the conflict to the user rather than silently "
            "picking one.",
            "",
        ]
    )
    return "\n".join(lines)


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3 :].lstrip()
    return content


def _parse_simple_yaml(text: str) -> dict:
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            value = [
                item.strip().strip("\"'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
        result[key.strip()] = value
    return result


def load_prompt(path: Path) -> str:
    if not path.is_file():
        return ""
    return _strip_frontmatter(path.read_text(encoding="utf-8"))


def load_with_metadata(path: Path) -> dict:
    if not path.is_file():
        return {"content": "", "metadata": {}}
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {"content": content, "metadata": {}}
    end = content.find("---", 3)
    if end == -1:
        return {"content": content, "metadata": {}}
    return {
        "content": content[end + 3 :].lstrip(),
        "metadata": _parse_simple_yaml(content[3:end].strip()),
    }


def _relpath(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return str(path).replace("\\", "/")
    return str(relative).replace("\\", "/")


def _dedupe(items) -> list[str]:  # noqa: ANN001
    output: list[str] = []
    seen: set[str] = set()
    for raw in items or []:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        output.append(value)
        seen.add(value)
    return output


def load_detected_contexts(
    *,
    refresh: bool = False,
    signals_path: Path = CONTEXT_SIGNALS_PATH,
    project_root: Path = PROJECT_ROOT,
    prompts_dir: Path = PROMPTS_DIR,
) -> tuple[list[str], dict]:
    """Load fresh detected contexts and discard names without prompt files."""

    from modules.prompt.context_state import load_context_state

    payload = load_context_state(
        path=signals_path,
        project_root=project_root,
        refresh=refresh,
    )
    available: list[str] = []
    ignored: list[str] = []
    for context in _dedupe(payload.get("contexts", [])):
        if (prompts_dir / "contexts" / f"{context}.md").is_file():
            available.append(context)
        else:
            ignored.append(context)
    metadata = {
        "schema_version": payload.get("schema_version"),
        "project_fingerprint": payload.get("project_fingerprint"),
        "detected_at": payload.get("detected_at"),
        "ignored_contexts": ignored,
    }
    return available, metadata


def resolve_contexts(
    explicit_contexts: Optional[list[str]],
    detected_contexts: Optional[list[str]],
) -> list[str]:
    """Merge contexts with explicit choices first and no duplicates."""

    return _dedupe([*(explicit_contexts or []), *(detected_contexts or [])])


def compose(
    profile: str = DEFAULT_PROFILE,
    task: Optional[str] = DEFAULT_TASK,
    contexts: Optional[list] = None,
    examples: Optional[list] = None,
    extra_instructions: Optional[str] = None,
    *,
    use_detected_contexts: bool = False,
    refresh_context_signals: bool = False,
    signals_path: Path = CONTEXT_SIGNALS_PATH,
) -> dict:
    """Compose a complete prompt using the declared priority ladder."""

    detected_contexts: list[str] = []
    detection_metadata: dict = {}
    if use_detected_contexts:
        detected_contexts, detection_metadata = load_detected_contexts(
            refresh=refresh_context_signals,
            signals_path=signals_path,
            project_root=PROJECT_ROOT,
            prompts_dir=PROMPTS_DIR,
        )
    resolved_contexts = resolve_contexts(contexts, detected_contexts)
    resolved_examples = _dedupe(examples)

    sources: list[str] = ["(inline) priority declaration"]
    parts: list[str] = [
        "# === PRIORITY DECLARATION ===\n\n" + _render_priority_header()
    ]

    base_path = PROMPTS_DIR / "base.md"
    if base := load_prompt(base_path):
        parts.append("# === BASE (Constitutional Layer) ===\n\n" + base)
        sources.append(_relpath(base_path))

    if task:
        task_path = PROMPTS_DIR / "tasks" / f"{task}.md"
        if task_content := load_prompt(task_path):
            parts.append(f"# === TASK: {task} ===\n\n{task_content}")
            sources.append(_relpath(task_path))

    loaded_contexts: list[str] = []
    for context in resolved_contexts:
        context_path = PROMPTS_DIR / "contexts" / f"{context}.md"
        if context_content := load_prompt(context_path):
            parts.append(f"# === CONTEXT: {context} ===\n\n{context_content}")
            sources.append(_relpath(context_path))
            loaded_contexts.append(context)

    profile_path = PROMPTS_DIR / "profiles" / f"{profile}.md"
    if profile_content := load_prompt(profile_path):
        parts.append(f"# === PROFILE: {profile} ===\n\n{profile_content}")
        sources.append(_relpath(profile_path))

    loaded_examples: list[str] = []
    for example in resolved_examples:
        example_path = PROMPTS_DIR / "examples" / f"{example}.md"
        if example_content := load_prompt(example_path):
            parts.append(f"# === EXAMPLE: {example} ===\n\n{example_content}")
            sources.append(_relpath(example_path))
            loaded_examples.append(example)

    if extra_instructions:
        parts.append("# === EXTRA (User Profile) ===\n\n" + extra_instructions)
        sources.append("markconfig/profile.md")

    composed_at = datetime.now(timezone.utc).isoformat()
    return {
        "prompt": "\n\n---\n\n".join(parts),
        "sources": sources,
        "metadata": {
            "version": VERSION,
            "composed_at": composed_at,
            "profile": profile,
            "task": task,
            "contexts": loaded_contexts,
            "explicit_contexts": _dedupe(contexts),
            "detected_contexts": detected_contexts,
            "context_detection": detection_metadata,
            "examples": loaded_examples,
        },
        "priority_ladder": [
            {"layer": label, "description": description, "priority": index}
            for index, (label, description) in enumerate(PRIORITY_LADDER, start=1)
        ],
    }


def write_composed(result: dict, output_path: Path = AGENTS_COMPOSED) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "<!-- Auto-generated by modules/prompt/composer.py at "
        f"{result['metadata']['composed_at']} -->\n"
        f"<!-- Sources: {', '.join(result['sources'])} -->\n"
        f"<!-- Version: {result['metadata']['version']} -->\n\n"
    )
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(header + result["prompt"], encoding="utf-8")
    temporary.replace(output_path)


def list_tasks() -> list[str]:
    directory = PROMPTS_DIR / "tasks"
    return sorted(path.stem for path in directory.glob("*.md")) if directory.exists() else []


def list_profiles() -> list[str]:
    directory = PROMPTS_DIR / "profiles"
    return sorted(path.stem for path in directory.glob("*.md")) if directory.exists() else []


def list_contexts() -> list[str]:
    directory = PROMPTS_DIR / "contexts"
    return sorted(path.stem for path in directory.glob("*.md")) if directory.exists() else []


def list_examples() -> list[str]:
    directory = PROMPTS_DIR / "examples"
    if not directory.exists():
        return []
    return sorted(
        str(path.relative_to(directory).with_suffix("")).replace("\\", "/")
        for path in directory.rglob("*.md")
    )


def _load_user_profile() -> Optional[str]:
    path = PROJECT_ROOT / "markconfig" / "profile.md"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prompt Composer")
    parser.add_argument("--pre-session", action="store_true")
    parser.add_argument("--task", default=None)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--example", action="append", default=[])
    parser.add_argument("--list-tasks", action="store_true")
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--list-contexts", action="store_true")
    parser.add_argument("--list-examples", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    listing = (
        (args.list_tasks, list_tasks),
        (args.list_profiles, list_profiles),
        (args.list_contexts, list_contexts),
        (args.list_examples, list_examples),
    )
    for enabled, function in listing:
        if enabled:
            for item in function():
                print(item)
            return 0

    result = compose(
        profile=args.profile,
        task=args.task,
        contexts=args.context,
        examples=args.example,
        extra_instructions=_load_user_profile(),
        use_detected_contexts=args.pre_session,
        refresh_context_signals=args.pre_session,
    )

    from .log import log_composition, write_version_snapshot

    if args.pre_session:
        write_composed(result)
        log_composition(result["metadata"])
        write_version_snapshot(result["sources"], args.profile, args.task)
        print(f"[OK] Composed -> {AGENTS_COMPOSED}")
        print(f"[OK] Sources: {', '.join(result['sources'])}")
    elif args.output:
        write_composed(result, Path(args.output))
        log_composition(result["metadata"])
        print(f"[OK] Composed -> {args.output}")
    else:
        print(result["prompt"])
        log_composition(result["metadata"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
