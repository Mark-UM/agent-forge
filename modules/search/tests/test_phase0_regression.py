#!/usr/bin/env python3
"""Phase 0 Bug Fix Regression Tests

Regression coverage for the 6 critical bugs fixed in Phase 0 of the
Coding Discipline Upgrade plan:

  B1 (CRITICAL): orchestrator._redact_pii_impl — redact_outbound returns
                  tuple but code called .get() → PII bypassed in production
  B2 (HIGH):     search._save_cache — non-atomic write could corrupt cache
  B3 (HIGH):     daemon._validate_screenshot_path — startswith prefix check
                  allowed path traversal (e.g., MODULE_DIR_evil/x.png)
  B4 (MED):      experiments.log_experiment — second-granularity timestamp
                  collision caused duplicate experiment_ids
  B6 (MED):      planner.plan_query — dead use_cache parameter + --no-cache
                  CLI flag promised but never implemented
  PII:           experiments.log_experiment — task_description stored raw,
                  leaking PII to disk

Each test specifically exercises the bug's failure mode pre-fix and verifies
the fix's behavior. Tests use real modules where possible (no over-mocking).
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# experiments.py lives in modules/prompt/
_PROMPT_DIR = os.path.join(_PROJECT_ROOT, 'modules', 'prompt')
if _PROMPT_DIR not in sys.path:
    sys.path.insert(0, _PROMPT_DIR)

# daemon.py lives in modules/browser/
_BROWSER_DIR = os.path.join(_PROJECT_ROOT, 'modules', 'browser')
if _BROWSER_DIR not in sys.path:
    sys.path.insert(0, _BROWSER_DIR)


# ── B1: PII bypass in orchestrator ─────────────────────────────

class TestB1PiiBypassOrchestrator(unittest.TestCase):
    """B1 regression: ensure PII is actually redacted, not silently bypassed.

    Pre-fix: redact_outbound returns tuple, code called .get() → AttributeError
    → fallback path → original_query used as-is → PII leaked to search MCPs.
    """

    def test_real_privacy_module_redacts_email(self):
        """End-to-end with REAL privacy module (not mocked).

        This test would have FAILED pre-fix because the AttributeError in
        orchestrator's try/except fell back to original_query.
        """
        from orchestrator import SearchOrchestrator
        orch = SearchOrchestrator('contact me at mark@example.com please')
        orch.redact_pii()
        # PII MUST be redacted (not original)
        self.assertNotIn('mark@example.com', orch.query)
        self.assertIn('[REDACTED-EMAIL]', orch.query)
        report = orch.step_reports['redact_pii']
        self.assertTrue(report['pii_found'])
        self.assertGreater(report['redacted_count'], 0)

    def test_real_privacy_module_redacts_phone(self):
        """End-to-end: Chinese phone number redaction."""
        from orchestrator import SearchOrchestrator
        orch = SearchOrchestrator('call 13800138000 for info')
        orch.redact_pii()
        self.assertNotIn('13800138000', orch.query)
        self.assertIn('[REDACTED-PHONE]', orch.query)

    def test_no_attribute_error_swallowed(self):
        """Verify the orchestrator no longer swallows AttributeError silently.

        Pre-fix path: redacted = redact_outbound(...) → tuple
                     redacted.get('redacted_query', ...) → AttributeError
                     except Exception: self.query = self.original_query
                     → PII leaked.

        Post-fix path: redacted_query, metadata = redact_outbound(...)
                       self.query = redacted_query
                       → PII redacted.
        """
        from orchestrator import SearchOrchestrator
        orch = SearchOrchestrator('email: test@domain.com')
        orch.redact_pii()
        # The query should NOT equal original (PII was present)
        self.assertNotEqual(orch.query, 'email: test@domain.com')
        # And the warning list should NOT contain a PII redact failure
        pii_failures = [w for w in orch.warnings if 'PII redact failed' in w]
        self.assertEqual(pii_failures, [],
                         f"PII redact failed silently: {pii_failures}")


# ── B2: Atomic cache write ─────────────────────────────────────

class TestB2AtomicCacheWrite(unittest.TestCase):
    """B2 regression: cache write must be atomic (temp + replace).

    Pre-fix: direct open(CACHE_FILE, 'w') + json.dump — crash mid-write
             leaves truncated/empty cache file → all subsequent reads fail.
    Post-fix: write to .tmp file, then os.replace (atomic on Win/POSIX).
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='b2_test_')
        self.cache_file = os.path.join(self.tmp_dir, 'cache.json')

    def test_atomic_write_creates_valid_file(self):
        """Normal write should produce a valid JSON file."""
        # Import search with patched CACHE_FILE
        with patch('search.CACHE_FILE', self.cache_file), \
             patch('search._ensure_log_dir'):
            import search
            search._save_cache({'key': 'value', 'nested': {'a': 1}})
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(data['key'], 'value')
            self.assertEqual(data['nested']['a'], 1)

    def test_no_tmp_file_left_after_success(self):
        """Successful write should not leave .tmp residue."""
        with patch('search.CACHE_FILE', self.cache_file), \
             patch('search._ensure_log_dir'):
            import search
            search._save_cache({'k': 'v'})
            tmp_path = self.cache_file + '.tmp'
            self.assertFalse(os.path.exists(tmp_path),
                             f"Temp file left behind: {tmp_path}")

    def test_tmp_file_cleaned_on_failure(self):
        """If os.replace fails, temp file should be cleaned up."""
        with patch('search.CACHE_FILE', self.cache_file), \
             patch('search._ensure_log_dir'), \
             patch('search.os.replace', side_effect=OSError('mock replace fail')):
            import search
            # Should not raise
            search._save_cache({'k': 'v'})
            # Temp file should be cleaned up
            tmp_path = self.cache_file + '.tmp'
            self.assertFalse(os.path.exists(tmp_path))


