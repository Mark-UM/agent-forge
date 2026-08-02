"""Tests for modules.prompt.composer (v1.5 P0)"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import composer


# ---------- _strip_frontmatter ----------

def test_strip_frontmatter_with_yaml():
    content = "---\nkey: value\n---\n\nbody text"
    result = composer._strip_frontmatter(content)
    assert result == "body text"


def test_strip_frontmatter_without_yaml():
    content = "just body"
    assert composer._strip_frontmatter(content) == "just body"


def test_strip_frontmatter_empty():
    assert composer._strip_frontmatter("") == ""


# ---------- _parse_simple_yaml ----------

def test_parse_simple_yaml_basic():
    text = 'description: "test"\npriority: 100'
    result = composer._parse_simple_yaml(text)
    assert result["description"] == "test"
    assert result["priority"] == "100"


def test_parse_simple_yaml_list():
    text = "leading_words: [red, green, seam]"
    result = composer._parse_simple_yaml(text)
    assert result["leading_words"] == ["red", "green", "seam"]


def test_parse_simple_yaml_single_quoted():
    text = "key: 'value'"
    result = composer._parse_simple_yaml(text)
    assert result["key"] == "value"


def test_parse_simple_yaml_skips_comments():
    text = "# comment\nkey: value"
    result = composer._parse_simple_yaml(text)
    assert result == {"key": "value"}


def test_parse_simple_yaml_skips_empty_lines():
    text = "\n\nkey: value\n\n"
    result = composer._parse_simple_yaml(text)
    assert result == {"key": "value"}


# ---------- load_prompt ----------

def test_load_prompt_existing_file(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\nkey: val\n---\nbody content", encoding="utf-8")
    result = composer.load_prompt(f)
    assert result == "body content"


def test_load_prompt_nonexistent(tmp_path):
    result = composer.load_prompt(tmp_path / "nonexistent.md")
    assert result == ""


# ---------- load_with_metadata ----------

def test_load_with_metadata_parses_frontmatter(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\ndescription: test\npriority: 100\n---\nbody", encoding="utf-8")
    result = composer.load_with_metadata(f)
    assert result["content"] == "body"
    assert result["metadata"]["description"] == "test"
    assert result["metadata"]["priority"] == "100"


def test_load_with_metadata_no_frontmatter(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("just body", encoding="utf-8")
    result = composer.load_with_metadata(f)
    assert result["content"] == "just body"
    assert result["metadata"] == {}


def test_load_with_metadata_nonexistent(tmp_path):
    result = composer.load_with_metadata(tmp_path / "nope.md")
    assert result["content"] == ""
    assert result["metadata"] == {}


# ---------- compose ----------

def test_compose_minimal_returns_dict_with_required_keys():
    result = composer.compose(profile="default")
    assert "prompt" in result
    assert "sources" in result
    assert "metadata" in result
    assert isinstance(result["prompt"], str)
    assert isinstance(result["sources"], list)
    assert isinstance(result["metadata"], dict)


def test_compose_metadata_contains_version():
    result = composer.compose()
    assert result["metadata"]["version"] == "1.5.0"


def test_compose_metadata_contains_profile():
    result = composer.compose(profile="terse")
    assert result["metadata"]["profile"] == "terse"


def test_compose_loads_base():
    result = composer.compose()
    # base.md must be loaded
    assert any("base.md" in s for s in result["sources"])


def test_compose_loads_profile():
    result = composer.compose(profile="terse")
    assert any("profiles/terse.md" in s for s in result["sources"])


def test_compose_loads_task():
    result = composer.compose(task="coding")
    assert any("tasks/coding.md" in s for s in result["sources"])


def test_compose_loads_multiple_contexts():
    result = composer.compose(contexts=["python", "secrets"])
    # P1 contexts exist — verify both are loaded
    assert any("contexts/python.md" in s for s in result["sources"])
    assert any("contexts/secrets.md" in s for s in result["sources"])


def test_compose_includes_extra_instructions():
    result = composer.compose(extra_instructions="EXTRA_MARKER_TEXT")
    assert "EXTRA_MARKER_TEXT" in result["prompt"]


def test_compose_task_none_does_not_load_task():
    result = composer.compose(task=None)
    assert not any("tasks/" in s for s in result["sources"])


def test_compose_prompt_has_section_headers():
    result = composer.compose(profile="default")
    assert "BASE" in result["prompt"]
    assert "PROFILE" in result["prompt"]


# ---------- write_composed ----------

def test_write_composed_creates_file(tmp_path):
    result = composer.compose()
    output = tmp_path / "output.md"
    composer.write_composed(result, output)
    assert output.exists()
    content = output.read_text(encoding="utf-8")
    assert "Auto-generated" in content
    assert result["prompt"] in content


def test_write_composed_creates_parent_dir(tmp_path):
    result = composer.compose()
    output = tmp_path / "subdir" / "nested" / "output.md"
    composer.write_composed(result, output)
    assert output.exists()


# ---------- list_tasks / list_profiles / list_contexts ----------

def test_list_tasks_returns_list():
    tasks = composer.list_tasks()
    assert isinstance(tasks, list)
    # All 7 P0 tasks must be present
    expected = {"coding", "review", "research", "debugging", "planning", "writing", "automation"}
    assert expected.issubset(set(tasks))


def test_list_profiles_returns_list():
    profiles = composer.list_profiles()
    assert isinstance(profiles, list)
    expected = {"default", "terse", "detailed", "socratic"}
    assert expected.issubset(set(profiles))


def test_list_contexts_returns_list():
    # P1 contexts exist — verify all 8 are present
    contexts = composer.list_contexts()
    assert isinstance(contexts, list)
    expected = {"python", "typescript", "haskell", "java", "cpp",
                "secrets", "memory", "long-session"}
    assert expected.issubset(set(contexts))


# ---------- main (CLI) ----------

def test_main_list_tasks(capsys):
    rc = composer.main(["--list-tasks"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "coding" in captured.out
    assert "review" in captured.out


def test_main_list_profiles(capsys):
    rc = composer.main(["--list-profiles"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "default" in captured.out
    assert "terse" in captured.out


def test_main_stdout(capsys):
    rc = composer.main(["--profile", "default"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "BASE" in captured.out


# ---------- list_examples (P1 gap fix) ----------

def test_list_examples_returns_list():
    examples = composer.list_examples()
    assert isinstance(examples, list)
    # All 12 P1 examples must be present
    expected = {
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
    }
    assert expected.issubset(set(examples))


def test_list_examples_uses_forward_slashes():
    """On Windows, paths must use forward slashes (not backslashes)."""
    examples = composer.list_examples()
    for ex in examples:
        assert "\\" not in ex, f"Backslash found in example name: {ex}"


def test_main_list_examples(capsys):
    rc = composer.main(["--list-examples"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "python/test-driven" in captured.out
    assert "haskell/monad-transformer" in captured.out
