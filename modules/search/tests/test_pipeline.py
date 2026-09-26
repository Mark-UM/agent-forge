"""Contract tests for modules.search.pipeline (Round 2 Phase 2.4 / 2.2 / 2.5 / 2.7).

Verifies:
    * Canonical STEP_ORDER per R2-2.4 (no `aggregate_pre`, all 13 steps present)
    * Sub-queries are dispatched to providers (R2-2.2); each result carries
      sub_query_id
    * Cache freshness based on `retrieved_at`, NOT `cached_at`; prewarm does
      not extend freshness (R2-2.5)
    * Verification status model: NOT_RUN ≠ VERIFIED; lexical overlap alone
      yields WEAK_SUPPORT (R2-2.7)
    * URL normalization strips only tracking params (R2-2.6)
    * Typed SearchPipelineResult is returned with OperationResult step reports
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.common.result import OperationResult, StepStatus
from modules.search.contracts import (
    ProviderCapability, ProviderExecution, SearchMode, SearchPlan,
    SearchRequest, SearchResult, SearchSubQuery, VerificationReport,
    VerificationStatus,
)
from modules.search.pipeline import (
    DEFAULT_CACHE_TTL_SECONDS, STEP_ORDER, SearchPipeline,
    cache_entry_fresh,
)
from modules.search.providers import (
    ProviderRegistry, SearchProvider,
)
from modules.search.url_utils import normalize_url


# ── Fake Provider for testing ────────────────────────────────

class FakeProvider:
    """Test double implementing the SearchProvider Protocol.

    Records every call so tests can assert sub_queries were dispatched.
    """
    name = 'fake'
    capabilities = {ProviderCapability.WEB_SEARCH}
    requires_credentials = False
    default_timeout = 5

    def __init__(self, results_per_query: int = 2,
                 fail_on_subquery_id: str | None = None) -> None:
        self.results_per_query = results_per_query
        self.fail_on_subquery_id = fail_on_subquery_id
        self.calls: list[SearchSubQuery] = []

    def search(self, sub_query: SearchSubQuery, deadline: datetime) -> list[SearchResult]:
        self.calls.append(sub_query)
        if self.fail_on_subquery_id and sub_query.id == self.fail_on_subquery_id:
            raise RuntimeError(f"injected failure on {sub_query.id}")
        return [
            SearchResult(
                id=f"{sub_query.id}-r{i}",
                sub_query_id=sub_query.id,
                provider=self.name,
                url=f"https://example.com/{sub_query.id}/{i}?utm_source=x&id={i}",
                title=f"Result {i} for {sub_query.query}",
                snippet=f"snippet {i}",
                raw_metadata={'position': i},
            )
            for i in range(self.results_per_query)
        ]

    def supports_language(self, language: str) -> bool:
        return True


def _make_registry(*providers: SearchProvider) -> ProviderRegistry:
    reg = ProviderRegistry()
    for p in providers:
        reg.register(p)
    return reg


def _make_planner_fn(sub_query_texts: list[str]):
    """Build a planner_fn that returns the given sub-query strings."""
    def planner_fn(query, max_sub_queries=5, mode='standard', language='auto'):
        sqs = [
            {'id': f'sq-{i+1:03d}', 'query': q, 'parent_query': query}
            for i, q in enumerate(sub_query_texts[:max_sub_queries])
        ]
        return {
            'root_query': query,
            'sub_queries': sqs,
            'intent': 'factual',
            'complexity': 'medium',
            'decompose': True,
            'rationale': 'test planner',
            'max_sub_queries': max_sub_queries,
        }
    return planner_fn


# ── R2-2.4: Canonical Step Order ─────────────────────────────

class TestStepOrder:
    def test_step_order_matches_spec(self):
        """R2-2.4: exact step order; `aggregate_pre` removed."""
        assert STEP_ORDER == [
            'validate_request',
            'normalize_query',
            'plan',
            'final_cache_lookup',
            'provider_cache_lookup',
            'provider_execute',
            'normalize_results',
            'deduplicate',
            'rank',
            'aggregate',
            'verify',
            'format',
            'cache_store',
        ]

    def test_aggregate_pre_removed(self):
        """R2-2.4: `aggregate_pre` is NOT in STEP_ORDER."""
        assert 'aggregate_pre' not in STEP_ORDER

    def test_no_dead_steps(self):
        """R2-2.4: every step in STEP_ORDER has a real implementation in
        SearchPipeline."""
        for step in STEP_ORDER:
            # Map step names to method names where they differ
            method_name = 'plan_step' if step == 'plan' else step
            assert hasattr(SearchPipeline, method_name), \
                f"SearchPipeline missing method for step '{step}'"


# ── R2-2.2: Sub-queries really dispatched ────────────────────

class TestSubQueryDispatch:
    def test_each_subquery_dispatched_to_provider(self):
        """R2-2.2: every sub-query emitted by the planner is dispatched to
        a provider. Root-query-only path is deleted."""
        provider = FakeProvider(results_per_query=2)
        request = SearchRequest(query='python asyncio', mode=SearchMode.DEEP,
                                max_sub_queries=3)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['python asyncio', 'asyncio examples',
                                          'asyncio best practices']),
        )
        result = pipe.execute()

        # Provider should have been called once per sub-query
        assert len(provider.calls) == 3
        called_ids = {sq.id for sq in provider.calls}
        assert called_ids == {'sq-001', 'sq-002', 'sq-003'}

    def test_every_result_carries_sub_query_id(self):
        """R2-2.2: every SearchResult carries the sub_query_id that produced it."""
        provider = FakeProvider(results_per_query=2)
        request = SearchRequest(query='test query', max_sub_queries=2)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['sub a', 'sub b']),
        )
        result = pipe.execute()
        for r in result.results:
            assert r.sub_query_id in {'sq-001', 'sq-002'}, \
                f"result {r.id} has bad sub_query_id={r.sub_query_id!r}"

    def test_root_query_only_path_deleted(self):
        """R2-2.2: when the planner returns multiple sub-queries, the
        provider is NOT called with the root query — only with sub-queries."""
        provider = FakeProvider()
        request = SearchRequest(query='root query', max_sub_queries=3)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['sub a', 'sub b', 'sub c']),
        )
        pipe.execute()
        # Provider was never called with the root query
        called_queries = {sq.query for sq in provider.calls}
        assert 'root query' not in called_queries
        assert called_queries == {'sub a', 'sub b', 'sub c'}

    def test_max_sub_queries_ceiling_enforced(self):
        """R2-2.3: planner output is trimmed to request.max_sub_queries."""
        provider = FakeProvider()
        request = SearchRequest(query='test', max_sub_queries=2)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['a', 'b', 'c', 'd', 'e']),
        )
        result = pipe.execute()
        # Only 2 sub-queries should have been dispatched
        assert len(provider.calls) == 2
        assert result.plan is not None
        assert len(result.plan.sub_queries) == 2


# ── R2-2.5: Cache freshness ──────────────────────────────────

class TestCacheFreshness:
    def test_freshness_based_on_retrieved_at(self):
        """R2-2.5: fresh entry (retrieved_at within TTL) → fresh."""
        now = datetime.now(timezone.utc)
        entry = {
            'retrieved_at': now.isoformat(),
            'cached_at': now.isoformat(),
            'warmed_at': None,
        }
        assert cache_entry_fresh(entry) is True

    def test_stale_when_retrieved_at_old(self):
        """R2-2.5: stale when retrieved_at older than TTL."""
        old = datetime.now(timezone.utc) - timedelta(seconds=DEFAULT_CACHE_TTL_SECONDS + 10)
        entry = {'retrieved_at': old.isoformat(), 'cached_at': old.isoformat()}
        assert cache_entry_fresh(entry) is False

    def test_prewarm_does_not_extend_freshness(self):
        """R2-2.5: updating `cached_at` or `warmed_at` to a recent time does
        NOT make a stale entry fresh. Only `retrieved_at` matters."""
        old_retrieved = datetime.now(timezone.utc) - timedelta(
            seconds=DEFAULT_CACHE_TTL_SECONDS + 60)
        recent = datetime.now(timezone.utc)
        entry = {
            'retrieved_at': old_retrieved.isoformat(),  # original fetch time
            'cached_at': recent.isoformat(),             # recently re-written
            'warmed_at': recent.isoformat(),             # prewarm touched it
        }
        assert cache_entry_fresh(entry) is False, \
            "prewarm refreshed cached_at/warmed_at must NOT extend freshness"

    def test_missing_retrieved_at_is_stale(self):
        """R2-2.5: legacy entry without retrieved_at is treated as stale."""
        entry = {'cached_at': datetime.now(timezone.utc).isoformat()}
        assert cache_entry_fresh(entry) is False

    def test_cache_hit_short_circuits_provider(self):
        """R2-2.4/2.5: a fresh final-cache hit means providers are NOT called."""
        provider = FakeProvider()
        now = datetime.now(timezone.utc)
        cache = {
            'final|test query|standard': {
                'query': 'test query',
                'results': [{'url': 'https://cached.example.com',
                             'title': 'cached', 'snippet': 'cached snippet'}],
                'retrieved_at': now.isoformat(),
                'cached_at': now.isoformat(),
            },
        }
        request = SearchRequest(query='test query')
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test query']),
            cache_get_fn=lambda k: cache.get(k),
        )
        result = pipe.execute()
        # Provider should NOT have been called
        assert len(provider.calls) == 0
        # Results came from cache
        assert len(result.results) == 1
        assert result.results[0].url == 'https://cached.example.com'
        # final_cache_lookup reports hit
        assert pipe.step_reports['final_cache_lookup'].data.get('hit') is True


# ── R2-2.7: Verification status model ────────────────────────

class TestVerificationModel:
    def test_no_verifier_yields_not_run_not_verified(self):
        """R2-2.7: when no verify_fn is injected, status is NOT_RUN, not VERIFIED."""
        provider = FakeProvider()
        request = SearchRequest(query='test', verify=True)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
            # no verify_fn
        )
        result = pipe.execute()
        assert result.verification.status == VerificationStatus.NOT_RUN
        assert result.verification.status != VerificationStatus.VERIFIED

    def test_verify_false_yields_not_requested(self):
        """R2-2.7: verify=False → status NOT_REQUESTED."""
        provider = FakeProvider()
        request = SearchRequest(query='test', verify=False)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
        )
        result = pipe.execute()
        assert result.verification.status == VerificationStatus.NOT_REQUESTED

    def test_lexical_overlap_only_yields_weak_support(self):
        """R2-2.7: legacy `verified=True` from keyword overlap maps to
        WEAK_SUPPORT, not VERIFIED."""
        provider = FakeProvider()
        # verify_fn returns legacy dict with verified=True (keyword overlap only)
        def verify_fn(query, results):
            return {'verified': True, 'overlap_score': 0.5}  # legacy form
        request = SearchRequest(query='test', verify=True)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
            verify_fn=verify_fn,
        )
        result = pipe.execute()
        assert result.verification.status == VerificationStatus.WEAK_SUPPORT
        assert result.verification.status != VerificationStatus.VERIFIED

    def test_independent_confirmation_yields_verified(self):
        """R2-2.7: explicit independent_confirmation flag promotes to VERIFIED."""
        provider = FakeProvider()
        def verify_fn(query, results):
            return {'verified': True, 'independent_confirmation': True}
        request = SearchRequest(query='test', verify=True)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
            verify_fn=verify_fn,
        )
        result = pipe.execute()
        assert result.verification.status == VerificationStatus.VERIFIED


# ── R2-2.6: URL normalization in pipeline ────────────────────

class TestPipelineUrlNormalization:
    def test_tracking_params_stripped_in_results(self):
        """R2-2.6: pipeline.normalize_results strips utm_* from URLs."""
        provider = FakeProvider(results_per_query=1)
        request = SearchRequest(query='test')
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
        )
        result = pipe.execute()
        for r in result.results:
            assert 'utm_source' not in r.url
            # Business params preserved
            assert 'id=' in r.url


# ── Typed result / OperationResult ───────────────────────────

class TestTypedResultContract:
    def test_returns_search_pipeline_result_type(self):
        provider = FakeProvider()
        request = SearchRequest(query='test')
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
        )
        result = pipe.execute()
        # Verify all expected fields are present in to_dict output
        d = result.to_dict()
        assert 'request' in d
        assert 'plan' in d
        assert 'results' in d
        assert 'provider_executions' in d
        assert 'verification' in d
        assert 'step_reports' in d
        assert 'warnings' in d
        assert 'degraded_mode' in d
        assert 'retrieved_at' in d

    def test_step_reports_are_operation_result(self):
        """R2: step reports are typed OperationResult, never {enabled: True}."""
        provider = FakeProvider()
        request = SearchRequest(query='test')
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
        )
        pipe.execute()
        for step_name, step_result in pipe.step_reports.items():
            assert isinstance(step_result, OperationResult), \
                f"step {step_name} returned {type(step_result).__name__}"
            assert isinstance(step_result.status, StepStatus)
            # Forbidden: {enabled: True} marker dict
            assert not (isinstance(step_result, dict)
                        and step_result.get('enabled') is True)

    def test_validation_failure_is_fatal(self):
        """R2: validate_request failure stops the pipeline."""
        # Empty query → validation fails
        request = SearchRequest(query='')
        pipe = SearchPipeline(request)
        result = pipe.execute()
        # Only validate_request should be in step_reports
        assert 'validate_request' in pipe.step_reports
        assert pipe.step_reports['validate_request'].success is False
        # No later steps should have run
        assert 'provider_execute' not in pipe.step_reports
        assert 'format' not in pipe.step_reports


class TestPrivacyFailureStopsOutbound:
    @pytest.mark.parametrize('failure', ['reported', 'raised'])
    def test_pipeline_stops_before_planner_and_provider(self, monkeypatch, failure):
        from modules.search import privacy

        provider = FakeProvider()
        planner_calls = []

        def planner(**kwargs):
            planner_calls.append(kwargs)
            return _make_planner_fn(['safe query'])(**kwargs)

        def bad_redaction(query):
            if failure == 'raised':
                raise RuntimeError('redaction unavailable')
            return query, {'redacted_count': 0, 'error': 'redaction unavailable'}

        monkeypatch.setattr(privacy, 'redact_outbound', bad_redaction)
        pipe = SearchPipeline(
            SearchRequest(query='contact 13800138000'),
            registry=_make_registry(provider), planner_fn=planner,
        )
        result = pipe.execute()
        assert pipe.step_reports['normalize_query'].status == StepStatus.FAILED
        assert 'provider_execute' not in pipe.step_reports
        assert planner_calls == []
        assert provider.calls == []
        assert pipe.normalized_query == ''
        assert result.success is False


# ── Degraded mode ────────────────────────────────────────────

class TestDegradedMode:
    def test_no_providers_yields_degraded(self):
        """When the registry is empty, provider_execute reports DEGRADED."""
        request = SearchRequest(query='test')
        pipe = SearchPipeline(
            request,
            registry=ProviderRegistry(),  # empty
            planner_fn=_make_planner_fn(['test']),
        )
        result = pipe.execute()
        assert result.degraded_mode is True
        assert pipe.step_reports['provider_execute'].status == StepStatus.DEGRADED

    def test_provider_failure_recorded_not_fatal(self):
        """Provider exceptions are recorded but don't abort the pipeline."""
        provider = FakeProvider(fail_on_subquery_id='sq-001')
        request = SearchRequest(query='test', max_sub_queries=1)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
        )
        result = pipe.execute()
        # Pipeline completed
        assert 'format' in pipe.step_reports
        assert 'cache_store' in pipe.step_reports
        # Provider execution is recorded as failed
        assert len(result.provider_executions) == 1
        assert result.provider_executions[0].status == StepStatus.FAILED


