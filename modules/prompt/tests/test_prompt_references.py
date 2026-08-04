"""Prompt reference checker (P5 fix).

Validates cross-references between Prompt / Command / Skill / Agent files:
- Command references to Agent names → agent file must exist
- Prompt references to Context files → context file must exist
- Skill references to Python modules → module file must exist
- Agent references to model names → model must be configured in opencode.json
- Trigger references to Context files → context file must exist
- Deprecated component names must not linger anywhere
- Composed prompt must contain the priority declaration header

Run:
    python -m pytest modules/prompt/tests/test_prompt_references.py -v
"""
import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OPENCODE_DIR = PROJECT_ROOT / ".opencode"
AGENTS_DIR = OPENCODE_DIR / "agents"
COMMANDS_DIR = OPENCODE_DIR / "commands"
PROMPTS_DIR = OPENCODE_DIR / "prompts"
SKILLS_DIR = OPENCODE_DIR / "skills"
OPENCODE_JSON = PROJECT_ROOT / "opencode.json"

# Deprecated agent / component names that must not appear in any reference
DEPRECATED_NAMES = {
    "review-standards",
    "review-spec",
    "review-quality",
    "review-architecture",
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _iter_md_files(root: Path) -> list:
    """Yield all .md files under root (recursive, does NOT follow symlinks).

    Uses os.walk(followlinks=False) to avoid broken symlink / reparse point
    errors that rglob hits on Windows.
    """
    if not root.exists():
        return []
    import os
    results = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Skip symlinked directories entirely (external skill packs, etc.)
        dirnames[:] = [d for d in dirnames
                       if not os.path.islink(os.path.join(dirpath, d))]
        for fname in sorted(filenames):
            if fname.endswith(".md"):
                p = Path(dirpath) / fname
                try:
                    p.stat()
                    results.append(p)
                except (OSError, FileNotFoundError):
                    continue
    return sorted(results)


def _iter_project_skills() -> list:
    """Return only project-owned skill directories.

    Excludes vendored external skill packs (anthropics-*, matt-*, obra-*,
    vercel-*) which may contain broken symlinks and are not part of the
    project's own reference graph.
    """
    if not SKILLS_DIR.exists():
        return []
    project_skills = []
    external_prefixes = ("anthropics-", "matt-", "obra-", "vercel-")
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        # Skip symlinks / reparse points that point to external packs
        try:
            if d.is_symlink():
                continue
        except (OSError, NotImplementedError):
            pass
        if d.name.startswith(external_prefixes):
            continue
        # Skip aggregated external skill container dirs
        if d.name in {"anthropics-skills", "mattpocock-skills",
                      "obra-superpowers", "vercel-agent-skills"}:
            continue
        project_skills.append(d)
    return project_skills


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:]
    return content


def _parse_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    fm = content[3:end]
    result = {}
    for line in fm.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip().strip('"\'')
    return result


def _existing_agent_names() -> set:
    return {f.stem for f in AGENTS_DIR.glob("*.md")}


def _existing_context_names() -> set:
    return {f.stem for f in (PROMPTS_DIR / "contexts").glob("*.md")}


def _existing_task_names() -> set:
    return {f.stem for f in (PROMPTS_DIR / "tasks").glob("*.md")}


def _existing_command_names() -> set:
    return {f.stem for f in COMMANDS_DIR.glob("*.md")}


def _existing_skill_names() -> set:
    """Return only project-owned skill directory names."""
    return {d.name for d in _iter_project_skills()}


