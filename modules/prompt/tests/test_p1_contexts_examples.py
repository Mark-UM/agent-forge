"""Tests for P1 features: contexts + examples loading (v1.5 P1)"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import composer, context


# ---------- P1 contexts exist ----------

EXPECTED_CONTEXTS = [
    "python", "typescript", "haskell", "java", "cpp",
    "secrets", "memory", "long-session",
]


@pytest.mark.parametrize("ctx_name", EXPECTED_CONTEXTS)
def test_context_file_exists(ctx_name):
    """All 8 P1 context prompts must exist."""
    path = PROJECT_ROOT / ".opencode" / "prompts" / "contexts" / f"{ctx_name}.md"
    assert path.exists(), f"Missing context: {ctx_name}.md"


def test_list_contexts_returns_all_p1():
    """list_contexts() returns all 8 P1 contexts."""
    contexts = composer.list_contexts()
    for expected in EXPECTED_CONTEXTS:
        assert expected in contexts, f"Missing context in listing: {expected}"


# ---------- P1 examples exist ----------

EXPECTED_EXAMPLES = [
    "python/test-driven",
    "python/refactor-extract-method",
    "python/bug-fix-pattern",
    "python/async-pattern",
    "typescript/react-component",
    "typescript/api-endpoint",
    "typescript/type-narrowing",
    "haskell/monad-transformer",
    "haskell/type-class",
    "review/security-checklist",
    "review/performance-pattern",
    "review/architecture-smell",
]


@pytest.mark.parametrize("ex_path", EXPECTED_EXAMPLES)
def test_example_file_exists(ex_path):
    """All 12 P1 Few-Shot examples must exist."""
    path = PROJECT_ROOT / ".opencode" / "prompts" / "examples" / f"{ex_path}.md"
    assert path.exists(), f"Missing example: {ex_path}.md"


# ---------- compose with contexts ----------

def test_compose_with_python_context():
    """compose(contexts=['python']) loads python context."""
    result = composer.compose(contexts=["python"])
    assert any("contexts/python.md" in s for s in result["sources"])
    assert "PEP 8" in result["prompt"]  # distinctive content from python.md


def test_compose_with_secrets_context():
    """compose(contexts=['secrets']) loads secrets context."""
    result = composer.compose(contexts=["secrets"])
    assert any("contexts/secrets.md" in s for s in result["sources"])
    assert "NEVER" in result["prompt"]  # Iron Rules contain NEVER


def test_compose_with_multiple_contexts():
    """compose(contexts=['python', 'secrets']) loads both."""
    result = composer.compose(contexts=["python", "secrets"])
    assert any("contexts/python.md" in s for s in result["sources"])
    assert any("contexts/secrets.md" in s for s in result["sources"])


def test_compose_with_nonexistent_context_skips_gracefully():
    """compose(contexts=['nonexistent']) does not crash; just skips."""
    result = composer.compose(contexts=["nonexistent"])
    assert isinstance(result["sources"], list)
    assert not any("nonexistent" in s for s in result["sources"])


# ---------- compose with examples ----------

def test_compose_with_python_example():
    """compose(examples=['python/test-driven']) loads the example."""
    result = composer.compose(examples=["python/test-driven"])
    assert any("examples/python/test-driven.md" in s for s in result["sources"])
    assert "pytest" in result["prompt"] or "fibonacci" in result["prompt"].lower()


def test_compose_with_multiple_examples():
    """compose(examples=[...]) loads all examples."""
    result = composer.compose(examples=[
        "python/test-driven",
        "typescript/react-component",
        "haskell/monad-transformer",
    ])
    assert any("python/test-driven.md" in s for s in result["sources"])
    assert any("typescript/react-component.md" in s for s in result["sources"])
    assert any("haskell/monad-transformer.md" in s for s in result["sources"])


def test_compose_with_nonexistent_example_skips_gracefully():
    """compose(examples=['nope/missing']) skips without crashing."""
    result = composer.compose(examples=["nope/missing"])
    assert not any("missing" in s for s in result["sources"])


# ---------- full compose (base + profile + task + context + example) ----------

def test_compose_full_stack():
    """All 5 layers compose together."""
    result = composer.compose(
        profile="default",
        task="coding",
        contexts=["python"],
        examples=["python/test-driven"],
    )
    # All layers loaded
    assert any("base.md" in s for s in result["sources"])
    assert any("profiles/default.md" in s for s in result["sources"])
    assert any("tasks/coding.md" in s for s in result["sources"])
    assert any("contexts/python.md" in s for s in result["sources"])
    assert any("examples/python/test-driven.md" in s for s in result["sources"])

    # Section headers present in correct order (P4: priority ladder order)
    # BASE → TASK → CONTEXT → PROFILE → EXAMPLE
    prompt = result["prompt"]
    # Find the section headers (not the priority declaration mentions)
    base_idx = prompt.find("# === BASE")
    task_idx = prompt.find("# === TASK: coding")
    ctx_idx = prompt.find("# === CONTEXT: python")
    profile_idx = prompt.find("# === PROFILE: default")
    ex_idx = prompt.find("# === EXAMPLE: python/test-driven")
    assert base_idx < task_idx < ctx_idx < profile_idx < ex_idx


# ---------- context detection integration ----------

def test_context_detector_to_suggestions_end_to_end():
    """End-to-end: detect signals from message + file → suggest contexts."""
    py_file = str(PROJECT_ROOT / "modules" / "prompt" / "composer.py")
    signals = context.detect_context_signals(
        message="implement a feature",
        file_paths=[py_file],
        turn_count=5,
    )
    suggested = context.suggest_contexts(signals)
    assert "python" in suggested
    assert "modules" in signals["directories"]


def test_context_detector_secrets_directory():
    """Detection of markconfig/ → secrets context."""
    secrets_file = str(PROJECT_ROOT / "markconfig" / "secrets.json")
    # File may not exist in test env, but detection works on path alone
    signals = context.detect_context_signals(
        message="update config",
        file_paths=[secrets_file],
        turn_count=2,
    )
    suggested = context.suggest_contexts(signals)
    assert "secrets" in suggested


# ---------- example metadata ----------

def test_examples_have_yaml_frontmatter():
    """All example files must have YAML frontmatter with description."""
    examples_dir = PROJECT_ROOT / ".opencode" / "prompts" / "examples"
    for ex_file in examples_dir.rglob("*.md"):
        content = ex_file.read_text(encoding="utf-8")
        assert content.startswith("---"), f"Missing frontmatter in {ex_file}"
        assert "description:" in content[:500], f"Missing description in {ex_file}"


def test_contexts_have_yaml_frontmatter():
    """All context files must have YAML frontmatter."""
    contexts_dir = PROJECT_ROOT / ".opencode" / "prompts" / "contexts"
    for ctx_file in contexts_dir.glob("*.md"):
        content = ctx_file.read_text(encoding="utf-8")
        assert content.startswith("---"), f"Missing frontmatter in {ctx_file}"
        assert "description:" in content[:500], f"Missing description in {ctx_file}"


# ---------- CLI integration ----------

def test_main_list_contexts(capsys):
    """CLI --list-contexts outputs all 8 P1 contexts."""
    rc = composer.main(["--list-contexts"])
    captured = capsys.readouterr()
    assert rc == 0
    for expected in EXPECTED_CONTEXTS:
        assert expected in captured.out


def test_main_with_context_and_example(capsys, tmp_path, monkeypatch):
    """CLI accepts --context and --example flags."""
    monkeypatch.setattr("modules.prompt.log.RUNTIME_DIR", tmp_path)
    monkeypatch.setattr("modules.prompt.log.LOG_PATH", tmp_path / "composition_log.jsonl")
    rc = composer.main([
        "--profile", "default",
        "--context", "python",
        "--example", "python/test-driven",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "CONTEXT: python" in captured.out
    assert "EXAMPLE: python/test-driven" in captured.out
