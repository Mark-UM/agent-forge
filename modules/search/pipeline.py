"""modules.search.pipeline — typed Search Pipeline (Round 2 Phase 2.4).

Implements the canonical step order mandated by R2-2.4:

    validate_request
    → normalize_query
    → plan
    → final_cache_lookup
    → provider_cache_lookup
    → provider_execute
    → normalize_results
    → deduplicate
    → rank
    → aggregate
    → verify
    → format
    → cache_store

Key invariants enforced by this module:
    * Each SearchSubQuery produced by the planner is dispatched to a Provider;
      the root-query-only path is deleted (R2-2.2).
    * Every SearchResult carries `sub_query_id` tying it back to the
      SearchSubQuery that produced it (R2-2.2).
    * Cache freshness is keyed on `retrieved_at`, not `cached_at`. Prewarm
      only updates `warmed_at` (R2-2.5).
    * URL normalization strips only tracking params (R2-2.6).
    * Verification uses the 7-state VerificationStatus enum; lexical overlap
      alone yields WEAK_SUPPORT, never VERIFIED (R2-2.7).
    * `aggregate_pre` is removed; no dead steps remain in STEP_ORDER.

The pipeline returns a typed `SearchPipelineResult`. Step reports are typed
`OperationResult` objects — never `{"enabled": True}` markers.

This module is the production path invoked by `pipeline_mcp.search_pipeline`
(R2-2.8). The legacy `orchestrator.SearchOrchestrator` is retained only as a
fallback for the degraded-mode path.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Local imports — modules.* is always present in Round 2+
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules.common.result import ErrorInfo, OperationResult, StepStatus
from modules.search.contracts import (
    LegacySearchAdapter, ProviderCapability, ProviderExecution,
    SearchMode, SearchPipelineResult, SearchPlan, SearchRequest,
    SearchResult, SearchSubQuery, VerificationReport, VerificationStatus,
)
from modules.search.providers import ProviderRegistry, SearchProvider
from modules.search.url_utils import normalize_url, urls_equivalent

# Make sibling search modules importable for legacy adapter fallbacks
_SEARCH_DIR = Path(__file__).resolve().parent
if str(_SEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_SEARCH_DIR))


# ── Canonical Step Order (R2-2.4) ────────────────────────────

STEP_ORDER = [
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

# Mandatory steps always run; optional steps need explicit request flag.
OPTIONAL_STEPS = frozenset({'verify'})

# Cache freshness TTL in seconds (24h). Freshness is computed from
# `retrieved_at`, never from `cached_at` (R2-2.5).
DEFAULT_CACHE_TTL_SECONDS = 24 * 3600


# ── Exceptions ───────────────────────────────────────────────

class PipelineValidationError(Exception):
    """Raised when SearchRequest fails validation (fatal)."""


# ── Cache freshness helpers (R2-2.5) ─────────────────────────

def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def cache_entry_fresh(entry: dict[str, Any], ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> bool:
    """Return True iff the cache entry is fresh per R2-2.5.

    Freshness is computed from `retrieved_at` (the original fetch time).
    `cached_at` and `warmed_at` are NOT freshness bases — prewarm only
    updates `warmed_at` and MUST NOT extend freshness.
    """
    retrieved_at = _parse_iso(entry.get('retrieved_at', ''))
    if retrieved_at is None:
        # Legacy entry without retrieved_at → treat as stale so callers
        # re-fetch and write the canonical field.
        return False
    now = datetime.now(timezone.utc)
    age = (now - retrieved_at).total_seconds()
    return 0 <= age < ttl_seconds


# ── SearchPipeline ───────────────────────────────────────────

class SearchPipeline:
    """Typed search pipeline. Each step returns OperationResult and mutates
    self._state. Run via `execute()` which returns SearchPipelineResult.

    Dependency injection:
        registry         — ProviderRegistry with registered providers
        planner_fn       — callable(query, max_sub_queries, mode, language) -> SearchPlan | dict
        cache_get_fn     — callable(key) -> dict | None  (returns raw cache entry)
        cache_store_fn   — callable(key, entry) -> None
        verify_fn        — callable(query, results) -> VerificationReport | dict
    """

    def __init__(
        self,
        request: SearchRequest,
        *,
        registry: Optional[ProviderRegistry] = None,
        planner_fn: Optional[Callable[..., Any]] = None,
        cache_get_fn: Optional[Callable[[str], Optional[dict]]] = None,
        cache_store_fn: Optional[Callable[[str, dict], None]] = None,
        verify_fn: Optional[Callable[[str, list[SearchResult]], Any]] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self.request = request
        self.registry = registry or ProviderRegistry()
        self._planner_fn = planner_fn
        self._cache_get_fn = cache_get_fn
        self._cache_store_fn = cache_store_fn
        self._verify_fn = verify_fn
        self._cache_ttl = cache_ttl_seconds

        # Pipeline state (mutated by each step)
        self.normalized_query: str = request.query
        self.plan: Optional[SearchPlan] = None
        self.results: list[SearchResult] = []
        self.provider_executions: list[ProviderExecution] = []
        self.verification: VerificationReport = VerificationReport(
            status=VerificationStatus.NOT_REQUESTED if not request.verify
            else VerificationStatus.NOT_RUN
        )
        self.formatted_output: str = ''
        self.degraded_mode: bool = False
        self.warnings: list[str] = []
        self.step_reports: dict[str, OperationResult] = {}

        # Final result cache
        self._result: Optional[SearchPipelineResult] = None

    # ── Helpers ──────────────────────────────────────────────

    def _record_step(self, name: str, result: OperationResult) -> None:
        self.step_reports[name] = result
        # Surface warnings/errors to pipeline-level
        for w in result.warnings:
            self.warnings.append(f"[{name}] {w.message}")
        if result.status == StepStatus.DEGRADED:
            self.degraded_mode = True

    def _deadline(self) -> datetime:
        return self.request.deadline or datetime.now(timezone.utc).replace(
            year=datetime.now(timezone.utc).year + 1
        )

    # ── Step 1: validate_request ────────────────────────────

    def validate_request(self) -> OperationResult:
        """Validate the typed SearchRequest. Fatal on failure."""
        try:
            if not isinstance(self.request, SearchRequest):
                raise PipelineValidationError('request must be SearchRequest')
            if not self.request.query or not self.request.query.strip():
                raise PipelineValidationError('query must be non-empty')
            if self.request.max_sub_queries < 1:
                raise PipelineValidationError('max_sub_queries must be >= 1')
            if not isinstance(self.request.mode, SearchMode):
                raise PipelineValidationError('mode must be SearchMode')
        except PipelineValidationError as e:
            res = OperationResult.failed(code='validation_error', message=str(e),
                                          exception=e, step='validate_request')
            self._record_step('validate_request', res)
            return res
        res = OperationResult.success_with(step='validate_request')
        self._record_step('validate_request', res)
        return res

    # ── Step 2: normalize_query ─────────────────────────────

    def normalize_query(self) -> OperationResult:
        """Normalize whitespace and surface PII redaction (best-effort)."""
        q = self.request.query.strip()
        # Collapse whitespace
        q = ' '.join(q.split())
        # Best-effort PII redaction via privacy module if available
        try:
            from privacy import redact_outbound  # type: ignore[import]
            redacted, meta = redact_outbound(q)
            if meta.get('redacted_count', 0) > 0:
                q = redacted
                self.warnings.append(
                    f"normalize_query redacted {meta['redacted_count']} PII pattern(s)"
                )
        except ImportError:
            pass  # privacy module optional
        except Exception as e:
            self.warnings.append(f"normalize_query privacy fallback: {type(e).__name__}")
        self.normalized_query = q
        res = OperationResult.success_with(data={'normalized_query': q},
                                            step='normalize_query')
        self._record_step('normalize_query', res)
        return res

    # ── Step 3: plan ────────────────────────────────────────

    def plan_step(self) -> OperationResult:
        """Decompose the normalized query into typed SearchSubQueries.

        R2-2.3: Orchestrator passes max_sub_queries, mode, language explicitly.
        R2-2.2: Sub-queries are dispatched to providers (not root query).
        """
        if self._planner_fn is None:
            # No planner injected → single sub-query equal to normalized query.
            sq = SearchSubQuery(
                id='sq-001',
                query=self.normalized_query,
                parent_query=self.normalized_query,
                rationale='no planner injected; root query as single sub-query',
            )
            self.plan = SearchPlan(
                root_query=self.normalized_query,
                sub_queries=[sq],
                max_sub_queries=self.request.max_sub_queries,
            )
            res = OperationResult.degraded(
                data={'plan': self.plan.to_dict()},
                step='plan',
                reason='no planner_fn injected',
            )
            self._record_step('plan', res)
            return res

        try:
            raw = self._planner_fn(
                query=self.normalized_query,
                max_sub_queries=self.request.max_sub_queries,
                mode=self.request.mode.value,
                language=self.request.language,
            )
            # Accept either SearchPlan or dict (legacy planner.py output)
            if isinstance(raw, SearchPlan):
                self.plan = raw
            elif isinstance(raw, dict):
                # Normalize dict to SearchPlan via LegacySearchAdapter.
                # Ensure canonical field name sub_queries.
                legacy_subqueries = raw.get('sub_queries') or raw.get('subqueries') or []
                if not legacy_subqueries and raw.get('sub_queries') is None:
                    # planner.py returns list[str] in 'sub_queries' key
                    if isinstance(raw.get('sub_queries'), list):
                        legacy_subqueries = raw['sub_queries']
                # If planner returned list[str], convert to list[dict]
                if legacy_subqueries and isinstance(legacy_subqueries[0], str):
                    legacy_subqueries = [
                        {'id': f'sq-{i+1:03d}', 'query': s,
                         'parent_query': self.normalized_query}
                        for i, s in enumerate(legacy_subqueries)
                    ]
                raw_plan = {
                    'root_query': raw.get('root_query', self.normalized_query),
                    'sub_queries': legacy_subqueries,
                    'intent': raw.get('intent', 'factual'),
                    'complexity': raw.get('complexity', 'simple'),
                    'decompose': raw.get('decompose', False),
                    'rationale': raw.get('rationale', ''),
                    'max_sub_queries': raw.get('max_sub_queries',
                                               raw.get('max_subqueries',
                                                       self.request.max_sub_queries)),
                    'retrieved_at': raw.get('retrieved_at', ''),
                }
                self.plan = LegacySearchAdapter.plan_from_dict(raw_plan)
            else:
                raise TypeError(f"planner_fn returned {type(raw).__name__}")

            # Enforce max_sub_queries ceiling (R2-2.3: real Planner uses the cap)
            if len(self.plan.sub_queries) > self.request.max_sub_queries:
                self.warnings.append(
                    f"planner returned {len(self.plan.sub_queries)} sub-queries; "
                    f"trimming to max_sub_queries={self.request.max_sub_queries}"
                )
                self.plan.sub_queries = self.plan.sub_queries[:self.request.max_sub_queries]

            # Ensure every sub-query has a stable id and parent_query
            for i, sq in enumerate(self.plan.sub_queries):
                if not sq.id:
                    sq.id = f'sq-{i+1:03d}'
                if not sq.parent_query:
                    sq.parent_query = self.plan.root_query

            res = OperationResult.success_with(
                data={'plan': self.plan.to_dict(), 'sub_query_count': len(self.plan.sub_queries)},
                step='plan',
            )
        except Exception as e:
            # Fallback: single sub-query = normalized query (degraded)
            sq = SearchSubQuery(
                id='sq-001',
                query=self.normalized_query,
                parent_query=self.normalized_query,
                rationale=f'planner failed: {type(e).__name__}',
            )
            self.plan = SearchPlan(
                root_query=self.normalized_query,
                sub_queries=[sq],
                max_sub_queries=self.request.max_sub_queries,
            )
            res = OperationResult.degraded(
                data={'plan': self.plan.to_dict()},
                step='plan',
                reason=f'planner failed: {e}',
            )
            self.warnings.append(f"plan degraded: {e}")
        self._record_step('plan', res)
        return res

    # ── Step 4: final_cache_lookup ──────────────────────────

    def _cache_key(self, query: str, *, scope: str = 'final') -> str:
        """Cache key. Scope 'final' = full pipeline result for the root query;
        'provider' = per-sub-query result."""
        return f"{scope}|{query.strip().lower()}|{self.request.mode.value}"

    def final_cache_lookup(self) -> OperationResult:
        """Check cache for a fresh full-pipeline result for the root query.

        R2-2.5: freshness based on retrieved_at; prewarm does not extend.
        R2-2.4: cache is checked BEFORE network (provider_execute).
        """
        if self._cache_get_fn is None:
            res = OperationResult.skipped(reason='no cache_get_fn', step='final_cache_lookup')
            self._record_step('final_cache_lookup', res)
            return res
        try:
            entry = self._cache_get_fn(self._cache_key(self.normalized_query))
        except Exception as e:
            res = OperationResult.failed(code='cache_get_error',
                                          message=f"final cache lookup failed: {e}",
                                          step='final_cache_lookup')
            self._record_step('final_cache_lookup', res)
            return res
        if not entry or not isinstance(entry, dict):
            res = OperationResult.success_with(data={'hit': False},
                                                step='final_cache_lookup')
            self._record_step('final_cache_lookup', res)
            return res
        if not cache_entry_fresh(entry, self._cache_ttl):
            res = OperationResult.success_with(
                data={'hit': False, 'stale': True,
                      'retrieved_at': entry.get('retrieved_at')},
                step='final_cache_lookup',
            )
            self.warnings.append('final cache hit but stale (retrieved_at expired)')
            self._record_step('final_cache_lookup', res)
            return res
        # Fresh hit — reconstruct results from cache
        cached_results = entry.get('results', []) or entry.get('top_results', [])
        self.results = [
            LegacySearchAdapter.result_from_dict(r, provider='cache',
                                                 sub_query_id='sq-cached')
            for r in cached_results if isinstance(r, dict)
        ]
        # Carry cached verification if present
        if entry.get('verification'):
            self.verification = LegacySearchAdapter.verification_status_from_dict(
                entry['verification'])
        res = OperationResult.success_with(
            data={'hit': True, 'count': len(self.results)},
            step='final_cache_lookup',
        )
        self._record_step('final_cache_lookup', res)
        return res

    # ── Step 5: provider_cache_lookup ───────────────────────

    def provider_cache_lookup(self) -> OperationResult:
        """Per-sub-query cache lookup. Removes hits from provider_execute list.

        Sub-queries without a fresh cache entry are collected in
        self._pending_subqueries for provider_execute.
        """
        self._pending_subqueries: list[SearchSubQuery] = []
        if self._cache_get_fn is None or self.plan is None:
            if self.plan:
                self._pending_subqueries = list(self.plan.sub_queries)
            res = OperationResult.skipped(reason='no cache_get_fn or no plan',
                                          step='provider_cache_lookup')
            self._record_step('provider_cache_lookup', res)
            return res

        cache_hits = 0
        for sq in self.plan.sub_queries:
            try:
                entry = self._cache_get_fn(self._cache_key(sq.query, scope='provider'))
            except Exception:
                entry = None
            if entry and isinstance(entry, dict) and cache_entry_fresh(entry, self._cache_ttl):
                cached_results = entry.get('results', []) or entry.get('top_results', [])
                for r in cached_results:
                    if isinstance(r, dict):
                        self.results.append(
                            LegacySearchAdapter.result_from_dict(
                                r, provider='cache', sub_query_id=sq.id))
                cache_hits += 1
            else:
                self._pending_subqueries.append(sq)

        res = OperationResult.success_with(
            data={'cache_hits': cache_hits,
                  'pending': len(self._pending_subqueries)},
            step='provider_cache_lookup',
        )
        self._record_step('provider_cache_lookup', res)
        return res

    # ── Step 6: provider_execute (R2-2.2) ───────────────────

    def provider_execute(self) -> OperationResult:
        """Dispatch each pending SearchSubQuery to its Provider.

        R2-2.2: sub_queries are really dispatched; root-query-only path deleted.
        Every SearchResult carries sub_query_id = sq.id.
        """
        if not getattr(self, '_pending_subqueries', []):
            res = OperationResult.success_with(
                data={'executed': 0, 'reason': 'all sub-queries served from cache'},
                step='provider_execute',
            )
            self._record_step('provider_execute', res)
            return res

        deadline = self._deadline()
        executed = 0
        for sq in self._pending_subqueries:
            # Pick provider: explicit hint > registry pick by capability
            provider = None
            if sq.provider_hint:
                provider = self.registry.get(sq.provider_hint)
            if provider is None:
                # Choose first available WEB_SEARCH or ACADEMIC provider
                caps = [ProviderCapability.WEB_SEARCH, ProviderCapability.ACADEMIC,
                        ProviderCapability.SEMANTIC]
                for cap in caps:
                    candidates = self.registry.by_capability(cap)
                    if candidates:
                        provider = candidates[0]
                        break
            if provider is None:
                self.warnings.append(f"no provider for sub-query {sq.id}")
                self.provider_executions.append(ProviderExecution(
                    provider='none', sub_query_id=sq.id,
                    status=StepStatus.SKIPPED, error='no provider available'))
                continue

            start = time.monotonic()
            try:
                results = provider.search(sq, deadline)
                duration_ms = (time.monotonic() - start) * 1000.0
                # Enforce sub_query_id is set on every result
                for r in results:
                    r.sub_query_id = sq.id
                    if not r.provider:
                        r.provider = provider.name
                self.results.extend(results)
                self.provider_executions.append(ProviderExecution(
                    provider=provider.name, sub_query_id=sq.id,
                    status=StepStatus.SUCCEEDED, results=results,
                    duration_ms=duration_ms))
                executed += 1
            except Exception as e:
                duration_ms = (time.monotonic() - start) * 1000.0
                self.warnings.append(f"provider {provider.name} failed on {sq.id}: {e}")
                self.provider_executions.append(ProviderExecution(
                    provider=provider.name, sub_query_id=sq.id,
                    status=StepStatus.FAILED, error=str(e),
                    duration_ms=duration_ms))

        # Degraded if zero providers executed but we had pending sub-queries
        status = StepStatus.SUCCEEDED if executed > 0 else StepStatus.DEGRADED
        res = OperationResult(
            success=executed > 0,
            status=status,
            data={'executed': executed,
                  'total_pending': len(self._pending_subqueries)},
            metadata={'step': 'provider_execute'},
        )
        if executed == 0:
            res.add_warning(ErrorInfo(code='no_providers_executed',
                                       message='no provider returned results'))
            self.degraded_mode = True
        self._record_step('provider_execute', res)
        return res

    # ── Step 7: normalize_results ───────────────────────────

    def normalize_results(self) -> OperationResult:
        """Apply URL normalization (R2-2.6) and ensure required fields."""
        for r in self.results:
            r.url = normalize_url(r.url) or r.url
            if not r.retrieved_at:
                r.retrieved_at = datetime.now(timezone.utc).isoformat()
            if not r.id:
                r.id = f"{r.provider}-{r.url[:40]}"
        res = OperationResult.success_with(
            data={'normalized_count': len(self.results)},
            step='normalize_results',
        )
        self._record_step('normalize_results', res)
        return res

    # ── Step 8: deduplicate ─────────────────────────────────

    def deduplicate(self) -> OperationResult:
        """Deduplicate by normalized URL (R2-2.6). Preserves first occurrence."""
        seen: set[str] = set()
        deduped: list[SearchResult] = []
        for r in self.results:
            key = normalize_url(r.url) if r.url else f"nourl::{r.id}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        removed = len(self.results) - len(deduped)
        self.results = deduped
        res = OperationResult.success_with(
            data={'removed': removed, 'remaining': len(self.results)},
            step='deduplicate',
        )
        self._record_step('deduplicate', res)
        return res

    # ── Step 9: rank ────────────────────────────────────────

    def rank(self) -> OperationResult:
        """Score and sort results. Score = existing score + provider weight.

        Provider weight is a simple heuristic; replace with a learned ranker
        later without changing the contract.
        """
        provider_weights = {
            'serper': 1.0, 'searxng': 0.9, 'arxiv': 1.1,
            'semantic_scholar': 1.1, 'local_semantic': 0.7, 'cache': 0.5,
        }
        for r in self.results:
            weight = provider_weights.get(r.provider, 0.8)
            # Position-based decay: results earlier in their provider list
            # get a small boost. raw_metadata may carry 'position'.
            position = r.raw_metadata.get('position') if r.raw_metadata else None
            pos_boost = 0.0
            if isinstance(position, int) and position > 0:
                pos_boost = max(0.0, 1.0 - position * 0.1)
            r.score = float(r.score) * weight + pos_boost
        self.results.sort(key=lambda r: r.score, reverse=True)
        res = OperationResult.success_with(
            data={'ranked': len(self.results)},
            step='rank',
        )
        self._record_step('rank', res)
        return res

    # ── Step 10: aggregate ──────────────────────────────────

    def aggregate(self) -> OperationResult:
        """Merge results across providers into the final list.

        R2-2.4: `aggregate_pre` removed. This is the only aggregate step.
        """
        # Already merged in self.results; cap to a reasonable size.
        max_results = 50
        if len(self.results) > max_results:
            self.results = self.results[:max_results]
            self.warnings.append(f"aggregate truncated to {max_results} results")
        res = OperationResult.success_with(
            data={'final_count': len(self.results)},
            step='aggregate',
        )
        self._record_step('aggregate', res)
        return res

    # ── Step 11: verify (R2-2.7) ────────────────────────────

    def verify(self) -> OperationResult:
        """Run verification using the 7-state VerificationStatus model.

        R2-2.7: NOT_RUN must NOT be promoted to VERIFIED. Lexical overlap
        alone yields WEAK_SUPPORT at most.
        """
        if not self.request.verify:
            self.verification = VerificationReport(status=VerificationStatus.NOT_REQUESTED)
            res = OperationResult.skipped(reason='verify=False in request',
                                          step='verify')
            self._record_step('verify', res)
            return res
        if self._verify_fn is None:
            # No verifier injected → status stays NOT_RUN, NOT VERIFIED.
            self.verification = VerificationReport(status=VerificationStatus.NOT_RUN)
            res = OperationResult.degraded(
                data={'status': self.verification.status.value},
                step='verify',
                reason='no verify_fn injected; status NOT_RUN (not VERIFIED)',
            )
            self._record_step('verify', res)
            return res
        try:
            raw = self._verify_fn(self.normalized_query, self.results)
            if isinstance(raw, VerificationReport):
                self.verification = raw
            elif isinstance(raw, dict):
                self.verification = LegacySearchAdapter.verification_status_from_dict(raw)
            else:
                self.verification = VerificationReport(
                    status=VerificationStatus.ERROR,
                    error=f"verify_fn returned {type(raw).__name__}")
        except Exception as e:
            self.verification = VerificationReport(
                status=VerificationStatus.ERROR, error=str(e))
            self.warnings.append(f"verify failed: {e}")
        res = OperationResult.success_with(
            data={'status': self.verification.status.value},
            step='verify',
        )
        self._record_step('verify', res)
        return res

    # ── Step 12: format ─────────────────────────────────────

    def format(self) -> OperationResult:
        """Format final output. Includes verification status (R2-2.7)."""
        lines: list[str] = []
        lines.append(f"# Search: {self.normalized_query}")
        lines.append(f"Mode: {self.request.mode.value} | Results: {len(self.results)}")
        lines.append(f"Verification: {self.verification.status.value}")
        if self.degraded_mode:
            lines.append("Degraded: true")
        lines.append("")
        for i, r in enumerate(self.results, 1):
            lines.append(f"## {i}. {r.title or r.url}")
            lines.append(f"URL: {r.url}")
            lines.append(f"Provider: {r.provider} | sub_query: {r.sub_query_id}")
            if r.snippet:
                lines.append(f"Snippet: {r.snippet[:300]}")
            lines.append(f"Retrieved: {r.retrieved_at}")
            lines.append("")
        if self.warnings:
            lines.append("## Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")
        self.formatted_output = '\n'.join(lines)
        res = OperationResult.success_with(
            data={'output_length': len(self.formatted_output)},
            step='format',
        )
        self._record_step('format', res)
        return res

    # ── Step 13: cache_store (R2-2.5) ───────────────────────

    def cache_store(self) -> OperationResult:
        """Write the final result to cache with canonical timestamps.

        R2-2.5: writes `retrieved_at` (freshness basis), `cached_at` (write
        time), and `warmed_at=None` (only set by prewarm). Does NOT refresh
        `cached_at` of existing entries to extend freshness.
        """
        if self._cache_store_fn is None:
            res = OperationResult.skipped(reason='no cache_store_fn',
                                          step='cache_store')
            self._record_step('cache_store', res)
            return res
        now_iso = datetime.now(timezone.utc).isoformat()
        entry = {
            'query': self.normalized_query,
            'mode': self.request.mode.value,
            'results': [r.to_dict() for r in self.results],
            'verification': self.verification.to_dict(),
            'retrieved_at': now_iso,        # freshness basis
            'cached_at': now_iso,            # write time
            'warmed_at': None,               # only set by prewarm
            'cache_schema_version': 3,       # R2 schema
        }
        try:
            self._cache_store_fn(self._cache_key(self.normalized_query), entry)
            res = OperationResult.success_with(
                data={'stored': True, 'retrieved_at': now_iso},
                step='cache_store',
            )
        except Exception as e:
            res = OperationResult.failed(code='cache_store_error',
                                          message=str(e), step='cache_store')
            self.warnings.append(f"cache_store failed: {e}")
        self._record_step('cache_store', res)
        return res

    # ── Orchestration ───────────────────────────────────────

    def execute(self) -> SearchPipelineResult:
        """Run all steps in STEP_ORDER. Returns typed SearchPipelineResult.

        Stops early if validate_request fails (fatal). Otherwise all steps
        run; failures are recorded as OperationResult but do not abort.
        """
        # Reset state
        self.results = []
        self.provider_executions = []
        self.warnings = []
        self.step_reports = {}
        self.degraded_mode = False
        self._result = None

        # validate_request is fatal
        v = self.validate_request()
        if not v.success:
            return self._build_result()

        self.normalize_query()
        self.plan_step()

        # final_cache_lookup may short-circuit provider execution if fresh hit
        cache_hit = False
        if self._cache_get_fn is not None:
            fl = self.final_cache_lookup()
            if fl.success and fl.data and fl.data.get('hit'):
                cache_hit = True

        if not cache_hit:
            self.provider_cache_lookup()
            self.provider_execute()

        self.normalize_results()
        self.deduplicate()
        self.rank()
        self.aggregate()
        self.verify()
        self.format()
        self.cache_store()

        return self._build_result()

    def _build_result(self) -> SearchPipelineResult:
        result = SearchPipelineResult(
            request=self.request,
            plan=self.plan,
            results=self.results,
            provider_executions=self.provider_executions,
            verification=self.verification,
            step_reports=self.step_reports,
            warnings=list(self.warnings),
            degraded_mode=self.degraded_mode,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
        )
        self._result = result
        return result


# ── Convenience entry point ─────────────────────────────────

def run_pipeline(
    query: str,
    *,
    mode: str = 'standard',
    language: str = 'auto',
    max_sub_queries: int = 5,
    verify: bool = True,
    registry: Optional[ProviderRegistry] = None,
    planner_fn: Optional[Callable[..., Any]] = None,
    cache_get_fn: Optional[Callable[[str], Optional[dict]]] = None,
    cache_store_fn: Optional[Callable[[str, dict], None]] = None,
    verify_fn: Optional[Callable[[str, list[SearchResult]], Any]] = None,
) -> SearchPipelineResult:
    """Build a SearchRequest and run the pipeline. Convenience wrapper."""
    try:
        mode_enum = SearchMode(mode)
    except ValueError:
        mode_enum = SearchMode.STANDARD
    request = SearchRequest(
        query=query, mode=mode_enum, language=language,
        max_sub_queries=max_sub_queries, verify=verify,
    )
    pipeline = SearchPipeline(
        request, registry=registry, planner_fn=planner_fn,
        cache_get_fn=cache_get_fn, cache_store_fn=cache_store_fn,
        verify_fn=verify_fn,
    )
    return pipeline.execute()
