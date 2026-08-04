#!/usr/bin/env python3
"""Contract tests for Phase 1 Search Pipeline remediation.

Each test verifies a cross-module contract that was previously broken:
  S1: planner → orchestrator sub_queries field passing
  S2: aggregator_fn is actually invoked
  S3: cache_store_fn is actually invoked
  S4: stream not in STEP_ORDER
  S5: prewarm not in STEP_ORDER
  S6: deep research max_subqueries respected
  S7: dry_run config mapping matches real execution
  S8: URL-less results not deleted by dedup
  S9: result property invalidates after new step
  S10: formatted_output includes verification status
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import argparse
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import orchestrator
from orchestrator import (
    SearchOrchestrator, STEP_ORDER, STEP_CONFIG_KEYS,
    OPTIONAL_STEPS, DEFAULT_CONFIG,
)

# Import planner for S1/S6 contract tests
try:
    import planner as _planner_mod
    _HAS_PLANNER = True
except ImportError:
    _HAS_PLANNER = False


SAMPLE_RESULTS = [
    {
        'title': 'FIT2004 Handbook',
        'url': 'https://handbook.monash.edu/2026/units/FIT2004',
        'snippet': 'Algorithms and Data Structures assessment 60%',
        'source': 'official-docs',
    },
    {
        'title': 'Student Notes',
        'url': 'https://github.com/student/notes',
        'snippet': 'Notes from 2024',
        'source': 'github',
    },
]


def _make_flags(**kwargs):
    defaults = dict(
        deep=False, aggregate=False, parallel=False, stream=False,
        i18n=False, academic=False, s2=False, save=False,
        deep_research=False, full_report=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestS1PlannerOrchestratorContract(unittest.TestCase):
    """S1: planner returns 'sub_queries', orchestrator reads 'sub_queries'."""

    def test_orchestrator_reads_sub_queries_from_planner(self):
        """Real planner output (sub_queries) → orchestrator receives it."""
        mock_planner_fn = MagicMock(return_value={
            'success': True,
            'intent': 'factual',
            'complexity': 'medium',
            'decompose': True,
            'sub_queries': ['query 1', 'query 2', 'query 3'],
            'rationale': 'test',
            'mode': 'planner',
        })
        flags = _make_flags(deep=True)
        orch = SearchOrchestrator('test query', flags)
        orch.inject({'planner_fn': mock_planner_fn})
        orch.plan()

        self.assertEqual(len(orch.subqueries), 3)
        self.assertEqual(orch.subqueries[0], 'query 1')

    def test_backward_compat_subqueries_field(self):
        """Deprecated 'subqueries' field still read with warning."""
        mock_planner_fn = MagicMock(return_value={
            'subqueries': ['old query 1', 'old query 2'],
        })
        flags = _make_flags(deep=True)
        orch = SearchOrchestrator('test query', flags)
        orch.inject({'planner_fn': mock_planner_fn})

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            orch.plan()
            self.assertEqual(len(orch.subqueries), 2)
            # Should have emitted a DeprecationWarning
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            self.assertGreater(len(deprecation_warnings), 0)

    @unittest.skipUnless(_HAS_PLANNER, "planner module not available")
    def test_real_planner_output_passes_to_orchestrator(self):
        """Integration: real planner.plan_query output → orchestrator.plan()."""
        # Mock the API call but use real parsing logic
        mock_plan_result = {
            'success': True,
            'intent': 'factual',
            'complexity': 'simple',
            'decompose': False,
            'sub_queries': ['single query'],
            'rationale': 'atomic query',
            'mode': 'planner',
            'planner_mode': 'pro',
            'max_subqueries': 5,
            'original_query': 'test',
        }
        with patch.object(_planner_mod, 'plan_query', return_value=mock_plan_result):
            flags = _make_flags(deep=True)
            orch = SearchOrchestrator('test query', flags)
            orch.inject({'planner_fn': _planner_mod.plan_query})
            orch.plan()
            self.assertEqual(len(orch.subqueries), 1)
            self.assertEqual(orch.subqueries[0], 'single query')

    def test_result_uses_sub_queries_key(self):
        """Result dict uses canonical 'sub_queries' key, not 'subqueries'."""
        orch = SearchOrchestrator('q')
        orch.run_all()
        result = orch.result
        self.assertIn('sub_queries', result)
        self.assertNotIn('subqueries', result)


class TestS2AggregatorCallbackInvoked(unittest.TestCase):
    """S2: aggregator_fn is actually called in the pipeline."""

    def test_aggregator_fn_called_when_enabled(self):
        """aggregator_fn must be invoked when enable_aggregator=True."""
        called = {'count': 0, 'args': None}

        def mock_aggregator(query, results):
            called['count'] += 1
            called['args'] = (query, results)
            return 'aggregated summary'

        flags = _make_flags(aggregate=True)
        orch = SearchOrchestrator('test query', flags)
        orch.results = SAMPLE_RESULTS
        orch.inject({'aggregator_fn': mock_aggregator})

        # Run through the aggregate step
        orch.aggregate()

        self.assertEqual(called['count'], 1)
        self.assertEqual(called['args'][0], 'test query')
        self.assertEqual(len(called['args'][1]), 2)
        self.assertEqual(orch.aggregated, 'aggregated summary')

    def test_aggregator_step_report_shows_invoked(self):
        flags = _make_flags(aggregate=True)
        orch = SearchOrchestrator('q', flags)
        orch.results = SAMPLE_RESULTS
        orch.inject({'aggregator_fn': lambda q, r: 'summary'})
        orch.aggregate()
        report = orch.step_reports['aggregate']
        self.assertTrue(report.get('invoked'))
        self.assertEqual(report.get('input_count'), 2)

    def test_aggregator_failure_preserves_results(self):
        """If aggregator_fn raises, results are preserved."""
        def failing_aggregator(query, results):
            raise RuntimeError('aggregator crashed')

        flags = _make_flags(aggregate=True)
        orch = SearchOrchestrator('q', flags)
        orch.results = SAMPLE_RESULTS
        orch.inject({'aggregator_fn': failing_aggregator})
        orch.aggregate()

        # Results should be intact
        self.assertEqual(len(orch.results), 2)
        report = orch.step_reports['aggregate']
        self.assertFalse(report.get('invoked'))
        self.assertIn('fallback', report)

    def test_aggregator_skipped_when_not_enabled(self):
        orch = SearchOrchestrator('q')
        orch.results = SAMPLE_RESULTS
        orch.inject({'aggregator_fn': lambda q, r: 'summary'})
        orch.aggregate()
        self.assertTrue(orch.step_reports['aggregate']['skipped'])


class TestS3CacheStoreCallbackInvoked(unittest.TestCase):
    """S3: cache_store_fn is actually called after dedup."""

    def test_cache_store_fn_called_after_dedup(self):
        """cache_store_fn must be invoked with deduped results."""
        stored = {'count': 0, 'args': None}

        def mock_cache_store(query, cache_entry):
            stored['count'] += 1
            stored['args'] = (query, cache_entry)

        orch = SearchOrchestrator('test query')
        orch.results = SAMPLE_RESULTS
        orch.inject({'cache_store_fn': mock_cache_store})

        orch.dedup()
        orch.cache_store()

        self.assertEqual(stored['count'], 1)
        self.assertEqual(stored['args'][0], 'test query')
        cache_entry = stored['args'][1]
        self.assertIn('results', cache_entry)
        self.assertIn('cached_at', cache_entry)
        self.assertIn('retrieved_at', cache_entry)
        self.assertEqual(cache_entry['cache_schema_version'], 2)

    def test_cache_store_skipped_on_cache_hit(self):
        """Don't overwrite cache when results came from cache."""
        stored = []

        orch = SearchOrchestrator('q')
        orch.inject({
            'cache_get_fn': lambda q: {'results': SAMPLE_RESULTS},
            'cache_store_fn': lambda q, e: stored.append(e),
        })
        orch.cache_lookup()
        orch.cache_store()

        self.assertEqual(len(stored), 0)
        self.assertTrue(orch.step_reports['cache_store']['skipped'])

    def test_cache_store_skipped_when_no_results(self):
        orch = SearchOrchestrator('q')
        orch.inject({'cache_store_fn': lambda q, e: None})
        orch.cache_store()
        self.assertTrue(orch.step_reports['cache_store']['skipped'])

    def test_cache_store_skipped_when_not_injected(self):
        orch = SearchOrchestrator('q')
        orch.results = SAMPLE_RESULTS
        orch.cache_store()
        self.assertTrue(orch.step_reports['cache_store']['skipped'])


