"""
Prompt Composer — dynamic prompt assembly engine (v1.5 P0)

Assembly order (high priority overrides low):
1. base.md             (always loaded, constitutional)
2. profile.md          (terse / detailed / socratic / default)
3. tasks/{type}.md     (task-specific)
4. contexts/{ctx}.md   (tech-stack / directory-specific, multiple allowed)
5. examples/{...}.md   (Few-Shot, P1)

CLI (must use -m form; direct script invocation fails on relative imports):
    python -m modules.prompt.composer --pre-session
    python -m modules.prompt.composer --task coding --profile terse
    python -m modules.prompt.composer --list-tasks
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Project root: this file is at modules/prompt/composer.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = PROJECT_ROOT / ".opencode" / "prompts"
RUNTIME_DIR = PROJECT_ROOT / "_runtime" / "prompt"
AGENTS_COMPOSED = PROJECT_ROOT / "AGENTS_COMPOSED.md"

DEFAULT_PROFILE = "default"
DEFAULT_TASK = None  # At session start, no task loaded; loaded on-demand during session
VERSION = "1.5.0"


def load_prompt(path: Path) -> str:
    """Load a prompt file, strip YAML frontmatter, return body only."""
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    return _strip_frontmatter(content)


def load_with_metadata(path: Path) -> dict:
    """Load a prompt file, return {content, metadata}."""
    if not path.exists():
        return {"content": "", "metadata": {}}
    content = path.read_text(encoding="utf-8")
    metadata = {}
    body = content
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            frontmatter = content[3:end].strip()
            body = content[end + 3:].lstrip()
            metadata = _parse_simple_yaml(frontmatter)
    return {"content": body, "metadata": metadata}


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from content."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].lstrip()
    return content


def _relpath(path: Path) -> str:
    """Return path relative to PROJECT_ROOT with forward slashes (cross-platform)."""
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _parse_simple_yaml(text: str) -> dict:
    """Parse simple 'key: value' YAML (no nesting, no PyYAML dependency)."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        # Strip quotes if present
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        # Parse list values: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            value = [v.strip().strip('"\'') for v in value[1:-1].split(",") if v.strip()]
        result[key] = value
    return result


def compose(
    profile: str = DEFAULT_PROFILE,
    task: Optional[str] = DEFAULT_TASK,
    contexts: Optional[list] = None,
    examples: Optional[list] = None,
    extra_instructions: Optional[str] = None,
) -> dict:
    """
    Compose a complete system prompt.

    Returns:
        {
            "prompt": str,           # composed full prompt
            "sources": list[str],    # loaded file paths (relative to PROJECT_ROOT)
            "metadata": dict,        # version, timestamp, profile, task, contexts, examples
        }
    """
    sources = []
    parts = []

    # 1. Base (always loaded)
    base_path = PROMPTS_DIR / "base.md"
    base = load_prompt(base_path)
    if base:
        parts.append("# === BASE (Constitutional Layer) ===\n\n" + base)
        sources.append(_relpath(base_path))

    # 2. Profile
    profile_path = PROMPTS_DIR / "profiles" / f"{profile}.md"
    profile_content = load_prompt(profile_path)
    if profile_content:
        parts.append(f"# === PROFILE: {profile} ===\n\n" + profile_content)
        sources.append(_relpath(profile_path))

    # 3. Task
    if task:
        task_path = PROMPTS_DIR / "tasks" / f"{task}.md"
        task_content = load_prompt(task_path)
        if task_content:
            parts.append(f"# === TASK: {task} ===\n\n" + task_content)
            sources.append(_relpath(task_path))

    # 4. Contexts (multiple allowed)
    if contexts:
        for ctx in contexts:
            ctx_path = PROMPTS_DIR / "contexts" / f"{ctx}.md"
            ctx_content = load_prompt(ctx_path)
            if ctx_content:
                parts.append(f"# === CONTEXT: {ctx} ===\n\n" + ctx_content)
                sources.append(_relpath(ctx_path))

    # 5. Examples (P1 feature, composer supports it now)
    if examples:
        for ex in examples:
            ex_path = PROMPTS_DIR / "examples" / f"{ex}.md"
            ex_content = load_prompt(ex_path)
            if ex_content:
                parts.append(f"# === EXAMPLE: {ex} ===\n\n" + ex_content)
                sources.append(_relpath(ex_path))

    # 6. Extra instructions (e.g., user profile from markconfig/profile.md)
    if extra_instructions:
        parts.append("# === EXTRA (User Profile) ===\n\n" + extra_instructions)

    prompt = "\n\n---\n\n".join(parts)

    return {
        "prompt": prompt,
        "sources": sources,
        "metadata": {
            "version": VERSION,
            "composed_at": datetime.now().isoformat(),
            "profile": profile,
            "task": task,
            "contexts": contexts or [],
            "examples": examples or [],
        },
    }


