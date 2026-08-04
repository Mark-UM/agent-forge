"""modules.search.contracts — typed search domain contracts (Round 2 Phase 1).

Replaces loose Dicts with typed dataclasses. New code emits canonical field
names; legacy adapters translate old field names for read-only compatibility.

Canonical field names (new code MUST emit these):
    sub_queries         (NOT subqueries)
    max_sub_queries     (NOT max_subqueries)
    verification_status (NOT verification)
    retrieved_at        (NOT cached_at for freshness)

Legacy field names (read-only, via LegacySearchAdapter):
    subqueries
    max_subqueries
    cached_at
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

# Local import — modules.common is always present in Round 2+
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules.common.result import OperationResult, StepStatus


# ── Enums ────────────────────────────────────────────────────

class SearchMode(str, Enum):
    """Search pipeline mode."""

    QUICK = 'quick'           # single provider, no planning
    STANDARD = 'standard'     # plan + 1-3 providers
    DEEP = 'deep'             # plan + all providers + verify
    ACADEMIC = 'academic'     # arXiv + Semantic Scholar focus


class VerificationStatus(str, Enum):
    """Verification result state. Replaces Boolean `verified` field.

    NOT_REQUESTED        — caller did not ask for verification
    NOT_RUN              — verification step skipped / disabled
    WEAK_SUPPORT         — only lexical overlap found (formerly "verified")
    PARTIALLY_SUPPORTED  — some claims supported, others unverified
    VERIFIED             — independent source(s) confirm
    CONTRADICTED         — independent source(s) contradict
    ERROR                — verifier itself failed
    """

    NOT_REQUESTED = 'not_requested'
    NOT_RUN = 'not_run'
    WEAK_SUPPORT = 'weak_support'
    PARTIALLY_SUPPORTED = 'partially_supported'
    VERIFIED = 'verified'
    CONTRADICTED = 'contradicted'
    ERROR = 'error'

    @classmethod
    def is_strong(cls, status: 'VerificationStatus') -> bool:
        """VERIFIED requires independent confirmation."""
        return status == cls.VERIFIED

    @classmethod
    def is_run(cls, status: 'VerificationStatus') -> bool:
        """Did the verifier actually execute? NOT_REQUESTED / NOT_RUN → False."""
        return status not in (cls.NOT_REQUESTED, cls.NOT_RUN)


class ProviderCapability(str, Enum):
    """What a search provider can do."""

    WEB_SEARCH = 'web_search'
    ACADEMIC = 'academic'
    SEMANTIC = 'semantic'
    FETCH = 'fetch'
    SUMMARIZE = 'summarize'
    LOCAL_CACHE = 'local_cache'


# ── Request / Plan ───────────────────────────────────────────

@dataclass
class SearchRequest:
    """Typed search request. Replaces loose kwargs."""

    query: str
    mode: SearchMode = SearchMode.STANDARD
    language: str = 'auto'           # 'auto' | 'en' | 'zh' | ...
    max_sub_queries: int = 5
    deadline: Optional[datetime] = None  # absolute deadline for whole pipeline
    providers: Optional[list[str]] = None  # None = all enabled; explicit list = subset
    verify: bool = True
    deep_research: bool = False
    user_timezone: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'query': self.query,
            'mode': self.mode.value,
            'language': self.language,
            'max_sub_queries': self.max_sub_queries,
            'deadline': self.deadline.isoformat() if self.deadline else None,
            'providers': self.providers,
            'verify': self.verify,
            'deep_research': self.deep_research,
            'user_timezone': self.user_timezone,
            'metadata': self.metadata,
        }


@dataclass
class SearchSubQuery:
    """A single atomic sub-query produced by the planner."""

    id: str                            # stable id (e.g. 'sq-001')
    query: str
    parent_query: str
    rationale: str = ''
    provider_hint: Optional[str] = None  # suggested provider, None = auto

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'query': self.query,
            'parent_query': self.parent_query,
            'rationale': self.rationale,
            'provider_hint': self.provider_hint,
        }


@dataclass
class SearchPlan:
    """Planner output: root query decomposed into sub-queries.

    Canonical field is `sub_queries` (NOT `subqueries`).
    """

    root_query: str
    sub_queries: list[SearchSubQuery]
    intent: str = 'factual'            # factual|comparative|technical|news|academic|opinion
    complexity: str = 'simple'         # simple|medium|complex
    decompose: bool = False
    rationale: str = ''
    max_sub_queries: int = 5
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            'root_query': self.root_query,
            'sub_queries': [sq.to_dict() for sq in self.sub_queries],
            'intent': self.intent,
            'complexity': self.complexity,
            'decompose': self.decompose,
            'rationale': self.rationale,
            'max_sub_queries': self.max_sub_queries,
            'retrieved_at': self.retrieved_at,
        }


# ── Results ──────────────────────────────────────────────────

@dataclass
class SearchResult:
    """A single search result from a provider.

    `sub_query_id` ties this result back to the SearchSubQuery that produced it.
    `retrieved_at` is the canonical freshness timestamp (NOT cached_at).
    """

    id: str
    sub_query_id: str
    provider: str                     # provider name (e.g. 'serper')
    url: str = ''
    title: str = ''
    snippet: str = ''
    content: str = ''                 # full fetched content (if any)
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    language: str = ''
    score: float = 0.0
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'sub_query_id': self.sub_query_id,
            'provider': self.provider,
            'url': self.url,
            'title': self.title,
            'snippet': self.snippet,
            'content': self.content,
            'retrieved_at': self.retrieved_at,
            'language': self.language,
            'score': self.score,
            'raw_metadata': self.raw_metadata,
        }


@dataclass
class ProviderExecution:
    """One provider's execution against one sub-query."""

    provider: str
    sub_query_id: str
    status: StepStatus = StepStatus.PENDING
    results: list[SearchResult] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0.0
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            'provider': self.provider,
            'sub_query_id': self.sub_query_id,
            'status': self.status.value,
            'results': [r.to_dict() for r in self.results],
            'error': self.error,
            'duration_ms': self.duration_ms,
            'retrieved_at': self.retrieved_at,
        }