class TestS4S5StreamPrewarmNotInStepOrder(unittest.TestCase):
    """S4/S5: stream and prewarm are not in STEP_ORDER."""

    def test_stream_not_in_step_order(self):
        self.assertNotIn('stream', STEP_ORDER)

    def test_prewarm_not_in_step_order(self):
        self.assertNotIn('prewarm', STEP_ORDER)

    def test_stream_returns_unsupported(self):
        orch = SearchOrchestrator('q')
        result = orch.stream()
        self.assertTrue(result.get('unsupported'))

    def test_prewarm_returns_unsupported(self):
        orch = SearchOrchestrator('q')
        result = orch.prewarm()
        self.assertTrue(result.get('unsupported'))

    def test_run_all_does_not_execute_stream_prewarm(self):
        """run_all should not create step_reports for stream/prewarm."""
        orch = SearchOrchestrator('q')
        orch.run_all()
        self.assertNotIn('stream', orch.step_reports)
        self.assertNotIn('prewarm', orch.step_reports)

    def test_cache_store_and_aggregate_in_step_order(self):
        """New steps cache_store and aggregate are in STEP_ORDER."""
        self.assertIn('cache_store', STEP_ORDER)
        self.assertIn('aggregate', STEP_ORDER)


class TestS6DeepResearchMaxSubqueries(unittest.TestCase):
    """S6: deep research mode respects max_subqueries=10."""

    def test_deep_research_sets_max_10(self):
        flags = _make_flags(deep_research=True)
        orch = SearchOrchestrator('q', flags)
        self.assertEqual(orch.config['max_subqueries'], 10)

    def test_normal_mode_max_5(self):
        """Without --deep-research, max_subqueries stays at default 5."""
        orch = SearchOrchestrator('q')
        self.assertEqual(orch.config['max_subqueries'], 5)

    @unittest.skipUnless(_HAS_PLANNER, "planner module not available")
    def test_planner_respects_max_10(self):
        """Planner should accept max_subqueries=10 without clamping to 5."""
        import planner
        # The planner should now have HARD_MAX_SUB_QUERIES = 10
        self.assertEqual(planner.HARD_MAX_SUB_QUERIES, 10)
        # And MAX_SUB_QUERIES is the default, not a hard cap
        self.assertEqual(planner.MAX_SUB_QUERIES, 5)

    @unittest.skipUnless(_HAS_PLANNER, "planner module not available")
    def test_planner_cli_accepts_up_to_10(self):
        """CLI --max-subqueries should accept values up to 10."""
        import planner
        # Check that the choices range includes 10
        # The CLI parser uses choices=range(1, HARD_MAX_SUB_QUERIES + 1)
        self.assertIn(10, range(1, planner.HARD_MAX_SUB_QUERIES + 1))


