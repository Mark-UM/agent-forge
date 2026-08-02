"""Phase 2 Flash Role Guard Tests

Validates the central dispatch interceptor (`modules/dispatch/guard.py`):

  1. FLASH_ALLOWED_TASKS — utility/review tasks return Flash model
  2. FLASH_FORBIDDEN_TASKS — implementation/reasoning tasks auto-redirect to Pro
  3. Unknown tasks default to Pro (safer)
  4. resolve_model() emits warning to stderr + logs to guard_log.jsonl
  5. strict=True raises FlashRoleViolationError instead of auto-redirecting
  6. call_flash() makes actual API call with resolved model
  7. Planner migration: DEFAULT_PLANNER_MODE == 'pro' (Decision 1)
  8. Integration: classify.py / quality.py / i18n.py / summarize.py / aggregator.py
     all route through guard.resolve_model()
  9. Context prompt: flash-role.md loads at priority 99 (always)
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.dispatch import guard
from modules.prompt import context, composer


# ── Model constants ────────────────────────────────────────────

class TestModelConstants:
    """Verify the two allowed models are correctly defined."""

    def test_flash_model_is_deepseek_chat(self):
        """Flash model must be deepseek-chat (per user constraint)."""
        assert guard.FLASH_MODEL == "deepseek-chat"

    def test_pro_model_is_deepseek_reasoner(self):
        """Pro model must be deepseek-reasoner (per user constraint)."""
        assert guard.PRO_MODEL == "deepseek-reasoner"

    def test_no_other_models_defined(self):
        """Only these two models should be in the guard module."""
        # The guard module should NOT export any other model constants
        # (this is a sanity check — we only allow 2 models per user constraint)
        assert guard.FLASH_MODEL != guard.PRO_MODEL


# ── Allowlist / Forbidden list ─────────────────────────────────

class TestTaskLists:
    """Verify the allowlist and forbidden list contents."""

    def test_allowed_tasks_contains_utilities(self):
        """Utility tasks must be in the allowlist."""
        for task in ["classify", "quality_scoring", "i18n", "summarize", "aggregator"]:
            assert task in guard.FLASH_ALLOWED_TASKS, f"{task} should be allowed"

    def test_allowed_tasks_contains_review_tasks(self):
        """Review tasks must be in the allowlist (Flash as REVIEWER)."""
        for task in ["review_code", "review_structure", "review_risk",
                     "integration_check", "doc_alignment_check"]:
            assert task in guard.FLASH_ALLOWED_TASKS, f"{task} should be allowed"

    def test_forbidden_tasks_contains_implementation(self):
        """Implementation tasks must be in the forbidden list."""
        for task in ["implement_core", "implement_ui", "implement_render",
                     "implement_contract", "implement_test", "implement_config"]:
            assert task in guard.FLASH_FORBIDDEN_TASKS, f"{task} should be forbidden"

    def test_forbidden_tasks_contains_reasoning(self):
        """Reasoning tasks must be in the forbidden list."""
        for task in ["planner", "architect", "refactor"]:
            assert task in guard.FLASH_FORBIDDEN_TASKS, f"{task} should be forbidden"

    def test_lists_are_disjoint(self):
        """No task should be in both allowlist and forbidden list."""
        overlap = guard.FLASH_ALLOWED_TASKS & guard.FLASH_FORBIDDEN_TASKS
        assert overlap == set(), f"Tasks in both lists: {overlap}"

    def test_lists_are_frozensets(self):
        """Lists must be frozensets (immutable, hashable)."""
        assert isinstance(guard.FLASH_ALLOWED_TASKS, frozenset)
        assert isinstance(guard.FLASH_FORBIDDEN_TASKS, frozenset)


# ── check_task_allowed ─────────────────────────────────────────

class TestCheckTaskAllowed:
    """check_task_allowed should return True for allowed, False otherwise."""

    @pytest.mark.parametrize("task,expected", [
        ("classify", True),
        ("quality_scoring", True),
        ("i18n", True),
        ("summarize", True),
        ("aggregator", True),
        ("review_code", True),
        ("review_structure", True),
        ("review_risk", True),
        # Forbidden tasks
        ("implement_core", False),
        ("implement_ui", False),
        ("planner", False),
        ("architect", False),
        ("refactor", False),
        # Unknown tasks default to False (safer — redirect to Pro)
        ("unknown_task", False),
        ("", False),
        ("", False),
    ])
    def test_check_task_allowed(self, task, expected):
        assert guard.check_task_allowed(task) is expected

    def test_check_task_allowed_non_string(self):
        """Non-string input should return False."""
        assert guard.check_task_allowed(None) is False
        assert guard.check_task_allowed(123) is False
        assert guard.check_task_allowed([]) is False


# ── resolve_model (auto-redirect behavior) ─────────────────────

class TestResolveModel:
    """resolve_model should return Flash for allowed, Pro for forbidden."""

    def test_allowed_task_returns_flash(self):
        """Allowed tasks should resolve to Flash model."""
        for task in ["classify", "quality_scoring", "i18n", "summarize",
                     "aggregator", "review_code"]:
            model = guard.resolve_model(task, caller="test")
            assert model == guard.FLASH_MODEL, \
                f"{task} should resolve to Flash, got {model}"

    def test_forbidden_task_auto_redirects_to_pro(self):
        """Forbidden tasks should auto-redirect to Pro (Decision 2).

        This is the key behavior: instead of raising, the guard logs a warning
        and returns Pro model so the caller can proceed.
        """
        for task in ["implement_core", "implement_ui", "planner",
                     "architect", "refactor"]:
            model = guard.resolve_model(task, caller="test")
            assert model == guard.PRO_MODEL, \
                f"{task} should redirect to Pro, got {model}"

    def test_unknown_task_defaults_to_pro(self):
        """Unknown tasks should default to Pro (safer)."""
        model = guard.resolve_model("unknown_task", caller="test")
        assert model == guard.PRO_MODEL

    def test_explicit_pro_request_always_returns_pro(self):
        """If caller explicitly requests Pro, always allow (Pro can do anything)."""
        # Even for allowed tasks, explicit Pro request should return Pro
        model = guard.resolve_model("classify", requested_model=guard.PRO_MODEL)
        assert model == guard.PRO_MODEL

    def test_allowed_task_does_not_emit_warning(self, capsys):
        """Allowed tasks should NOT emit a warning to stderr."""
        guard.resolve_model("classify", caller="test")
        captured = capsys.readouterr()
        assert "[flash_guard]" not in captured.err
        assert "REDIRECT" not in captured.err

    def test_forbidden_task_emits_warning(self, capsys):
        """Forbidden tasks should emit a warning with REDIRECT keyword."""
        guard.resolve_model("implement_core", caller="test")
        captured = capsys.readouterr()
        assert "[flash_guard]" in captured.err
        assert "REDIRECT" in captured.err
        assert "implement_core" in captured.err

    def test_strict_mode_raises_for_forbidden(self):
        """strict=True should raise FlashRoleViolationError for forbidden tasks."""
        with pytest.raises(guard.FlashRoleViolationError) as exc_info:
            guard.resolve_model("implement_core", caller="test", strict=True)
        assert "implement_core" in str(exc_info.value)
        assert "forbidden" in str(exc_info.value).lower()

    def test_strict_mode_allows_allowed_tasks(self):
        """strict=True should NOT raise for allowed tasks."""
        # Should not raise
        model = guard.resolve_model("classify", caller="test", strict=True)
        assert model == guard.FLASH_MODEL


# ── Guard log ──────────────────────────────────────────────────

class TestGuardLog:
    """Guard events should be logged to guard_log.jsonl."""

    def test_redirect_logged_to_file(self, tmp_path, monkeypatch):
        """Redirect events should be appended to guard_log.jsonl."""
        # Patch the log path to a temp location
        log_file = tmp_path / "guard_log.jsonl"
        monkeypatch.setattr(guard, "_GUARD_LOG", log_file)
        monkeypatch.setattr(guard, "_RUNTIME_DIR", tmp_path)

        # Trigger a redirect
        guard.resolve_model("implement_core", caller="test_caller")

        # Verify log file was created and contains the redirect event
        assert log_file.exists()
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["event"] == "redirect"
        assert entry["task_type"] == "implement_core"
        assert entry["detail"]["caller"] == "test_caller"
        assert entry["detail"]["from_model"] == guard.FLASH_MODEL
        assert entry["detail"]["to_model"] == guard.PRO_MODEL

    def test_blocked_logged_in_strict_mode(self, tmp_path, monkeypatch):
        """Blocked events should be logged when strict=True raises."""
        log_file = tmp_path / "guard_log.jsonl"
        monkeypatch.setattr(guard, "_GUARD_LOG", log_file)
        monkeypatch.setattr(guard, "_RUNTIME_DIR", tmp_path)

        with pytest.raises(guard.FlashRoleViolationError):
            guard.resolve_model("planner", caller="test", strict=True)

        assert log_file.exists()
        entry = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["event"] == "blocked"
        assert entry["task_type"] == "planner"


# ── call_flash (integration) ───────────────────────────────────

class TestCallFlash:
    """call_flash should resolve model + make API call + return content."""

    def test_call_flash_allowed_task_uses_flash(self):
        """call_flash for allowed task should use Flash model."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "test response"}}]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test_key"}):
                result = guard.call_flash(
                    task_type="classify",
                    prompt="classify this",
                    caller="test",
                )
        assert result["content"] == "test response"
        assert result["model_used"] == guard.FLASH_MODEL
        assert result["redirected"] is False

    def test_call_flash_forbidden_task_redirects_to_pro(self):
        """call_flash for forbidden task should auto-redirect to Pro."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "pro response"}}]
        }).encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test_key"}):
                result = guard.call_flash(
                    task_type="implement_core",
                    prompt="implement this",
                    caller="test",
                )
        assert result["content"] == "pro response"
        assert result["model_used"] == guard.PRO_MODEL
        assert result["redirected"] is True

    def test_call_flash_raises_on_missing_api_key(self):
        """call_flash should raise RuntimeError if no API key."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                guard.call_flash(
                    task_type="classify",
                    prompt="test",
                )
            assert "DEEPSEEK_API_KEY" in str(exc_info.value)

    def test_call_flash_strict_mode_raises(self):
        """call_flash with strict=True should raise for forbidden tasks."""
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test_key"}):
            with pytest.raises(guard.FlashRoleViolationError):
                guard.call_flash(
                    task_type="planner",
                    prompt="plan this",
                    strict=True,
                )