@dataclass
class VerificationReport:
    """Verification result for a set of search results.

    `status` is the canonical VerificationStatus enum. `lexical_overlap_score`
    is reported separately and MUST NOT promote the status to VERIFIED on its
    own (it maps to WEAK_SUPPORT at most).
    """

    status: VerificationStatus = VerificationStatus.NOT_RUN
    lexical_overlap_score: float = 0.0
    evidence: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    verified_claim_count: int = 0
    total_claim_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'status': self.status.value,
            'lexical_overlap_score': self.lexical_overlap_score,
            'evidence': self.evidence,
            'contradictions': self.contradictions,
            'verified_claim_count': self.verified_claim_count,
            'total_claim_count': self.total_claim_count,
            'error': self.error,
        }


# ── Pipeline Result ──────────────────────────────────────────

@dataclass
class SearchPipelineResult:
    """Top-level pipeline result returned by SearchOrchestrator and
    `modules.search.pipeline_mcp.search_pipeline`.

    Carries the typed SearchPlan, all SearchResults, VerificationReport,
    per-step status, warnings, and the canonical freshness timestamp
    `retrieved_at`.
    """

    request: SearchRequest
    plan: Optional[SearchPlan] = None
    results: list[SearchResult] = field(default_factory=list)
    provider_executions: list[ProviderExecution] = field(default_factory=list)
    verification: VerificationReport = field(default_factory=VerificationReport)
    step_reports: dict[str, OperationResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    degraded_mode: bool = False
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def success(self) -> bool:
        return bool(self.results) or (
            self.verification.status == VerificationStatus.VERIFIED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'request': self.request.to_dict(),
            'plan': self.plan.to_dict() if self.plan else None,
            'results': [r.to_dict() for r in self.results],
            'provider_executions': [p.to_dict() for p in self.provider_executions],
            'verification': self.verification.to_dict(),
            'step_reports': {k: v.to_dict() for k, v in self.step_reports.items()},
            'warnings': self.warnings,
            'degraded_mode': self.degraded_mode,
            'retrieved_at': self.retrieved_at,
        }


# ── Legacy Adapter (read-only) ───────────────────────────────

class LegacySearchAdapter:
    """Translate legacy Dict shapes to typed contracts.

    Read-only: callers MUST NOT use this to emit legacy field names. New code
    emits canonical names; this adapter only helps consume old data (e.g.
    cached entries written before Round 2).
    """

    @staticmethod
    def plan_from_dict(d: dict[str, Any]) -> SearchPlan:
        sub_queries_raw = d.get('sub_queries') or d.get('subqueries') or []
        sub_queries = [
            SearchSubQuery(
                id=sq.get('id', f'sq-{i:03d}'),
                query=sq.get('query', sq.get('text', '')),
                parent_query=d.get('root_query', d.get('query', '')),
                rationale=sq.get('rationale', ''),
                provider_hint=sq.get('provider_hint'),
            )
            for i, sq in enumerate(sub_queries_raw)
        ]
        return SearchPlan(
            root_query=d.get('root_query', d.get('query', '')),
            sub_queries=sub_queries,
            intent=d.get('intent', 'factual'),
            complexity=d.get('complexity', 'simple'),
            decompose=d.get('decompose', False),
            rationale=d.get('rationale', ''),
            max_sub_queries=d.get('max_sub_queries') or d.get('max_subqueries', 5),
            retrieved_at=d.get('retrieved_at') or d.get('cached_at', ''),
        )

    @staticmethod
    def result_from_dict(d: dict[str, Any], *, provider: str = 'unknown',
                         sub_query_id: str = 'sq-000') -> SearchResult:
        return SearchResult(
            id=d.get('id', ''),
            sub_query_id=d.get('sub_query_id', sub_query_id),
            provider=d.get('provider', provider),
            url=d.get('url', ''),
            title=d.get('title', ''),
            snippet=d.get('snippet', ''),
            content=d.get('content', ''),
            retrieved_at=d.get('retrieved_at') or d.get('cached_at', ''),
            language=d.get('language', ''),
            score=d.get('score', 0.0),
            raw_metadata=d.get('raw_metadata', d.get('metadata', {})),
        )

    @staticmethod
    def verification_status_from_dict(d: dict[str, Any]) -> VerificationReport:
        """Translate legacy Boolean `verified` field to typed status.

        Legacy `verified=True` from keyword overlap alone becomes
        WEAK_SUPPORT, NOT VERIFIED. Only an explicit `independent_confirmation`
        field can promote to VERIFIED.
        """
        raw_status = d.get('verification_status') or d.get('status')
        if raw_status and isinstance(raw_status, str):
            try:
                status = VerificationStatus(raw_status)
            except ValueError:
                status = VerificationStatus.NOT_RUN
        else:
            legacy_verified = bool(d.get('verified', False))
            independent = bool(d.get('independent_confirmation', False))
            if not d:
                status = VerificationStatus.NOT_REQUESTED
            elif independent:
                status = VerificationStatus.VERIFIED
            elif legacy_verified:
                # Old "verified" was just keyword overlap — downgrade.
                status = VerificationStatus.WEAK_SUPPORT
            else:
                status = VerificationStatus.NOT_RUN
        return VerificationReport(
            status=status,
            lexical_overlap_score=float(d.get('lexical_overlap_score',
                                              d.get('overlap_score', 0.0))),
            evidence=list(d.get('evidence', [])),
            contradictions=list(d.get('contradictions', [])),
            verified_claim_count=int(d.get('verified_claim_count', 0)),
            total_claim_count=int(d.get('total_claim_count', 0)),
            error=d.get('error'),
        )