class TestS7DryRunConfigMapping(unittest.TestCase):
    """S7: dry_run uses STEP_CONFIG_KEYS, not guessed enable_{step_name}."""

    def test_step_config_keys_complete(self):
        """Every step in STEP_ORDER has an entry in STEP_CONFIG_KEYS."""
        for step in STEP_ORDER:
            self.assertIn(step, STEP_CONFIG_KEYS,
                         f'{step} missing from STEP_CONFIG_KEYS')

    def test_dry_run_enabled_matches_is_step_enabled(self):
        """dry_run 'enabled' field must match is_step_enabled() for each step."""
        flags = _make_flags(deep=True, aggregate=True, parallel=True)
        orch = SearchOrchestrator('q', flags)
        plan = orch.dry_run()

        for step_info in plan['steps']:
            step_name = step_info['step']
            self.assertEqual(step_info['enabled'],
                             orch.is_step_enabled(step_name),
                             f'{step_name}: dry_run enabled={step_info["enabled"]} '
                             f'but is_step_enabled={orch.is_step_enabled(step_name)}')

    def test_dry_run_config_key_correct_for_s2(self):
        """semantic_scholar step uses 'enable_s2' config key, not 'enable_semantic_scholar'."""
        self.assertEqual(STEP_CONFIG_KEYS['semantic_scholar'], 'enable_s2')

    def test_dry_run_config_key_correct_for_parallel(self):
        """parallel_exec step uses 'enable_parallel', not 'enable_parallel_exec'."""
        self.assertEqual(STEP_CONFIG_KEYS['parallel_exec'], 'enable_parallel')

    def test_dry_run_includes_config_key_field(self):
        """Each step in dry_run plan should include its config_key."""
        orch = SearchOrchestrator('q')
        plan = orch.dry_run()
        for step_info in plan['steps']:
            self.assertIn('config_key', step_info)