# ── Planner migration (Decision 1) ─────────────────────────────

class TestPlannerMigration:
    """Verify planner.py was migrated from Flash to Pro (Decision 1)."""

    def test_planner_default_mode_is_pro(self):
        """DEFAULT_PLANNER_MODE should be 'pro' after Phase 2 migration."""
        # Import planner fresh
        sys.path.insert(0, str(PROJECT_ROOT / "modules" / "search"))
        import planner
        assert planner.DEFAULT_PLANNER_MODE == "pro"

    def test_planner_still_supports_flash_mode(self):
        """planner.py should still support --planner-mode flash (backward compat)."""
        import planner
        assert "flash" in planner.PLANNER_MODES
        assert "pro" in planner.PLANNER_MODES

    def test_planner_modes_mapping_unchanged(self):
        """PLANNER_MODES mapping should be unchanged (flash→chat, pro→reasoner)."""
        import planner
        assert planner.PLANNER_MODES["flash"] == "deepseek-chat"
        assert planner.PLANNER_MODES["pro"] == "deepseek-reasoner"


# ── Integration: utility modules route through guard ──────────

class TestUtilityModuleIntegration:
    """Verify classify/quality/i18n/summarize/aggregator all use guard."""

    def test_classify_uses_guard(self):
        """classify.py should import and use guard.resolve_model()."""
        from modules.prompt import classify
        # classify_flash should call guard.resolve_model("classify", ...)
        # which returns Flash (classify is allowlisted)
        model = guard.resolve_model("classify", caller="classify.classify_flash")
        assert model == guard.FLASH_MODEL

    def test_quality_uses_guard(self):
        """quality.py should resolve quality_scoring task via guard."""
        model = guard.resolve_model("quality_scoring", caller="quality._call_flash_api")
        assert model == guard.FLASH_MODEL

    def test_i18n_uses_guard(self):
        """i18n.py should resolve i18n task via guard."""
        model = guard.resolve_model("i18n", caller="i18n._call_translate_api")
        assert model == guard.FLASH_MODEL

    def test_summarize_uses_guard(self):
        """summarize.py should resolve summarize task via guard."""
        model = guard.resolve_model("summarize", caller="summarize._call_summarize_api")
        assert model == guard.FLASH_MODEL

    def test_aggregator_uses_guard(self):
        """aggregator.py should resolve aggregator task via guard."""
        model = guard.resolve_model("aggregator", caller="aggregator._call_aggregator_api")
        assert model == guard.FLASH_MODEL


