"""Phase 1 Context Detection Tests

Validates the 3 new context detection rules added in Phase 1:

  1. `anti-patterns` context — loaded when any code language detected
  2. `naming-contract` context — loaded when spec docs present OR code files touched
  3. `test-quality` context — loaded when test files detected

Also validates:
  - detect_test_file() recognizes all common test file patterns across languages
  - detect_spec_doc() recognizes spec/design/requirement docs and docs/ directories
  - detect_context_signals() includes has_test_files / has_spec_docs flags
  - suggest_contexts() emits the 3 new contexts in correct priority order
  - composer.py can load the 3 new context prompt files (integration check)
  - Existing tests still pass (backward compat for signal dict)
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.prompt import context, composer


# ── detect_test_file ───────────────────────────────────────────

class TestDetectTestFile:
    """detect_test_file should recognize common test file naming conventions."""

    @pytest.mark.parametrize("filename,expected", [
        # TypeScript / JavaScript
        ("foo.test.ts", True),
        ("foo.test.tsx", True),
        ("foo.spec.ts", True),
        ("foo.spec.tsx", True),
        ("foo.test.js", True),
        ("foo.spec.js", True),
        # Python
        ("test_foo.py", True),
        ("foo_test.py", True),
        # Go
        ("test_foo.go", True),
        ("foo_test.go", True),
        # Java (JUnit) — Test*.java convention (matches TestFoo, Tester, TestCalculator, etc.)
        ("TestFoo.java", True),
        ("Tester.java", True),  # Test + "er" suffix — valid Test*.java match
        # Non-test files (must return False)
        ("foo.ts", False),
        ("foo.py", False),
        ("foo.go", False),
        ("Foo.java", False),  # Does not start with "Test"
        ("README.md", False),
        ("Makefile", False),
        # Edge cases
        ("", False),
        ("test.py", False),  # "test" alone, not test_*.py
    ])
    def test_test_file_patterns(self, filename, expected):
        assert context.detect_test_file(filename) is expected

    def test_test_file_with_full_path(self):
        """Full path should still match by basename."""
        assert context.detect_test_file("/some/dir/foo.test.ts") is True
        assert context.detect_test_file("C:\\proj\\test_main.py") is True
        assert context.detect_test_file("/some/dir/foo.ts") is False

    def test_test_file_case_insensitive(self):
        """Patterns are case-insensitive."""
        assert context.detect_test_file("FOO.TEST.TS") is True
        assert context.detect_test_file("TEST_FOO.PY") is True


# ── detect_spec_doc ────────────────────────────────────────────

class TestDetectSpecDoc:
    """detect_spec_doc should recognize spec / design / requirement docs."""

    @pytest.mark.parametrize("filename,expected", [
        # Filename patterns
        ("api.spec.md", True),
        ("game.specification.md", True),
        ("system-design.md", True),
        ("design-doc.md", True),
        ("architecture-spec.md", True),
        ("user-requirements.md", True),
        ("product-prd.md", True),
        # Non-spec docs
        ("README.md", False),
        ("CHANGELOG.md", False),
        ("notes.md", False),
        # Non-markdown
        ("spec.txt", False),
        ("design.doc", False),
        # Edge cases
        ("", False),
    ])
    def test_spec_doc_filename_patterns(self, filename, expected):
        assert context.detect_spec_doc(filename) is expected

    def test_spec_doc_in_specs_directory(self):
        """Files under specs/ directory should be recognized as spec docs."""
        p = str(PROJECT_ROOT / "specs" / "api.md")
        assert context.detect_spec_doc(p) is True

    def test_spec_doc_in_docs_directory(self):
        """Files under docs/ directory should be recognized as spec docs."""
        p = str(PROJECT_ROOT / "docs" / "architecture.md")
        assert context.detect_spec_doc(p) is True

    def test_spec_doc_in_design_directory(self):
        """Files under design/ directory should be recognized as spec docs."""
        p = str(PROJECT_ROOT / "design" / "ui.md")
        assert context.detect_spec_doc(p) is True

    def test_non_md_in_specs_directory_not_recognized(self):
        """Non-markdown files in specs/ should NOT be recognized."""
        p = str(PROJECT_ROOT / "specs" / "api.txt")
        assert context.detect_spec_doc(p) is False

    def test_spec_doc_outside_project_falls_back_to_raw_parts(self):
        """Spec doc detection should work even for paths outside the project."""
        # Use a path that doesn't exist but has spec/design dir hint
        assert context.detect_spec_doc("/tmp/specs/foo.md") is True
        assert context.detect_spec_doc("/tmp/docs/design.md") is True
        # Random non-spec path
        assert context.detect_spec_doc("/tmp/random/foo.md") is False


# ── detect_context_signals (extended) ──────────────────────────

class TestDetectContextSignalsExtended:
    """detect_context_signals should include has_test_files / has_spec_docs flags."""

    def test_signals_includes_new_keys(self):
        """New keys must be present in signals dict (backward compat)."""
        signals = context.detect_context_signals("hello", file_paths=[], turn_count=0)
        assert "has_test_files" in signals
        assert "has_spec_docs" in signals
        assert signals["has_test_files"] is False
        assert signals["has_spec_docs"] is False

    def test_signals_detects_test_file(self):
        """When test file is in working set, has_test_files should be True."""
        p = str(PROJECT_ROOT / "src" / "foo.test.ts")
        signals = context.detect_context_signals("implement", file_paths=[p])
        assert signals["has_test_files"] is True

    def test_signals_detects_spec_doc(self):
        """When spec doc is in working set, has_spec_docs should be True."""
        p = str(PROJECT_ROOT / "specs" / "api.md")
        signals = context.detect_context_signals("implement", file_paths=[p])
        assert signals["has_spec_docs"] is True

    def test_signals_detects_both(self):
        """Both test files and spec docs can be detected simultaneously."""
        test_p = str(PROJECT_ROOT / "src" / "foo.test.ts")
        spec_p = str(PROJECT_ROOT / "specs" / "api.md")
        signals = context.detect_context_signals("implement", file_paths=[test_p, spec_p])
        assert signals["has_test_files"] is True
        assert signals["has_spec_docs"] is True

    def test_signals_preserves_existing_keys(self):
        """Existing signal keys must still be present (backward compat)."""
        signals = context.detect_context_signals("hello", file_paths=[], turn_count=0)
        for key in ("languages", "directories", "session_length", "task_hints"):
            assert key in signals, f"Missing existing key: {key}"


# ── suggest_contexts (Phase 1 additions) ───────────────────────

class TestSuggestContextsPhase1:
    """suggest_contexts should emit the 3 new contexts in correct scenarios."""

    def test_anti_patterns_loaded_when_code_detected(self):
        """anti-patterns context should load when any code language detected."""
        signals = {
            "languages": ["python"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "anti-patterns" in result

    def test_anti_patterns_not_loaded_without_code(self):
        """anti-patterns should NOT load when no code language detected."""
        signals = {
            "languages": [],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "anti-patterns" not in result

    def test_naming_contract_loaded_with_code(self):
        """naming-contract should load when code files are touched (even without spec doc)."""
        signals = {
            "languages": ["typescript"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "naming-contract" in result

    def test_naming_contract_loaded_with_spec_doc_only(self):
        """naming-contract should load when spec doc present (even without code)."""
        signals = {
            "languages": [],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": True,
        }
        result = context.suggest_contexts(signals)
        assert "naming-contract" in result

    def test_naming_contract_not_loaded_without_code_or_spec(self):
        """naming-contract should NOT load when no code AND no spec doc."""
        signals = {
            "languages": [],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "naming-contract" not in result

    def test_test_quality_loaded_when_test_files_detected(self):
        """test-quality should load when has_test_files is True."""
        signals = {
            "languages": ["typescript"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": True,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "test-quality" in result

    def test_test_quality_not_loaded_without_test_files(self):
        """test-quality should NOT load when has_test_files is False."""
        signals = {
            "languages": ["typescript"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "test-quality" not in result

    def test_all_three_loaded_for_typical_coding_task(self):
        """Typical coding task with tests should load all 3 new contexts."""
        signals = {
            "languages": ["typescript"],
            "directories": [],
            "session_length": "short",
            "task_hints": ["coding"],
            "has_test_files": True,
            "has_spec_docs": True,
        }
        result = context.suggest_contexts(signals)
        assert "anti-patterns" in result
        assert "naming-contract" in result
        assert "test-quality" in result
        assert "typescript" in result  # language context still present

    def test_priority_order_anti_patterns_before_naming_before_test_quality(self):
        """anti-patterns (P95) should appear before naming-contract (P92) before test-quality (P88)."""
        signals = {
            "languages": ["typescript"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": True,
            "has_spec_docs": True,
        }
        result = context.suggest_contexts(signals)
        anti_idx = result.index("anti-patterns")
        naming_idx = result.index("naming-contract")
        test_idx = result.index("test-quality")
        assert anti_idx < naming_idx < test_idx, \
            f"Priority order wrong: {result}"


# ── Backward compatibility ─────────────────────────────────────

class TestBackwardCompat:
    """Existing signal dict format (without new keys) should still work."""

    def test_suggest_contexts_with_old_format(self):
        """Old signal dict (without has_test_files/has_spec_docs) should not crash."""
        # Simulate old-format signals dict
        old_signals = {
            "languages": ["python"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
        }
        # Should not raise KeyError
        result = context.suggest_contexts(old_signals)
        # anti-patterns and naming-contract still loaded (based on languages)
        assert "anti-patterns" in result
        assert "naming-contract" in result
        # test-quality NOT loaded (has_test_files defaults to falsy via .get())
        assert "test-quality" not in result

    def test_suggest_contexts_empty_old_format(self):
        """Empty old-format signals should not crash.

        Phase 2 update: flash-role is always loaded (constitutional layer),
        so even empty signals return ['flash-role'].
        """
        old_signals = {
            "languages": [],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
        }
        result = context.suggest_contexts(old_signals)
        # Phase 2: flash-role always loaded; nothing else
        assert result == ["flash-role"]


# ── Composer integration ───────────────────────────────────────

class TestComposerIntegration:
    """Verify composer.py can load the 3 new context prompt files."""

    def test_list_contexts_includes_new_contexts(self):
        """composer.list_contexts() should include the 3 new contexts."""
        contexts = composer.list_contexts()
        assert "anti-patterns" in contexts
        assert "naming-contract" in contexts
        assert "test-quality" in contexts

    def test_load_anti_patterns_prompt(self):
        """composer should be able to load anti-patterns.md."""
        path = composer.PROMPTS_DIR / "contexts" / "anti-patterns.md"
        content = composer.load_prompt(path)
        assert content  # non-empty
        assert "Anti-Pattern Blacklist" in content
        assert "Engineering Anti-patterns" in content

    def test_load_naming_contract_prompt(self):
        """composer should be able to load naming-contract.md."""
        path = composer.PROMPTS_DIR / "contexts" / "naming-contract.md"
        content = composer.load_prompt(path)
        assert content
        assert "Naming Contract" in content
        assert "GamePhase" in content  # key example from source doc

    def test_load_test_quality_prompt(self):
        """composer should be able to load test-quality.md."""
        path = composer.PROMPTS_DIR / "contexts" / "test-quality.md"
        content = composer.load_prompt(path)
        assert content
        assert "Test Quality Gate" in content
        assert "toContain" in content  # key banned pattern

    def test_compose_with_new_contexts(self):
        """compose() should accept the 3 new contexts and include them in output."""
        result = composer.compose(
            profile="default",
            task="coding",
            contexts=["anti-patterns", "naming-contract", "test-quality"],
        )
        assert "Anti-Pattern Blacklist" in result["prompt"]
        assert "Naming Contract" in result["prompt"]
        assert "Test Quality Gate" in result["prompt"]
        # Sources should include all 3 context files
        sources_str = " ".join(result["sources"])
        assert "anti-patterns.md" in sources_str
        assert "naming-contract.md" in sources_str
        assert "test-quality.md" in sources_str

    def test_compose_metadata_records_contexts(self):
        """compose() metadata should record all loaded contexts."""
        result = composer.compose(
            profile="default",
            task="coding",
            contexts=["anti-patterns", "naming-contract"],
        )
        assert "anti-patterns" in result["metadata"]["contexts"]
        assert "naming-contract" in result["metadata"]["contexts"]


# ── End-to-end: signals → contexts → compose ──────────────────

class TestEndToEndDetectionToCompose:
    """End-to-end: file paths → detect signals → suggest contexts → compose."""

    def test_typical_coding_task_with_tests(self):
        """Simulate: user opens foo.ts and foo.test.ts → all 3 contexts load."""
        # Simulate file paths in working set
        src_file = str(PROJECT_ROOT / "src" / "foo.ts")
        test_file = str(PROJECT_ROOT / "src" / "foo.test.ts")
        spec_file = str(PROJECT_ROOT / "specs" / "foo.spec.md")

        # Step 1: detect signals
        signals = context.detect_context_signals(
            "implement foo feature",
            file_paths=[src_file, test_file, spec_file],
            turn_count=3,
        )
        assert "typescript" in signals["languages"]
        assert signals["has_test_files"] is True
        assert signals["has_spec_docs"] is True
        assert "coding" in signals["task_hints"]

        # Step 2: suggest contexts
        suggested = context.suggest_contexts(signals)
        assert "anti-patterns" in suggested
        assert "naming-contract" in suggested
        assert "test-quality" in suggested
        assert "typescript" in suggested

        # Step 3: compose with suggested contexts
        result = composer.compose(
            profile="default",
            task=context.suggest_task(signals),
            contexts=suggested,
        )
        # All 3 new prompts should be in the composed output
        assert "Anti-Pattern Blacklist" in result["prompt"]
        assert "Naming Contract" in result["prompt"]
        assert "Test Quality Gate" in result["prompt"]
        # TypeScript context should also be loaded
        assert "TypeScript Context" in result["prompt"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