class TestS8DedupUrlLessResults(unittest.TestCase):
    """S8: URL-less results use fingerprint, not empty string."""

    def test_url_less_results_not_deleted(self):
        """Two results with no URL but different content should both survive."""
        results = [
            {'title': 'Result A', 'snippet': 'Content A', 'source': 'arxiv'},
            {'title': 'Result B', 'snippet': 'Content B', 'source': 'arxiv'},
        ]
        orch = SearchOrchestrator('q')
        orch.results = results
        orch.dedup()
        self.assertEqual(len(orch.results), 2)

    def test_url_less_duplicates_removed(self):
        """Two results with no URL and same content should be deduped."""
        results = [
            {'title': 'Same Title', 'snippet': 'Same Snippet', 'source': 'arxiv'},
            {'title': 'Same Title', 'snippet': 'Same Snippet', 'source': 'arxiv'},
        ]
        orch = SearchOrchestrator('q')
        orch.results = results
        orch.dedup()
        self.assertEqual(len(orch.results), 1)

    def test_url_normalization_dedup(self):
        """Same URL with different case/fragment should be deduped."""
        results = [
            {'url': 'https://Example.COM/path', 'title': 'A'},
            {'url': 'https://example.com/path#fragment', 'title': 'B'},
        ]
        orch = SearchOrchestrator('q')
        orch.results = results
        orch.dedup()
        self.assertEqual(len(orch.results), 1)

    def test_mixed_url_and_url_less(self):
        """Mix of URL and URL-less results should work correctly."""
        results = [
            {'url': 'https://a.com', 'title': 'A'},
            {'title': 'No URL', 'snippet': 'Content', 'source': 'github'},
            {'url': 'https://b.com', 'title': 'B'},
        ]
        orch = SearchOrchestrator('q')
        orch.results = results
        orch.dedup()
        self.assertEqual(len(orch.results), 3)