def write_composed(result: dict, output_path: Path = AGENTS_COMPOSED) -> None:
    """Write the composed prompt to a file with metadata header."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"<!-- Auto-generated by modules/prompt/composer.py at "
        f"{result['metadata']['composed_at']} -->\n"
        f"<!-- Sources: {', '.join(result['sources'])} -->\n"
        f"<!-- Version: {result['metadata']['version']} -->\n\n"
    )
    output_path.write_text(header + result["prompt"], encoding="utf-8")


def list_tasks() -> list:
    """Return list of available task prompt names."""
    tasks_dir = PROMPTS_DIR / "tasks"
    if not tasks_dir.exists():
        return []
    return sorted(f.stem for f in tasks_dir.glob("*.md"))


def list_profiles() -> list:
    """Return list of available profile names."""
    profiles_dir = PROMPTS_DIR / "profiles"
    if not profiles_dir.exists():
        return []
    return sorted(f.stem for f in profiles_dir.glob("*.md"))


def list_contexts() -> list:
    """Return list of available context names."""
    contexts_dir = PROMPTS_DIR / "contexts"
    if not contexts_dir.exists():
        return []
    return sorted(f.stem for f in contexts_dir.glob("*.md"))


def list_examples() -> list:
    """Return list of available example names (relative path stems, e.g., 'python/test-driven')."""
    examples_dir = PROMPTS_DIR / "examples"
    if not examples_dir.exists():
        return []
    result = []
    for f in examples_dir.rglob("*.md"):
        rel = f.relative_to(examples_dir)
        # Convert OS path separators to forward slashes
        result.append(str(rel.with_suffix("")).replace("\\", "/"))
    return sorted(result)


def _load_user_profile() -> Optional[str]:
    """Load user profile from markconfig/profile.md as extra instructions."""
    profile_path = PROJECT_ROOT / "markconfig" / "profile.md"
    if profile_path.exists():
        return profile_path.read_text(encoding="utf-8")
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prompt Composer (v1.5)")
    parser.add_argument("--pre-session", action="store_true",
                        help="Pre-session composition for AGENTS_COMPOSED.md")
    parser.add_argument("--task", type=str, default=None,
                        help="Task type: coding|review|research|debugging|planning|writing|automation")
    parser.add_argument("--profile", type=str, default=DEFAULT_PROFILE,
                        help="Profile: default|terse|detailed|socratic")
    parser.add_argument("--context", action="append", default=[],
                        help="Context (can be repeated): python|typescript|secrets|memory|...")
    parser.add_argument("--example", action="append", default=[],
                        help="Example (can be repeated)")
    parser.add_argument("--list-tasks", action="store_true",
                        help="List available task prompts")
    parser.add_argument("--list-profiles", action="store_true",
                        help="List available profiles")
    parser.add_argument("--list-contexts", action="store_true",
                        help="List available contexts")
    parser.add_argument("--list-examples", action="store_true",
                        help="List available examples")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (default: stdout for non-pre-session, AGENTS_COMPOSED.md for --pre-session)")
    args = parser.parse_args(argv)

    if args.list_tasks:
        for t in list_tasks():
            print(t)
        return 0

    if args.list_profiles:
        for p in list_profiles():
            print(p)
        return 0

    if args.list_contexts:
        for c in list_contexts():
            print(c)
        return 0

    if args.list_examples:
        for e in list_examples():
            print(e)
        return 0

    # Load user profile as extra instructions
    extra = _load_user_profile()

    result = compose(
        profile=args.profile,
        task=args.task,
        contexts=args.context or None,
        examples=args.example or None,
        extra_instructions=extra,
    )

    # Lazy import to avoid circular dependency
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