def _configured_models() -> set:
    """Return model names configured in opencode.json."""
    if not OPENCODE_JSON.exists():
        return set()
    try:
        data = json.loads(OPENCODE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return set()
    models = set()
    # Common patterns: data["models"] = {name: {...}} or data["providers"][...]["models"]
    if isinstance(data.get("models"), dict):
        models.update(data["models"].keys())
    if isinstance(data.get("providers"), dict):
        for provider_cfg in data["providers"].values():
            if isinstance(provider_cfg, dict) and isinstance(
                provider_cfg.get("models"), dict
            ):
                models.update(provider_cfg["models"].keys())
    # Also accept string references like "deepseek/deepseek-v4-flash"
    return models


# ── 1. Deprecated names must not appear ──────────────────────────────────

@pytest.mark.parametrize("md_file", _iter_md_files(OPENCODE_DIR))
def test_no_deprecated_component_references(md_file):
    """P5: No file under .opencode/ should reference deprecated components."""
    content = _read(md_file)
    violations = []
    for name in DEPRECATED_NAMES:
        # Match the name as a token (not a substring of another word)
        pattern = rf"\b{re.escape(name)}\b"
        if re.search(pattern, content):
            violations.append(name)
    assert not violations, (
        f"{md_file.relative_to(PROJECT_ROOT)} references deprecated "
        f"components: {violations}. These were removed; update references."
    )


# ── 2. Command → Agent references must resolve ───────────────────────────

def _extract_agent_references(content: str) -> set:
    """Extract agent names referenced in a command/prompt file.

    Looks for patterns like:
        - frontmatter `agent:` field
        - "review-code subagent" / "review-code agent"
        - "review-code.md"
        - "Agent: review-code"
    """
    refs = set()
    fm = _parse_frontmatter(content)
    if "agent" in fm and fm["agent"]:
        # agent: build means the build agent, not a review subagent
        # but we still record it
        refs.add(fm["agent"])

    # Match "review-code" as a standalone token (with word boundaries)
    # Look in the body, not the frontmatter
    body = _strip_frontmatter(content)
    # Pattern: agent names are kebab-case identifiers
    for match in re.finditer(r"\b([a-z][a-z0-9-]*-code)\b", body):
        refs.add(match.group(1))
    for match in re.finditer(r"\b([a-z][a-z0-9-]*-structure)\b", body):
        refs.add(match.group(1))
    for match in re.finditer(r"\b([a-z][a-z0-9-]*-risk)\b", body):
        refs.add(match.group(1))
    # Also catch explicit "review-XXX" references
    for match in re.finditer(r"\b(review-[a-z]+)\b", body):
        refs.add(match.group(1))
    return refs


@pytest.mark.parametrize("cmd_file", _iter_md_files(COMMANDS_DIR))
def test_command_agent_references_exist(cmd_file):
    """P5: Every agent referenced by a command must exist in .opencode/agents/."""
    content = _read(cmd_file)
    refs = _extract_agent_references(content)
    # Filter out generic agent names like "build" that aren't subagents
    subagent_refs = {r for r in refs if r.startswith("review-")}
    if not subagent_refs:
        return
    existing = _existing_agent_names()
    missing = subagent_refs - existing
    assert not missing, (
        f"{cmd_file.relative_to(PROJECT_ROOT)} references non-existent "
        f"agents: {sorted(missing)}. Existing agents: {sorted(existing)}"
    )


# ── 3. Prompt → Context references must resolve ─────────────────────────

def _extract_context_references(content: str) -> set:
    """Extract context file references like 'contexts/foo.md' or 'contexts/foo'."""
    refs = set()
    # Match contexts/<name>.md or contexts/<name>
    for m in re.finditer(r"contexts/([a-z0-9-]+)(?:\.md)?", content):
        refs.add(m.group(1))
    return refs


@pytest.mark.parametrize("md_file", _iter_md_files(OPENCODE_DIR))
def test_prompt_context_references_exist(md_file):
    """P5: Every contexts/<name>.md reference must point to an existing file."""
    content = _read(md_file)
    refs = _extract_context_references(content)
    if not refs:
        return
    existing = _existing_context_names()
    missing = refs - existing
    assert not missing, (
        f"{md_file.relative_to(PROJECT_ROOT)} references non-existent "
        f"contexts: {sorted(missing)}. Existing: {sorted(existing)}"
    )


# ── 4. AGENTS.md → Context references must resolve ──────────────────────

def test_agents_md_context_table_resolves():
    """P5: The Context Awareness table in AGENTS.md must reference existing
    context files. Specifically checks for the new threejs-game-loop and
    tower-stack-project contexts added in Phase 2.2."""
    agents_md = PROJECT_ROOT / "AGENTS.md"
    if not agents_md.exists():
        pytest.skip("AGENTS.md not found")
    content = _read(agents_md)
    refs = _extract_context_references(content)
    existing = _existing_context_names()
    missing = refs - existing
    assert not missing, (
        f"AGENTS.md references non-existent contexts: {sorted(missing)}"
    )
    # Critical: the new Phase 2.2 contexts must be referenced
    assert "threejs-game-loop" in refs, (
        "AGENTS.md must reference threejs-game-loop.md context "
        "(Phase 2.2 isolation)"
    )
    assert "tower-stack-project" in refs, (
        "AGENTS.md must reference tower-stack-project.md context "
        "(Phase 2.2 isolation)"
    )


# ── 5. Skill → Python module references must resolve ────────────────────

def _extract_python_module_references(content: str) -> set:
    """Extract references like 'modules.foo.bar' or 'modules/foo/bar.py'."""
    refs = set()
    # modules.foo.bar
    for m in re.finditer(r"\bmodules\.([a-z_][a-z0-9_.]+)\b", content):
        refs.add(m.group(1))
    # modules/foo/bar
    for m in re.finditer(r"modules/([a-z_][a-z0-9_/]+)", content):
        refs.add(m.group(1).replace("/", "."))
    return refs


@pytest.mark.parametrize("skill_dir", _iter_project_skills())
def test_skill_python_module_references_exist(skill_dir):
    """P5: Every Python module referenced by a SKILL.md must exist.

    A reference like `modules.foo.bar.baz` is resolved by:
      1. Trying modules/foo/bar/baz.py        (baz is a module)
      2. Trying modules/foo/bar/baz/__init__.py (baz is a package)
      3. Trying modules/foo/bar.py             (baz is a function/attr in bar)
      4. Trying modules/foo/bar/__init__.py    (baz is an attr in package bar)
      5. Trying modules/foo/bar/baz/ as a directory (data files, not Python)
    If none resolve, the reference is reported as missing.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        pytest.skip(f"{skill_dir.name}/SKILL.md not found")
    content = _read(skill_md)
    refs = _extract_python_module_references(content)
    if not refs:
        return
    missing = []
    for ref in refs:
        parts = ref.split(".")
        if not parts:
            continue
        # Prepend "modules" since the regex stripped it
        full_parts = ["modules"] + parts
        resolved = False
        # Strategy 1: deepest is a module file
        candidate = PROJECT_ROOT.joinpath(*full_parts[:-1],
                                          full_parts[-1] + ".py")
        if candidate.exists():
            resolved = True
            continue
        # Strategy 2: deepest is a package
        candidate = PROJECT_ROOT.joinpath(*full_parts, "__init__.py")
        if candidate.exists():
            resolved = True
            continue
        # Strategy 3: last segment is a function/attribute, drop it
        if len(full_parts) >= 2:
            candidate = PROJECT_ROOT.joinpath(
                *full_parts[:-2], full_parts[-2] + ".py")
            if candidate.exists():
                resolved = True
                continue
            candidate = PROJECT_ROOT.joinpath(
                *full_parts[:-1], "__init__.py")
            if candidate.exists():
                resolved = True
                continue
        # Strategy 4: path exists as a directory (data files, not a Python pkg)
        candidate_dir = PROJECT_ROOT.joinpath(*full_parts)
        if candidate_dir.is_dir():
            resolved = True
            continue
        if not resolved:
            # Strip trailing dots from ref for cleaner error message
            clean_ref = ref.rstrip(".")
            if clean_ref:
                missing.append(clean_ref)
    assert not missing, (
        f"{skill_dir.name}/SKILL.md references non-existent Python "
        f"modules: {sorted(missing)}"
    )


# ── 6. Agent → Model references must be configured ─────────────────────

@pytest.mark.parametrize("agent_file", _iter_md_files(AGENTS_DIR))
def test_agent_model_references_configured(agent_file):
    """P5: Every agent's `model:` field must be configured in opencode.json.

    Skipped if opencode.json has no models block (e.g., minimal test env).
    """
    content = _read(agent_file)
    fm = _parse_frontmatter(content)
    model = fm.get("model", "").strip()
    if not model:
        pytest.skip(f"{agent_file.name} has no model: field")
    configured = _configured_models()
    if not configured:
        pytest.skip("opencode.json has no models block")
    # Models may be referenced as "provider/model" form; accept either
    # the full string or the part after the slash
    short_name = model.split("/")[-1] if "/" in model else model
    assert model in configured or short_name in configured, (
        f"{agent_file.name} references model '{model}' which is not "
        f"configured in opencode.json. Configured: {sorted(configured)}"
    )


# ── 7. Anti-patterns.md must not contain Tower Stack / Three.js specifics ──

def test_anti_patterns_no_tower_stack_specifics():
    """P5: anti-patterns.md must not contain Tower-Stack-specific examples
    that pollute non-TS projects. Tower Stack rules live in
    tower-stack-project.md."""
    ap = PROMPTS_DIR / "contexts" / "anti-patterns.md"
    if not ap.exists():
        pytest.skip("anti-patterns.md not found")
    content = _read(ap)
    # These tokens are Tower-Stack-specific and should NOT appear in the
    # cross-language anti-patterns file
    forbidden_tokens = [
        "SoundManager.init()",
        "Tower Stack 3D",
        "tower-stack",
        "EffectComposer",  # Three.js specific
        "requestAnimationFrame",  # Three.js specific game loop
        "main.ts",  # TS-specific bootstrap
        "Core layer importing `three`",
    ]
    violations = [t for t in forbidden_tokens if t in content]
    assert not violations, (
        f"anti-patterns.md still contains project-specific tokens that "
        f"should be isolated: {violations}"
    )


def test_tower_stack_context_exists():
    """P5: tower-stack-project.md must exist after Phase 2.2 isolation."""
    ctx = PROMPTS_DIR / "contexts" / "tower-stack-project.md"
    assert ctx.exists(), (
        "tower-stack-project.md context must exist (Phase 2.2) to isolate "
        "Tower Stack rules from generic anti-patterns"
    )


def test_threejs_context_exists():
    """P5: threejs-game-loop.md must exist after Phase 2.2 isolation."""
    ctx = PROMPTS_DIR / "contexts" / "threejs-game-loop.md"
    assert ctx.exists(), (
        "threejs-game-loop.md context must exist (Phase 2.2) to isolate "
        "Three.js rules from generic anti-patterns"
    )


# ── 8. Composer must emit priority declaration ──────────────────────────

def test_composer_emits_priority_declaration():
    """P5: composer.compose() must emit the priority declaration header."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from modules.prompt import composer
    result = composer.compose(profile="default")
    prompt = result["prompt"]
    assert "PRIORITY DECLARATION" in prompt, (
        "Composed prompt must contain the priority declaration header"
    )
    assert "BASE" in prompt and "TASK" in prompt and "CONTEXT" in prompt, (
        "Priority declaration must enumerate BASE / TASK / CONTEXT layers"
    )
    assert "priority_ladder" in result, (
        "compose() return value must include priority_ladder metadata"
    )


# ── 9. base.md / AGENTS_BASE.md must have unified plan rules ────────────

def test_base_md_has_deterministic_plan_rules():
    """P5/R2-3.1: AGENTS_BASE.md must have the deterministic
    'Rule 1 / Rule 2 / Rule 3' plan-vs-execute section.

    R2-3.1: base.md (the slimmed prompt layer) must NOT contain these rules —
    they live in AGENTS_BASE.md and task prompts. base.md must also NOT
    simultaneously require 'do not question' and 'wait for confirmation'
    (completion criterion 10)."""
    # AGENTS_BASE.md must contain all three rules
    agents_base = PROJECT_ROOT / "AGENTS_BASE.md"
    if agents_base.exists():
        content = _read(agents_base)
        assert "Rule 1 — Execute immediately" in content, (
            "AGENTS_BASE.md must contain Rule 1 of the deterministic plan rules"
        )
        assert "Rule 2 — Brief plan" in content, (
            "AGENTS_BASE.md must contain Rule 2 of the deterministic plan rules"
        )
        assert "Rule 3 — Plan and WAIT" in content, (
            "AGENTS_BASE.md must contain Rule 3 of the deterministic plan rules"
        )
        # The old conflicting wording must be gone
        assert "No plan presentation needed unless explicitly asked" not in content, (
            "AGENTS_BASE.md still has the old conflicting plan rule wording"
        )

    # R2-3.1: base.md must NOT contain plan rules (they were moved out)
    base_md = PROMPTS_DIR / "base.md"
    if base_md.exists():
        base_content = _read(base_md)
        assert "Rule 1 — Execute immediately" not in base_content, (
            "base.md must NOT contain plan rules (R2-3.1: moved to AGENTS_BASE.md/task prompts)"
        )
        # Criterion 10: base.md must NOT simultaneously require
        # "do not question" and "wait for confirmation"
        has_no_question = "no question" in base_content.lower() or "do not question" in base_content.lower()
        has_wait_confirm = "wait for confirmation" in base_content.lower() or "等待确认" in base_content
        assert not (has_no_question and has_wait_confirm), (
            "base.md must NOT simultaneously require 'no questioning' and "
            "'wait for confirmation' (R2-3.1 / criterion 10)"
        )


# ── 10. review.md task prompt must reference current 3 agents ───────────

def test_review_task_prompt_references_current_agents():
    """P5: tasks/review.md must reference review-code, review-structure,
    review-risk — not the deprecated review-standards / review-spec."""
    review_task = PROMPTS_DIR / "tasks" / "review.md"
    if not review_task.exists():
        pytest.skip("tasks/review.md not found")
    content = _read(review_task)
    assert "review-code" in content
    assert "review-structure" in content
    assert "review-risk" in content
    assert "review-standards" not in content
    assert "review-spec" not in content


# ── 11. Trigger validation ──────────────────────────────────────────────

def test_context_triggers_are_real_signals():
    """P5: Context files must use real trigger signals (file path, imports,
    package.json, marker file) — NOT 'when any code language is detected'
    for project-specific rules."""
    project_specific = {
        "threejs-game-loop",
        "tower-stack-project",
        "ui-components",
    }
    contexts_dir = PROMPTS_DIR / "contexts"
    for ctx_name in project_specific:
        ctx_file = contexts_dir / f"{ctx_name}.md"
        if not ctx_file.exists():
            continue
        content = _read(ctx_file)
        fm = _parse_frontmatter(content)
        trigger = fm.get("trigger", "").lower()
        # Project-specific contexts must NOT use "any code language" trigger
        assert "any code language" not in trigger, (
            f"{ctx_name}.md uses the over-broad 'any code language' trigger; "
            f"project-specific contexts must use real signals (file path, "
            f"package.json dep, marker file, etc.)"
        )
        # Must reference a real signal
        real_signals = ["package.json", "marker", "file path", "imports",
                        "src/", "matches"]
        assert any(s in trigger for s in real_signals), (
            f"{ctx_name}.md trigger '{trigger}' does not reference a real "
            f"signal; must use file path / package.json / marker / import"
        )


# ── 12. Cross-file consistency: anti-patterns must not duplicate ui-components ──

def test_anti_patterns_does_not_duplicate_ui_rules():
    """P5: anti-patterns.md (cross-language) must not duplicate rules that
    already live in ui-components.md (which has a narrower trigger)."""
    ap = PROMPTS_DIR / "contexts" / "anti-patterns.md"
    ui = PROMPTS_DIR / "contexts" / "ui-components.md"
    if not ap.exists() or not ui.exists():
        pytest.skip("Required context files missing")
    ap_content = _read(ap)
    # UI-specific patterns that should only appear in ui-components.md
    ui_only_patterns = [
        "BasePanel",
        "UIManager",
        "refreshText()",
        "setLocale()",
        "src/ui/panels/",
    ]
    violations = [p for p in ui_only_patterns if p in ap_content]
    # Note: anti-patterns.md may mention these in passing; the rule is that
    # the *rules* (forbidden patterns with specific Panel class requirements)
    # should not be duplicated. We check for the more specific markers.
    assert not violations, (
        f"anti-patterns.md duplicates UI-specific rules that belong in "
        f"ui-components.md: {violations}"
    )