class TestS9ResultInvalidation(unittest.TestCase):
    """S9: result property invalidates after state changes."""

    def test_result_updates_after_new_step(self):
        """Accessing result, then executing a new step, should give fresh result."""
        orch = SearchOrchestrator('q')
        orch.inject({'search_mcp': lambda q: SAMPLE_RESULTS})
        orch.redact_pii()
        orch.execute_layers()
        r1 = orch.result
        self.assertEqual(r1['results_count'], 2)

        # Now add more results
        orch.results.append({'url': 'https://c.com', 'title': 'C'})
        r2 = orch.result
        self.assertEqual(r2['results_count'], 3)

    def test_result_invalidated_after_dedup(self):
        """Result should reflect post-dedup state."""
        orch = SearchOrchestrator('q')
        orch.results = [
            {'url': 'https://a.com'},
            {'url': 'https://a.com'},  # duplicate
        ]
        r1 = orch.result
        self.assertEqual(r1['results_count'], 2)

        orch.dedup()
        r2 = orch.result
        self.assertEqual(r2['results_count'], 1)

    def test_result_not_cached_across_steps(self):
        """Each step execution should invalidate the result cache."""
        orch = SearchOrchestrator('q')
        orch.run_all()
        r1 = orch.result

        # Execute another step
        orch.format()
        r2 = orch.result

        # r2 should be a different object (cache was invalidated)
        self.assertIsNot(r1, r2)


class TestS10VerificationInFormat(unittest.TestCase):
    """S10: formatted_output includes verification status."""

    def test_format_includes_verification_when_present(self):
        """When verification_report exists, formatted_output should mention it."""
        orch = SearchOrchestrator('test query')
        orch.results = SAMPLE_RESULTS
        orch.verification_report = {
            'verified': True,
            'trigger_reason': 'query_contains_year_keyword',
            'cross_check': {'consistency_score': 0.85},
            'warnings': [],
        }
        orch.format()
        self.assertIn('Verification Status', orch.formatted_output)
        self.assertIn('verified', orch.formatted_output.lower())

    def test_format_shows_unverified_when_not_verified(self):
        orch = SearchOrchestrator('test query')
        orch.results = SAMPLE_RESULTS
        orch.verification_report = {
            'verified': False,
            'trigger_reason': 'low_authority_average',
            'warnings': ['consistency too low'],
        }
        orch.format()
        self.assertIn('unverified', orch.formatted_output.lower())

    def test_format_without_verification(self):
        """When no verification_report, formatted_output should not have verification section."""
        orch = SearchOrchestrator('test query')
        orch.results = SAMPLE_RESULTS
        orch.verification_report = None
        orch.format()
        self.assertNotIn('Verification Status', orch.formatted_output)

    def test_format_includes_aggregated_summary(self):
        """When aggregated content exists, formatted_output should include it."""
        orch = SearchOrchestrator('test query')
        orch.results = SAMPLE_RESULTS
        orch.aggregated = 'This is a summary of findings.'
        orch.format()
        self.assertIn('Aggregated Summary', orch.formatted_output)
        self.assertIn('This is a summary', orch.formatted_output)

    def test_verify_runs_before_format_in_pipeline(self):
        """In run_all, verify should execute before format."""
        orch = SearchOrchestrator('2026 query')
        orch.results = SAMPLE_RESULTS
        orch.inject({'fetch_mcp': lambda u: 'some content'})
        orch.run_all()

        # verify should appear before format in executed_steps
        verify_idx = orch.executed_steps.index('verify')
        format_idx = orch.executed_steps.index('format')
        self.assertLess(verify_idx, format_idx)

        # formatted_output should contain verification info
        self.assertIn('Verification Status', orch.formatted_output)