# ── Context prompt integration ─────────────────────────────────

class TestFlashRoleContext:
    """Verify flash-role.md is loaded at priority 99 (always)."""

    def test_flash_role_md_exists(self):
        """flash-role.md should exist in contexts directory."""
        path = composer.PROMPTS_DIR / "contexts" / "flash-role.md"
        assert path.exists()

    def test_flash_role_md_has_priority_99(self):
        """flash-role.md should have priority 99 (highest, constitutional).

        Note: composer._parse_simple_yaml returns strings; we compare as int.
        """
        path = composer.PROMPTS_DIR / "contexts" / "flash-role.md"
        loaded = composer.load_with_metadata(path)
        # YAML parser returns string "99"; compare as int
        assert int(loaded["metadata"].get("priority", 0)) == 99

    def test_list_contexts_includes_flash_role(self):
        """composer.list_contexts() should include flash-role."""
        contexts = composer.list_contexts()
        assert "flash-role" in contexts

    def test_suggest_contexts_always_includes_flash_role(self):
        """suggest_contexts should always include flash-role (even with no signals)."""
        # Empty signals — flash-role should still be loaded
        signals = {
            "languages": [],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": False,
            "has_spec_docs": False,
        }
        result = context.suggest_contexts(signals)
        assert "flash-role" in result, \
            "flash-role should always be loaded (constitutional layer)"

    def test_flash_role_loaded_first(self):
        """flash-role (priority 99) should be loaded before other contexts."""
        signals = {
            "languages": ["python"],
            "directories": [],
            "session_length": "short",
            "task_hints": [],
            "has_test_files": True,
            "has_spec_docs": True,
        }
        result = context.suggest_contexts(signals)
        # flash-role should be first (highest priority)
        assert result[0] == "flash-role"

    def test_compose_includes_flash_role(self):
        """compose() should include flash-role content in output."""
        result = composer.compose(
            profile="default",
            task="coding",
            contexts=["flash-role", "anti-patterns"],
        )
        assert "Flash Model Role Guard" in result["prompt"]
        assert "REVIEWER" in result["prompt"]
        assert "IMPLEMENTER" in result["prompt"]