# ── B3: Screenshot path traversal ──────────────────────────────

class TestB3ScreenshotPathTraversal(unittest.TestCase):
    """B3 regression: path traversal via startswith prefix check.

    Pre-fix: abs_path.startswith(allowed) returns True for
             MODULE_DIR + "_evil/x.png" — path traversal.
    Post-fix: realpath + strict os.sep-aware prefix check.

    Note: in production, SCREENSHOT_ALLOWED_DIRS contains both MODULE_DIR
    and PROJECT_ROOT. To isolate the startswith bug, we patch
    SCREENSHOT_ALLOWED_DIRS to a single controlled test directory.
    """

    def setUp(self):
        self.tmp_allowed = tempfile.mkdtemp(prefix='b3_allowed_')
        self.tmp_outside = tempfile.mkdtemp(prefix='b3_outside_')

    def test_legitimate_subpath_passes(self):
        """A path directly inside an allowed dir should pass."""
        import daemon
        legit_path = os.path.join(self.tmp_allowed, 'screenshot.png')
        with patch.object(daemon, 'SCREENSHOT_ALLOWED_DIRS', [self.tmp_allowed]):
            result = daemon._validate_screenshot_path(legit_path)
        self.assertEqual(os.path.realpath(result), os.path.realpath(legit_path))

    def test_traversal_via_suffix_underscore_rejected(self):
        """{allowed_dir}_evil/x.png must NOT pass as subpath of allowed_dir.

        This is the exact pre-fix bug: startswith(allowed_dir) returned True
        because '{allowed}_evil' starts with '{allowed}'.
        """
        import daemon
        evil_path = self.tmp_allowed + '_evil/x.png'
        # Ensure the evil path is NOT inside any production allowed dir
        # by patching to only our test dir
        with patch.object(daemon, 'SCREENSHOT_ALLOWED_DIRS', [self.tmp_allowed]):
            with self.assertRaises(ValueError) as ctx:
                daemon._validate_screenshot_path(evil_path)
        self.assertIn('不在允许的目录内', str(ctx.exception))

    def test_dotdot_traversal_rejected(self):
        """../escape/x.png from inside allowed dir must be rejected."""
        import daemon
        # Build path that escapes the allowed dir via ..
        evil_path = os.path.join(self.tmp_allowed, '..', os.path.basename(self.tmp_outside), 'evil.png')
        with patch.object(daemon, 'SCREENSHOT_ALLOWED_DIRS', [self.tmp_allowed]):
            with self.assertRaises(ValueError):
                daemon._validate_screenshot_path(evil_path)

    def test_empty_path_returns_default(self):
        """Empty path should fall back to default screenshot location."""
        import daemon
        # Use the production MODULE_DIR as default (no patch)
        result = daemon._validate_screenshot_path('')
        self.assertEqual(
            os.path.realpath(result),
            os.path.realpath(os.path.join(daemon.MODULE_DIR, 'screenshot.png')))


# ── B4: Experiment ID collision ────────────────────────────────

