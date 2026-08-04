"""Contract tests for modules.search.contracts (Round 2 Phase 1)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from modules.common.result import StepStatus
from modules.search.contracts import (
    LegacySearchAdapter, ProviderCapability, ProviderExecution,
    SearchMode, SearchPipelineResult, SearchPlan, SearchRequest,
    SearchResult, SearchSubQuery, VerificationReport, VerificationStatus,
)


# ── Enums ────────────────────────────────────────────────────

class TestSearchEnums:
    def test_search_mode_values(self):
        assert SearchMode.STANDARD.value == 'standard'
        assert SearchMode.DEEP.value == 'deep'

    def test_verification_status_seven_states(self):
        """R2-2.7: NOT_REQUESTED, NOT_RUN, WEAK_SUPPORT, PARTIALLY_SUPPORTED,
        VERIFIED, CONTRADICTED, ERROR."""
        expected = {'not_requested', 'not_run', 'weak_support',
                    'partially_supported', 'verified', 'contradicted', 'error'}
        actual = {s.value for s in VerificationStatus}
        assert actual == expected

    def test_verification_is_strong_only_verified(self):
        assert VerificationStatus.is_strong(VerificationStatus.VERIFIED)
        assert not VerificationStatus.is_strong(VerificationStatus.WEAK_SUPPORT)
        assert not VerificationStatus.is_strong(VerificationStatus.PARTIALLY_SUPPORTED)

    def test_verification_is_run(self):
        assert VerificationStatus.is_run(VerificationStatus.VERIFIED)
        assert VerificationStatus.is_run(VerificationStatus.WEAK_SUPPORT)
        assert not VerificationStatus.is_run(VerificationStatus.NOT_REQUESTED)
        assert not VerificationStatus.is_run(VerificationStatus.NOT_RUN)


# ── SearchRequest ────────────────────────────────────────────

class TestSearchRequest:
    def test_canonical_field_names(self):
        """R2-2: new code emits sub_queries / max_sub_queries, NOT legacy names."""
        req = SearchRequest(query='test', max_sub_queries=10)
        d = req.to_dict()
        assert 'max_sub_queries' in d
        assert 'max_subqueries' not in d

    def test_default_mode_standard(self):
        req = SearchRequest(query='test')
        assert req.mode == SearchMode.STANDARD

    def test_to_dict_roundtrip(self):
        req = SearchRequest(query='hello', mode=SearchMode.DEEP, language='zh',
                            max_sub_queries=8)
        d = req.to_dict()
        assert d['query'] == 'hello'
        assert d['mode'] == 'deep'
        assert d['language'] == 'zh'
        assert d['max_sub_queries'] == 8


# ── SearchPlan ───────────────────────────────────────────────

class TestSearchPlan:
    def test_plan_emits_sub_queries_canonical(self):
        """R2-2: canonical field is `sub_queries` (NOT `subqueries`)."""
        sq1 = SearchSubQuery(id='sq-001', query='foo', parent_query='foo bar')
        sq2 = SearchSubQuery(id='sq-002', query='bar', parent_query='foo bar')
        plan = SearchPlan(root_query='foo bar', sub_queries=[sq1, sq2])
        d = plan.to_dict()
        assert 'sub_queries' in d
        assert 'subqueries' not in d
        assert len(d['sub_queries']) == 2
        assert d['sub_queries'][0]['id'] == 'sq-001'

    def test_plan_retrieved_at_present(self):
        """R2-2.5: freshness keyed on retrieved_at."""
        plan = SearchPlan(root_query='x', sub_queries=[])
        assert plan.retrieved_at  # not empty


# ── SearchResult ─────────────────────────────────────────────

class TestSearchResult:
    def test_sub_query_id_required(self):
        """R2-2.2: results must carry sub_query_id back to the originating sub-query."""
        r = SearchResult(id='r-1', sub_query_id='sq-001', provider='serper',
                         url='https://example.com')
        d = r.to_dict()
        assert d['sub_query_id'] == 'sq-001'

    def test_retrieved_at_canonical(self):
        """R2-2.5: freshness uses retrieved_at, NOT cached_at."""
        r = SearchResult(id='r-1', sub_query_id='sq-001', provider='serper')
        d = r.to_dict()
        assert 'retrieved_at' in d
        assert 'cached_at' not in d


# ── VerificationReport ───────────────────────────────────────

class TestVerificationReport:
    def test_default_not_run(self):
        """R2-2.7: default state is NOT_RUN, NOT VERIFIED."""
        v = VerificationReport()
        assert v.status == VerificationStatus.NOT_RUN
        assert v.status != VerificationStatus.VERIFIED

    def test_lexical_overlap_does_not_imply_verified(self):
        """R2-2.7: keyword overlap → WEAK_SUPPORT at most, never VERIFIED."""
        v = VerificationReport(
            status=VerificationStatus.WEAK_SUPPORT,
            lexical_overlap_score=0.95,
        )
        assert v.status != VerificationStatus.VERIFIED
        assert v.status == VerificationStatus.WEAK_SUPPORT

    def test_to_dict_includes_lexical_overlap_score(self):
        v = VerificationReport(status=VerificationStatus.WEAK_SUPPORT,
                               lexical_overlap_score=0.5)
        d = v.to_dict()
        assert d['status'] == 'weak_support'
        assert d['lexical_overlap_score'] == 0.5


# ── LegacySearchAdapter ──────────────────────────────────────

class TestLegacySearchAdapter:
    def test_plan_from_dict_accepts_legacy_subqueries_field(self):
        """Read-only: legacy `subqueries` field is accepted on input."""
        legacy = {
            'root_query': 'foo',
            'subqueries': [
                {'query': 'foo', 'rationale': 'r1'},
                {'query': 'bar', 'rationale': 'r2'},
            ],
            'max_subqueries': 7,
            'cached_at': '2026-01-01T00:00:00+00:00',
        }
        plan = LegacySearchAdapter.plan_from_dict(legacy)
        assert plan.root_query == 'foo'
        assert len(plan.sub_queries) == 2
        assert plan.max_sub_queries == 7
        # Canonical field populated from legacy cached_at
        assert plan.retrieved_at == '2026-01-01T00:00:00+00:00'

    def test_plan_from_dict_prefers_canonical_sub_queries(self):
        """If both fields present, canonical `sub_queries` wins."""
        d = {
            'root_query': 'foo',
            'sub_queries': [{'query': 'canonical'}],
            'subqueries': [{'query': 'legacy'}],
        }
        plan = LegacySearchAdapter.plan_from_dict(d)
        assert len(plan.sub_queries) == 1
        assert plan.sub_queries[0].query == 'canonical'

    def test_verification_legacy_verified_becomes_weak_support(self):
        """R2-2.7: legacy `verified=True` from keyword overlap downgrades."""
        legacy = {'verified': True, 'overlap_score': 0.8}
        v = LegacySearchAdapter.verification_status_from_dict(legacy)
        assert v.status == VerificationStatus.WEAK_SUPPORT
        assert v.lexical_overlap_score == 0.8
        assert v.status != VerificationStatus.VERIFIED

    def test_verification_independent_confirmation_promotes_to_verified(self):
        legacy = {'verified': True, 'independent_confirmation': True}
        v = LegacySearchAdapter.verification_status_from_dict(legacy)
        assert v.status == VerificationStatus.VERIFIED

    def test_verification_empty_dict_is_not_requested(self):
        v = LegacySearchAdapter.verification_status_from_dict({})
        assert v.status == VerificationStatus.NOT_REQUESTED

    def test_verification_explicit_status_string_preserved(self):
        legacy = {'verification_status': 'contradicted'}
        v = LegacySearchAdapter.verification_status_from_dict(legacy)
        assert v.status == VerificationStatus.CONTRADICTED


# ── SearchPipelineResult ─────────────────────────────────────

class TestSearchPipelineResult:
    def test_degraded_mode_flag(self):
        """R2-2.8: degraded mode must be explicitly flagged."""
        req = SearchRequest(query='test')
        result = SearchPipelineResult(request=req, degraded_mode=True)
        d = result.to_dict()
        assert d['degraded_mode'] is True

    def test_to_dict_contains_canonical_fields(self):
        req = SearchRequest(query='test')
        result = SearchPipelineResult(request=req)
        d = result.to_dict()
        assert 'request' in d
        assert 'plan' in d
        assert 'results' in d
        assert 'verification' in d
        assert 'retrieved_at' in d
        assert 'cached_at' not in d  # legacy field must not appear


# ── ProviderExecution ────────────────────────────────────────

class TestProviderExecution:
    def test_carries_sub_query_id(self):
        """R2-2.2: provider execution records which sub_query it ran."""
        pe = ProviderExecution(provider='serper', sub_query_id='sq-001')
        d = pe.to_dict()
        assert d['sub_query_id'] == 'sq-001'
        assert d['provider'] == 'serper'
        assert d['status'] == 'pending'