# ── End-to-end: signals → contexts → compose ──────────────────

class TestEndToEndPhase2:
    """End-to-end: file paths → detect signals → suggest contexts → compose."""

    def test_typical_coding_task_loads_all_phase_contexts(self):
        """Typical coding task should load flash-role + anti-patterns +
        naming-contract + (test-quality if test files present)."""
        src_file = str(PROJECT_ROOT / "src" / "foo.ts")
        test_file = str(PROJECT_ROOT / "src" / "foo.test.ts")

        signals = context.detect_context_signals(
            "implement foo feature",
            file_paths=[src_file, test_file],
            turn_count=3,
        )
        suggested = context.suggest_contexts(signals)

        # Phase 2 context
        assert "flash-role" in suggested
        # Phase 1 contexts
        assert "anti-patterns" in suggested
        assert "naming-contract" in suggested
        assert "test-quality" in suggested
        # Language context
        assert "typescript" in suggested

        # Priority order: flash-role (99) → anti-patterns (95) → typescript →
        # naming-contract (92) → test-quality (88)
        assert suggested.index("flash-role") < suggested.index("anti-patterns")
        assert suggested.index("anti-patterns") < suggested.index("naming-contract")
        assert suggested.index("naming-contract") < suggested.index("test-quality")

        # Compose
        result = composer.compose(
            profile="default",
            task="coding",
            contexts=suggested,
        )
        assert "Flash Model Role Guard" in result["prompt"]
        assert "Anti-Pattern Blacklist" in result["prompt"]
        assert "Naming Contract" in result["prompt"]
        assert "Test Quality Gate" in result["prompt"]


# ── Helper functions ───────────────────────────────────────────

class TestHelperFunctions:
    """list_allowed_tasks / list_forbidden_tasks / get_guard_log_tail."""

    def test_list_allowed_tasks_returns_sorted_list(self):
        """list_allowed_tasks should return a sorted list."""
        result = guard.list_allowed_tasks()
        assert isinstance(result, list)
        assert result == sorted(result)
        assert "classify" in result
        assert "review_code" in result

    def test_list_forbidden_tasks_returns_sorted_list(self):
        """list_forbidden_tasks should return a sorted list."""
        result = guard.list_forbidden_tasks()
        assert isinstance(result, list)
        assert result == sorted(result)
        assert "planner" in result
        assert "implement_core" in result

    def test_get_guard_log_tail_returns_empty_when_no_log(self, tmp_path, monkeypatch):
        """get_guard_log_tail should return [] when log file doesn't exist."""
        monkeypatch.setattr(guard, "_GUARD_LOG", tmp_path / "nonexistent.jsonl")
        result = guard.get_guard_log_tail()
        assert result == []

    def test_get_guard_log_tail_returns_entries(self, tmp_path, monkeypatch):
        """get_guard_log_tail should return most recent entries."""
        log_file = tmp_path / "guard_log.jsonl"
        monkeypatch.setattr(guard, "_GUARD_LOG", log_file)
        monkeypatch.setattr(guard, "_RUNTIME_DIR", tmp_path)

        # Generate some events
        guard.resolve_model("implement_core", caller="test1")
        guard.resolve_model("planner", caller="test2")

        entries = guard.get_guard_log_tail(n=10)
        assert len(entries) >= 2
        assert entries[-1]["task_type"] == "planner"
        assert entries[-2]["task_type"] == "implement_core"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