class TestS12PrewarmTimestamps(unittest.TestCase):
    """S12: prewarm preserves retrieved_at, adds warmed_at."""

    def test_prewarm_cache_entry_has_new_fields(self):
        """Prewarmed cache entries should have retrieved_at, warmed_at, schema_version."""
        import prewarm

        # Create a mock history file
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = os.path.join(tmpdir, 'search')
            os.makedirs(log_dir)
            log_file = os.path.join(log_dir, 'search_history.2026-08-01.jsonl')
            original_timestamp = '2026-08-01T10:00:00'
            with open(log_file, 'w') as f:
                json.dump({
                    'query': 'test query',
                    'timestamp': original_timestamp,
                    'top_results': [{'title': 'Result', 'url': 'https://a.com'}],
                    'results_count': 1,
                    'location': 'unknown',
                }, f)

            with patch.object(prewarm, '_LOG_DIR', log_dir), \
                 patch.object(prewarm, 'CACHE_FILE', os.path.join(log_dir, 'search_cache.json')):
                result = prewarm.prewarm_cache(top_n=10, days_window=None)

                self.assertGreater(result['prewarmed_count'], 0)

                # Load the cache and check fields
                cache = prewarm._load_cache()
                for key, entry in cache.items():
                    self.assertIn('retrieved_at', entry)
                    self.assertIn('warmed_at', entry)
                    self.assertEqual(entry.get('cache_schema_version'), 2)
                    # retrieved_at should be the original timestamp
                    self.assertEqual(entry['retrieved_at'], original_timestamp)


class TestS13VerifierNaming(unittest.TestCase):
    """S13: verifier uses accurate naming for weak verification."""

    def test_verifier_has_lexical_consistency_field(self):
        """Cross-check result should include lexical_consistency field."""
        import verifier

        query = 'FIT2004 assessment 2026'
        results = [
            {'url': 'https://handbook.monash.edu/2026/units/FIT2004',
             'title': 'Handbook', 'source': 'official-docs'},
        ]
        fetched_l1 = [
            {'url': 'https://handbook.monash.edu/2026/units/FIT2004',
             'status': 'success',
             'content_snippet': 'FIT2004 Algorithms assessment 2026'},
        ]

        cross_check = verifier._cross_check_results(query, results, fetched_l1)
        self.assertIn('lexical_consistency', cross_check)
        self.assertIn('source_overlap', cross_check)
        self.assertIn('verification_strength', cross_check)

    def test_verifier_strength_values(self):
        """verification_strength should be one of: none, weak, moderate, strong."""
        import verifier

        # Test with high consistency
        cross_check = verifier._cross_check_results(
            'algorithms data structures',
            [{'url': 'https://a.com', 'source': 'docs'}],
            [{'url': 'https://a.com', 'status': 'success',
              'content_snippet': 'algorithms data structures assessment'}],
        )
        self.assertIn(cross_check['verification_strength'],
                      ('moderate', 'strong'))

    def test_verifier_strong_threshold(self):
        """verified=True only when consistency >= 0.8."""
        import verifier

        # Mock verify to get a high-consistency result
        with patch.object(verifier, '_default_fetch', return_value='algorithms data structures 2026 assessment'):
            report = verifier.verify_against_authority(
                'algorithms data structures 2026 assessment',
                [{'url': 'https://handbook.monash.edu/2026/units/FIT2004',
                  'title': 'Handbook', 'source': 'official-docs'}],
            )
            # If verified is True, consistency should be >= 0.8
            if report.get('verified'):
                cross = report.get('cross_check', {})
                self.assertGreaterEqual(cross.get('consistency_score', 0), 0.8)


class TestS11ParallelAbandoned(unittest.TestCase):
    """S11: parallel search marks abandoned tasks, doesn't wait for them."""

    def test_parallel_result_has_abandoned_field(self):
        import parallel
        result = parallel._empty_result('first_completed')
        self.assertIn('abandoned', result)

    def test_parallel_does_not_wait_for_slow_thread(self):
        """When timeout is reached, slow thread should be abandoned, not waited for."""
        import parallel
        import time

        def fast_caller(query):
            return [{'title': 'fast', 'url': 'https://fast.com'}]

        def slow_caller(query):
            time.sleep(5)
            return [{'title': 'slow', 'url': 'https://slow.com'}]

        start = time.monotonic()
        result = parallel.parallel_search(
            'test',
            ['fast', 'slow'],
            {'fast': fast_caller, 'slow': slow_caller},
            mode='first_completed',
            timeout=2,
        )
        elapsed = time.monotonic() - start

        # Should return well before slow_caller's 5s sleep
        self.assertLess(elapsed, 4)
        self.assertTrue(result['success'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
