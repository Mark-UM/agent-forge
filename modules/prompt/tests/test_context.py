"""Tests for modules.prompt.context (v1.5 P0)"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import context


# ---------- detect_file_extension ----------

def test_detect_file_extension_python():
    assert context.detect_file_extension("foo.py") == "python"


def test_detect_file_extension_typescript():
    assert context.detect_file_extension("foo.ts") == "typescript"
    assert context.detect_file_extension("foo.tsx") == "typescript"


def test_detect_file_extension_haskell():
    assert context.detect_file_extension("foo.hs") == "haskell"


def test_detect_file_extension_java():
    assert context.detect_file_extension("Foo.java") == "java"


def test_detect_file_extension_cpp_variants():
    assert context.detect_file_extension("foo.cpp") == "cpp"
    assert context.detect_file_extension("foo.cc") == "cpp"
    assert context.detect_file_extension("foo.cxx") == "cpp"
    assert context.detect_file_extension("foo.h") == "cpp"
    assert context.detect_file_extension("foo.hpp") == "cpp"


def test_detect_file_extension_unknown():
    assert context.detect_file_extension("foo.md") is None
    assert context.detect_file_extension("foo.unknown") is None


def test_detect_file_extension_case_insensitive():
    assert context.detect_file_extension("FOO.PY") == "python"


def test_detect_file_extension_no_extension():
    assert context.detect_file_extension("Makefile") is None


# ---------- detect_file_directory ----------

def test_detect_file_directory_markconfig():
    p = str(PROJECT_ROOT / "markconfig" / "secrets.json")
    assert context.detect_file_directory(p) == "secrets"


def test_detect_file_directory_memory():
    p = str(PROJECT_ROOT / "_data" / "memory" / "MEMORY.md")
    assert context.detect_file_directory(p) == "memory"


def test_detect_file_directory_modules():
    p = str(PROJECT_ROOT / "modules" / "prompt" / "composer.py")
    assert context.detect_file_directory(p) == "modules"


def test_detect_file_directory_opencode():
    p = str(PROJECT_ROOT / ".opencode" / "prompts" / "base.md")
    assert context.detect_file_directory(p) == "opencode"


def test_detect_file_directory_runtime():
    p = str(PROJECT_ROOT / "_runtime" / "prompt" / "log.jsonl")
    assert context.detect_file_directory(p) == "runtime"


def test_detect_file_directory_outside_project(tmp_path):
    p = str(tmp_path / "foo.txt")
    assert context.detect_file_directory(p) is None


# ---------- detect_session_length ----------

def test_detect_session_length_short():
    assert context.detect_session_length(0) == "short"
    assert context.detect_session_length(9) == "short"


def test_detect_session_length_medium():
    assert context.detect_session_length(10) == "medium"
    assert context.detect_session_length(49) == "medium"


def test_detect_session_length_long():
    assert context.detect_session_length(50) == "long"
    assert context.detect_session_length(1000) == "long"


# ---------- detect_keywords ----------

def test_detect_keywords_coding():
    assert "coding" in context.detect_keywords("implement a new function")
    assert "coding" in context.detect_keywords("修复这个 bug")  # 修复 → coding


def test_detect_keywords_debugging():
    assert "debugging" in context.detect_keywords("this crashes on startup")
    assert "debugging" in context.detect_keywords("error in module")


def test_detect_keywords_review():
    assert "review" in context.detect_keywords("review my PR")
    assert "review" in context.detect_keywords("审查代码")


def test_detect_keywords_research():
    assert "research" in context.detect_keywords("research this topic")
    assert "research" in context.detect_keywords("搜索 Python 教程")


def test_detect_keywords_planning():
    assert "planning" in context.detect_keywords("plan a new architecture")


def test_detect_keywords_writing():
    assert "writing" in context.detect_keywords("write documentation")


def test_detect_keywords_automation():
    assert "automation" in context.detect_keywords("automate the browser")


def test_detect_keywords_no_match():
    assert context.detect_keywords("hello world") == []


def test_detect_keywords_multiple_matches():
    result = context.detect_keywords("fix the bug and review the PR")
    assert "coding" in result
    assert "review" in result
    assert "debugging" in result


# ---------- detect_context_signals ----------

def test_detect_context_signals_returns_required_keys():
    signals = context.detect_context_signals("hello", file_paths=[], turn_count=0)
    assert "languages" in signals
    assert "directories" in signals
    assert "session_length" in signals
    assert "task_hints" in signals


def test_detect_context_signals_with_python_file():
    p = str(PROJECT_ROOT / "modules" / "prompt" / "composer.py")
    signals = context.detect_context_signals("implement", file_paths=[p], turn_count=5)
    assert "python" in signals["languages"]
    assert "modules" in signals["directories"]
    assert signals["session_length"] == "short"
    assert "coding" in signals["task_hints"]


# ---------- suggest_contexts ----------

def test_suggest_contexts_python():
    signals = {"languages": ["python"], "directories": [], "session_length": "short", "task_hints": []}
    assert "python" in context.suggest_contexts(signals)


def test_suggest_contexts_secrets():
    signals = {"languages": [], "directories": ["secrets"], "session_length": "short", "task_hints": []}
    assert "secrets" in context.suggest_contexts(signals)


def test_suggest_contexts_memory():
    signals = {"languages": [], "directories": ["memory"], "session_length": "short", "task_hints": []}
    assert "memory" in context.suggest_contexts(signals)


def test_suggest_contexts_long_session():
    signals = {"languages": [], "directories": [], "session_length": "long", "task_hints": []}
    assert "long-session" in context.suggest_contexts(signals)


def test_suggest_contexts_empty():
    # Phase 2: flash-role is always loaded as a constitutional layer
    signals = {"languages": [], "directories": [], "session_length": "short", "task_hints": []}
    assert context.suggest_contexts(signals) == ["flash-role"]


# ---------- suggest_task ----------

def test_suggest_task_returns_first_hint():
    signals = {"task_hints": ["coding", "review"]}
    assert context.suggest_task(signals) == "coding"


def test_suggest_task_returns_none_when_no_hints():
    signals = {"task_hints": []}
    assert context.suggest_task(signals) is None