class TestB4ExperimentIdCollision(unittest.TestCase):
    """B4 regression: same-second log_experiment calls must have unique IDs.

    Pre-fix: experiment_id = f"exp-{int(datetime.now().timestamp())}"
             → same-second calls share ID → stats dedup loses records.
    Post-fix: experiment_id = f"exp-{ts}-{uuid4_hex[:4]}" → unique.
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='b4_test_')
        self.tmp_path_obj = Path(self.tmp_dir)
        self.exp_file = self.tmp_path_obj / 'experiments.jsonl'

    def test_same_second_logs_have_unique_ids(self):
        """Rapid successive calls must produce unique experiment_ids."""
        with patch('experiments.EXPERIMENTS_PATH', self.exp_file), \
             patch('experiments.RUNTIME_DIR', self.tmp_path_obj):
            import experiments
            ids = []
            for _ in range(5):
                eid = experiments.log_experiment(
                    task_type='coding',
                    profile='default',
                    task_description='rapid fire test',
                )
                ids.append(eid)
            # All 5 IDs must be unique
            self.assertEqual(len(ids), len(set(ids)),
                             f"Duplicate experiment_ids: {ids}")

    def test_id_format_includes_timestamp_and_suffix(self):
        """ID format should be exp-{timestamp}-{4-hex}."""
        with patch('experiments.EXPERIMENTS_PATH', self.exp_file), \
             patch('experiments.RUNTIME_DIR', self.tmp_path_obj):
            import experiments
            eid = experiments.log_experiment(
                task_type='coding', profile='default',
                task_description='format check',
            )
            # exp-<digits>-<4 hex chars>
            parts = eid.split('-')
            self.assertEqual(len(parts), 3)
            self.assertEqual(parts[0], 'exp')
            self.assertTrue(parts[1].isdigit())
            self.assertEqual(len(parts[2]), 4)
            int(parts[2], 16)  # must be valid hex


# ── PII: Experiments free-text redaction ───────────────────────

class TestPiiRedactionInExperiments(unittest.TestCase):
    """PII regression: task_description and user_feedback must be redacted."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix='pii_test_')
        self.tmp_path_obj = Path(self.tmp_dir)
        self.exp_file = self.tmp_path_obj / 'experiments.jsonl'

    def test_email_redacted_in_description(self):
        """Email in task_description should be replaced with [REDACTED-EMAIL]."""
        with patch('experiments.EXPERIMENTS_PATH', self.exp_file), \
             patch('experiments.RUNTIME_DIR', self.tmp_path_obj):
            import experiments
            raw_desc = 'reach me at attacker@evil.com for details'
            experiments.log_experiment(
                task_type='coding', profile='default',
                task_description=raw_desc,
            )
            with open(self.exp_file, 'r', encoding='utf-8') as f:
                record = json.loads(f.readline())
            self.assertNotIn('attacker@evil.com', record['task_description'])
            self.assertIn('[REDACTED-EMAIL]', record['task_description'])
            self.assertIn('email', record['pii_redaction']['description_patterns'])

    def test_phone_redacted_in_feedback(self):
        """Chinese phone in user_feedback should be replaced."""
        with patch('experiments.EXPERIMENTS_PATH', self.exp_file), \
             patch('experiments.RUNTIME_DIR', self.tmp_path_obj):
            import experiments
            raw_feedback = 'call me 13912345678 later'
            experiments.log_experiment(
                task_type='coding', profile='default',
                task_description='test',
                user_feedback=raw_feedback,
            )
            with open(self.exp_file, 'r', encoding='utf-8') as f:
                record = json.loads(f.readline())
            self.assertNotIn('13912345678', record['user_feedback'])
            self.assertIn('phone_cn', record['pii_redaction']['feedback_patterns'])

    def test_no_pii_leaks_to_disk(self):
        """Both description and feedback must not contain raw PII on disk."""
        with patch('experiments.EXPERIMENTS_PATH', self.exp_file), \
             patch('experiments.RUNTIME_DIR', self.tmp_path_obj):
            import experiments
            experiments.log_experiment(
                task_type='coding', profile='default',
                task_description='email: a@b.com phone: 13800138000',
                user_feedback='contact: x@y.com',
            )
            with open(self.exp_file, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            self.assertNotIn('a@b.com', raw_content)
            self.assertNotIn('13800138000', raw_content)
            self.assertNotIn('x@y.com', raw_content)


# ── B6: Dead use_cache parameter removed ───────────────────────

class TestB6DeadUseCacheRemoved(unittest.TestCase):
    """B6 regression: dead use_cache parameter and --no-cache flag removed.

    Pre-fix: plan_query(use_cache=True) accepted but never read; --no-cache
             CLI flag existed but did nothing. Confusing API surface.
    Post-fix: parameter and CLI flag removed; cleaner signature.
    """

    def test_plan_query_signature_has_no_use_cache(self):
        """plan_query should NOT accept use_cache anymore."""
        import inspect
        import planner
        sig = inspect.signature(planner.plan_query)
        self.assertNotIn('use_cache', sig.parameters,
                         "use_cache parameter should be removed")

    def test_plan_query_accepts_remaining_params(self):
        """plan_query should still accept the legitimate params."""
        import inspect
        import planner
        sig = inspect.signature(planner.plan_query)
        expected = {'user_query', 'api_key', 'timeout', 'planner_mode', 'max_subqueries'}
        self.assertEqual(set(sig.parameters.keys()), expected)

    def test_calling_with_use_cache_raises_typeerror(self):
        """Passing use_cache should now raise TypeError (signature change)."""
        import planner
        with self.assertRaises(TypeError):
            # use_cache is no longer a valid param
            planner.plan_query('test query', use_cache=False)


# ── Smoke: ensure imports still work ───────────────────────────

class TestImportsIntact(unittest.TestCase):
    """Verify all modified modules still import cleanly."""

    def test_import_orchestrator(self):
        import orchestrator  # noqa: F401

    def test_import_search(self):
        import search  # noqa: F401

    def test_import_planner(self):
        import planner  # noqa: F401

    def test_import_experiments(self):
        # Re-import fresh to ensure no stale state
        import importlib
        if 'experiments' in sys.modules:
            importlib.reload(sys.modules['experiments'])
        else:
            import experiments  # noqa: F401

    def test_import_daemon(self):
        import daemon  # noqa: F401


if __name__ == '__main__':
    unittest.main(verbosity=2)
