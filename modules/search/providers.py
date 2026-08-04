"""modules.search.providers — SearchProvider Protocol + adapters (Round 2 Phase 2).

Defines the typed Provider Protocol that all search providers implement.
Adapters wrap existing serper_mcp / arxiv_mcp / semantic_scholar_mcp / searxng
functions so they conform to the Protocol without modifying the originals.

Each provider declares:
    name                   — stable identifier (e.g. 'serper')
    capabilities           — set of ProviderCapability
    requires_credentials   — True if API key needed
    supports_language      — callable or static set
    default_timeout        — seconds

The search() method receives a typed SearchSubQuery + deadline and returns
a list[SearchResult]. Adapters translate the legacy dict returns of the
existing MCP modules into typed SearchResult objects.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

# Local imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules.common.result import OperationResult, StepStatus
from modules.search.contracts import (
    ProviderCapability, SearchResult, SearchSubQuery, VerificationStatus,
)

# Make sibling modules importable
_SEARCH_DIR = Path(__file__).resolve().parent
if str(_SEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(_SEARCH_DIR))


# ── Protocol ─────────────────────────────────────────────────

@runtime_checkable
class SearchProvider(Protocol):
    """Typed Protocol every search provider implements.

    Implementations may be classes or module-level objects; they only need
    to expose the attributes and the search() method.
    """

    name: str
    capabilities: set[ProviderCapability]
    requires_credentials: bool
    default_timeout: int

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        """Execute a search for one sub-query.

        Args:
            sub_query: typed SearchSubQuery with id, query, parent_query.
            deadline: absolute deadline; provider SHOULD abort after this.

        Returns:
            list[SearchResult], each carrying sub_query_id = sub_query.id.
            Returns [] on no results. Raises on hard failure.
        """
        ...

    def supports_language(self, language: str) -> bool:
        """True if the provider can serve the given language code."""
        ...


# ── Helper: build SearchResult from legacy dict ──────────────

def _result_from_legacy_dict(d: dict[str, Any], *, provider: str,
                             sub_query_id: str) -> SearchResult:
    """Translate a legacy search-result dict into a typed SearchResult."""
    return SearchResult(
        id=d.get('id', f"{provider}-{d.get('url', '')[:40]}"),
        sub_query_id=sub_query_id,
        provider=provider,
        url=d.get('url', d.get('link', '')),
        title=d.get('title', ''),
        snippet=d.get('snippet', d.get('description', d.get('text', ''))),
        content=d.get('content', ''),
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        language=d.get('language', ''),
        score=float(d.get('score', 0.0)),
        raw_metadata=d,
    )


# ── Serper Adapter ───────────────────────────────────────────

class SerperProvider:
    """Adapter for modules.search.serper_mcp.serper_search()."""

    name = 'serper'
    capabilities = {ProviderCapability.WEB_SEARCH}
    requires_credentials = True
    default_timeout = 30

    def __init__(self, api_key: Optional[str] = None,
                 search_fn: Optional[Callable] = None) -> None:
        self._api_key = api_key or os.environ.get('SERPER_API_KEY')
        # Allow injection for testing; lazy-import otherwise.
        self._search_fn = search_fn

    def _resolve_fn(self):
        if self._search_fn is not None:
            return self._search_fn
        try:
            from serper_mcp import serper_search
            self._search_fn = serper_search
            return serper_search
        except ImportError:
            return None

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        fn = self._resolve_fn()
        if fn is None:
            return []
        timeout = max(1, int((deadline - datetime.now(timezone.utc)).total_seconds()))
        result = fn(
            query=sub_query.query,
            api_key=self._api_key,
            num=10,
            timeout=min(timeout, self.default_timeout),
        )
        if not isinstance(result, dict) or not result.get('success', False):
            return []
        organic = result.get('organic', []) or result.get('results', [])
        return [
            _result_from_legacy_dict(r, provider=self.name,
                                     sub_query_id=sub_query.id)
            for r in organic
        ]

    def supports_language(self, language: str) -> bool:
        return True  # Serper supports all languages via hl param


# ── SearXNG Adapter ──────────────────────────────────────────

class SearXNGProvider:
    """Adapter for SearXNG MCP (npx mcp-searxng)."""

    name = 'searxng'
    capabilities = {ProviderCapability.WEB_SEARCH}
    requires_credentials = False
    default_timeout = 30

    def __init__(self, search_fn: Optional[Callable] = None) -> None:
        self._search_fn = search_fn

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        if self._search_fn is None:
            return []
        try:
            raw = self._search_fn(sub_query.query)
        except Exception:
            return []
        if isinstance(raw, dict):
            raw = raw.get('results', [])
        if not isinstance(raw, list):
            return []
        return [
            _result_from_legacy_dict(r, provider=self.name,
                                     sub_query_id=sub_query.id)
            for r in raw if isinstance(r, dict)
        ]

    def supports_language(self, language: str) -> bool:
        return True


# ── arXiv Adapter ────────────────────────────────────────────

class ArxivProvider:
    """Adapter for modules.search.arxiv_mcp."""

    name = 'arxiv'
    capabilities = {ProviderCapability.ACADEMIC}
    requires_credentials = False
    default_timeout = 30

    def __init__(self, search_fn: Optional[Callable] = None) -> None:
        self._search_fn = search_fn

    def _resolve_fn(self):
        if self._search_fn is not None:
            return self._search_fn
        try:
            from arxiv_mcp import arxiv_search
            self._search_fn = arxiv_search
            return arxiv_search
        except ImportError:
            return None

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        fn = self._resolve_fn()
        if fn is None:
            return []
        try:
            result = fn(query=sub_query.query, max_results=10)
        except Exception:
            return []
        if isinstance(result, dict):
            papers = result.get('results', []) or result.get('papers', [])
        elif isinstance(result, list):
            papers = result
        else:
            papers = []
        out: list[SearchResult] = []
        for p in papers:
            if not isinstance(p, dict):
                continue
            out.append(SearchResult(
                id=p.get('id', p.get('arxiv_id', '')),
                sub_query_id=sub_query.id,
                provider=self.name,
                url=p.get('url', p.get('link', '')),
                title=p.get('title', ''),
                snippet=p.get('summary', p.get('abstract', ''))[:500],
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                language='en',
                score=float(p.get('score', 0.0)),
                raw_metadata=p,
            ))
        return out

    def supports_language(self, language: str) -> bool:
        return language in ('en', 'auto')


# ── Semantic Scholar Adapter ─────────────────────────────────

class SemanticScholarProvider:
    """Adapter for modules.search.semantic_scholar_mcp."""

    name = 'semantic_scholar'
    capabilities = {ProviderCapability.ACADEMIC, ProviderCapability.SEMANTIC}
    requires_credentials = False
    default_timeout = 30

    def __init__(self, search_fn: Optional[Callable] = None) -> None:
        self._search_fn = search_fn

    def _resolve_fn(self):
        if self._search_fn is not None:
            return self._search_fn
        try:
            from semantic_scholar_mcp import semantic_scholar_search
            self._search_fn = semantic_scholar_search
            return semantic_scholar_search
        except ImportError:
            return None

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        fn = self._resolve_fn()
        if fn is None:
            return []
        try:
            result = fn(query=sub_query.query, limit=10)
        except Exception:
            return []
        if isinstance(result, dict):
            papers = result.get('results', []) or result.get('papers', [])
        elif isinstance(result, list):
            papers = result
        else:
            papers = []
        out: list[SearchResult] = []
        for p in papers:
            if not isinstance(p, dict):
                continue
            out.append(SearchResult(
                id=str(p.get('paperId', p.get('id', ''))),
                sub_query_id=sub_query.id,
                provider=self.name,
                url=p.get('url', p.get('openAccessPdf', {}).get('url', '')),
                title=p.get('title', ''),
                snippet=(p.get('abstract') or '')[:500],
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                language='en',
                score=float(p.get('score', 0.0)),
                raw_metadata=p,
            ))
        return out

    def supports_language(self, language: str) -> bool:
        return language in ('en', 'auto')


# ── Local Semantic (ChromaDB) Adapter ────────────────────────

class LocalSemanticProvider:
    """Adapter for local ChromaDB-based semantic search."""

    name = 'local_semantic'
    capabilities = {ProviderCapability.SEMANTIC, ProviderCapability.LOCAL_CACHE}
    requires_credentials = False
    default_timeout = 10

    def __init__(self, search_fn: Optional[Callable] = None) -> None:
        self._search_fn = search_fn

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        if self._search_fn is None:
            return []
        try:
            raw = self._search_fn(sub_query.query, top_k=5)
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        return [
            _result_from_legacy_dict(r, provider=self.name,
                                     sub_query_id=sub_query.id)
            for r in raw if isinstance(r, dict)
        ]

    def supports_language(self, language: str) -> bool:
        return True


# ── Fetch Adapter (optional, for full-content retrieval) ─────

class FetchProvider:
    """Optional adapter for modules.mcp.fetch_mcp — fetches full page content."""

    name = 'fetch'
    capabilities = {ProviderCapability.FETCH}
    requires_credentials = False
    default_timeout = 15

    def __init__(self, fetch_fn: Optional[Callable] = None) -> None:
        self._fetch_fn = fetch_fn

    def search(self, sub_query: SearchSubQuery,
               deadline: datetime) -> list[SearchResult]:
        """Fetch provider does not search; it enriches existing results.

        Called separately by the pipeline after provider_execute to fill in
        `content` for top results. Returns [] when used as a search provider.
        """
        return []

    def fetch_content(self, url: str) -> Optional[str]:
        if self._fetch_fn is None:
            return None
        try:
            result = self._fetch_fn(url)
            if isinstance(result, dict):
                return result.get('content', result.get('text', ''))
            return str(result) if result else None
        except Exception:
            return None

    def supports_language(self, language: str) -> bool:
        return True


# ── Provider Registry ────────────────────────────────────────

class ProviderRegistry:
    """Registry of available providers. Pipeline looks up providers by name
    or by capability.

    Providers are registered explicitly; the pipeline does not auto-discover.
    This keeps the dependency graph explicit and testable.
    """

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}

    def register(self, provider: SearchProvider) -> 'ProviderRegistry':
        self._providers[provider.name] = provider
        return self

    def get(self, name: str) -> Optional[SearchProvider]:
        return self._providers.get(name)

    def all(self) -> list[SearchProvider]:
        return list(self._providers.values())

    def by_capability(self, cap: ProviderCapability) -> list[SearchProvider]:
        return [p for p in self._providers.values() if cap in p.capabilities]

    def names(self) -> list[str]:
        return list(self._providers.keys())

    def filter_by_language(self, language: str) -> list[SearchProvider]:
        return [p for p in self._providers.values()
                if p.supports_language(language)]


def default_registry(*, include_credentials_required: bool = True,
                     inject_search_fns: Optional[dict[str, Callable]] = None
                     ) -> ProviderRegistry:
    """Build a default registry with all known providers.

    Args:
        include_credentials_required: if False, skip providers that need
            credentials but have no key available (e.g. Serper without key).
        inject_search_fns: optional dict mapping provider name → callable,
            used by tests to inject mocks.
    """
    inject = inject_search_fns or {}
    reg = ProviderRegistry()

    serper = SerperProvider(search_fn=inject.get('serper'))
    if include_credentials_required or serper._api_key or 'serper' in inject:
        reg.register(serper)

    reg.register(SearXNGProvider(search_fn=inject.get('searxng')))
    reg.register(ArxivProvider(search_fn=inject.get('arxiv')))
    reg.register(SemanticScholarProvider(search_fn=inject.get('semantic_scholar')))
    reg.register(LocalSemanticProvider(search_fn=inject.get('local_semantic')))
    reg.register(FetchProvider(fetch_fn=inject.get('fetch')))
    return reg