# ── Provider Execution tracking ──────────────────────────────

class TestProviderExecution:
    def test_provider_execution_carries_sub_query_id(self):
        provider = FakeProvider()
        request = SearchRequest(query='test', max_sub_queries=2)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['a', 'b']),
        )
        result = pipe.execute()
        assert len(result.provider_executions) == 2
        exec_ids = {pe.sub_query_id for pe in result.provider_executions}
        assert exec_ids == {'sq-001', 'sq-002'}

    def test_provider_execution_records_duration(self):
        provider = FakeProvider()
        request = SearchRequest(query='test')
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=_make_planner_fn(['test']),
        )
        result = pipe.execute()
        for pe in result.provider_executions:
            assert pe.duration_ms >= 0.0


# ── End-to-end: real planner-shaped dict → provider ──────────

class TestEndToEndPlannerToProvider:
    def test_real_planner_dict_with_string_subqueries(self):
        """Planner.py returns list[str] in 'sub_queries'. Pipeline must
        convert to typed SearchSubQuery and dispatch each to a provider."""
        provider = FakeProvider()
        # Simulate planner.py's actual return shape (list[str])
        def planner_fn(query, max_sub_queries=5, mode='standard', language='auto'):
            return {
                'success': True,
                'intent': 'factual',
                'complexity': 'medium',
                'decompose': True,
                'sub_queries': ['sub1', 'sub2', 'sub3'],
                'rationale': 'test',
                'mode': 'planner',
                'planner_mode': 'pro',
                'max_subqueries': max_sub_queries,
                'original_query': query,
            }
        request = SearchRequest(query='test', max_sub_queries=3)
        pipe = SearchPipeline(
            request,
            registry=_make_registry(provider),
            planner_fn=planner_fn,
        )
        result = pipe.execute()
        # All 3 string sub-queries should be dispatched
        assert len(provider.calls) == 3
        called_queries = {sq.query for sq in provider.calls}
        assert called_queries == {'sub1', 'sub2', 'sub3'}
        # Each result carries a sub_query_id
        assert all(r.sub_query_id for r in result.results)
