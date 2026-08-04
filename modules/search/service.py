"""modules.search.service — Unified Search Service layer (Phase B).

This is the single entry point that MCP, CLI, and /search Command all call.
It delegates to the existing SearchPipeline in pipeline.py — it is a thin
wrapper that provides:

    1. Consistent input/output contract (SearchServiceRequest → SearchServiceResult)
    2. Default wiring (provider registry, cache, planner, verifier)
    3. Health check endpoint for the Capability Registry
    4. Degraded mode tracking (no silent failures)

Architecture:

    Search MCP   ─┐
    Search CLI   ─┼─→  SearchService.search()  ─→  SearchPipeline.execute()
    /search      ─┘            │
                                ├─ ProviderRegistry (default or injected)
                                ├─ Cache (on-disk JSON)
                                ├─ Planner (real or stub)
                                └─ Verifier (real or stub)

The Service does NOT implement search logic itself — it wires dependencies
and delegates to SearchPipeline. This keeps the pipeline testable in isolation
while giving callers a single, consistent entry point.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Bootstrap project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules.common.result import OperationResult, StepStatus
from modules.common.run import EventType, RunRecorder, RunResult, RunStatus
from modules.search.contracts import (
    SearchMode, SearchPipelineResult, SearchRequest, SearchResult,
    VerificationReport, VerificationStatus,
)
from modules.search.pipeline import SearchPipeline, DEFAULT_CACHE_TTL_SECONDS
from modules.search.providers import (
    ProviderRegistry, default_registry,
)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ── Service-level request/result contracts ──────────────────

@dataclass(frozen=True)
class SearchServiceRequest:
    """Normalized request accepted by SearchService.search().

    This is a simpler contract than SearchRequest — the Service handles
    enum conversion and validation internally.
    """
    query: str
    mode: str = 'standard'           # quick|standard|deep|academic
    language: str = 'auto'
    max_sub_queries: int = 5
    verify: bool = True
    no_cache: bool = False


@dataclass(frozen=True)
class SearchServiceResult:
    """Normalized result returned by SearchService.search().

    Wraps the SearchPipelineResult with service-level metadata. The optional
    `run` field carries a RunResult (Phase C Run/Step/Event model) when
    `record_run=True` is passed to search().
    """
    success: bool
    query: str
    mode: str
    result: Optional[dict] = None       # serialized SearchPipelineResult
    error: Optional[str] = None
    degraded_mode: bool = False
    duration_ms: int = 0
    timestamp: str = ''
    run: Optional[RunResult] = None     # Phase C: Run/Step/Event telemetry

    def to_dict(self) -> dict[str, Any]:
        return {
            'success': self.success,
            'query': self.query,
            'mode': self.mode,
            'result': self.result,
            'error': self.error,
            'degraded_mode': self.degraded_mode,
            'duration_ms': self.duration_ms,
            'timestamp': self.timestamp,
            'run': self.run.to_dict() if self.run else None,
        }


# ── Exceptions ──────────────────────────────────────────────

class SearchServiceError(Exception):
    """Base error for SearchService."""


class SearchServiceValidationError(SearchServiceError):
    """Raised when the request is invalid (empty query, bad mode, etc.)."""


# ── On-disk cache (shared across all entry points) ──────────

_CACHE_FILE = _PROJECT_ROOT / '_runtime' / 'search' / 'pipeline_cache.json'


def _load_cache() -> dict[str, Any]:
    """Load the on-disk cache. Returns empty dict if missing or corrupt."""
    if not _CACHE_FILE.exists():
        return {}
    try:
        with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    """Save cache to disk atomically. Non-fatal on failure."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_FILE.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CACHE_FILE)
    except OSError:
        pass


def _cache_get(key: str) -> Optional[dict]:
    return _load_cache().get(key)


def _cache_store(key: str, entry: dict) -> None:
    cache = _load_cache()
    cache[key] = entry
    _save_cache(cache)


# ── SearchService ───────────────────────────────────────────

class SearchService:
    """Unified Search Service — single entry point for MCP, CLI, and /search.

    All three entry points (Search MCP server, Search CLI, /search Markdown
    command) call `service.search()` with a SearchServiceRequest. The Service
    builds the internal SearchRequest, wires dependencies (provider registry,
    cache, planner, verifier), runs the SearchPipeline, and returns a
    normalized SearchServiceResult.

    The Service does NOT contain search logic — it is a dependency wiring
    layer that delegates to SearchPipeline.
    """

    def __init__(
        self,
        *,
        registry: Optional[ProviderRegistry] = None,
        planner_fn: Optional[Callable[..., Any]] = None,
        verify_fn: Optional[Callable[[str, list[SearchResult]], Any]] = None,
        cache_get_fn: Optional[Callable[[str], Optional[dict]]] = None,
        cache_store_fn: Optional[Callable[[str, dict], None]] = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        """Initialize the SearchService.

        Args:
            registry: ProviderRegistry. If None, built on each search() call
                      via default_registry().
            planner_fn: Sub-query planner. If None, pipeline uses a stub.
            verify_fn: Verifier. If None, verification is skipped.
            cache_get_fn: Cache read function. If None, on-disk JSON cache.
            cache_store_fn: Cache write function. If None, on-disk JSON cache.
            cache_ttl_seconds: Cache freshness TTL. Default 24h.
        """
        self._registry = registry
        self._planner_fn = planner_fn
        self._verify_fn = verify_fn
        self._cache_get_fn = cache_get_fn or _cache_get
        self._cache_store_fn = cache_store_fn or _cache_store
        self._cache_ttl = cache_ttl_seconds

    # ── Public API ──────────────────────────────────────────

    def search(self, request: SearchServiceRequest,
               *, record_run: bool = False) -> SearchServiceResult:
        """Execute a search. This is the single unified entry point.

        Args:
            request: SearchServiceRequest with query, mode, etc.
            record_run: If True, record a Run/Step/Event RunResult (Phase C)
                and attach it to the returned SearchServiceResult.run field.
                Default False to avoid overhead when telemetry is not needed.

        Returns:
            SearchServiceResult with serialized pipeline result or error.

        Raises:
            SearchServiceValidationError: If the request is invalid.
        """
        start = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        # Validate request
        if not request.query or not request.query.strip():
            raise SearchServiceValidationError("query must not be empty")

        query = request.query.strip()

        # Build mode enum
        try:
            mode_enum = SearchMode(request.mode)
        except ValueError:
            raise SearchServiceValidationError(
                f"Invalid mode: {request.mode!r}. "
                f"Valid: {[m.value for m in SearchMode]}"
            )

        # Build provider registry
        registry = self._registry or self._build_default_registry()

        # Build internal SearchRequest
        internal_request = SearchRequest(
            query=query,
            mode=mode_enum,
            language=request.language,
            max_sub_queries=request.max_sub_queries,
            verify=request.verify,
        )

        # Determine cache functions
        cache_get = None if request.no_cache else self._cache_get_fn
        cache_store = None if request.no_cache else self._cache_store_fn

        # Optional Run/Step/Event recording (Phase C)
        recorder: Optional[RunRecorder] = None
        if record_run:
            recorder = RunRecorder(
                run_type='search',
                metadata={
                    'query': query, 'mode': request.mode,
                    'language': request.language,
                    'no_cache': request.no_cache,
                },
            )
            recorder.__enter__()
            if request.no_cache:
                recorder.event(EventType.CACHE_MISS,
                               payload={'reason': 'no_cache flag set'})

        # Run pipeline
        try:
            pipeline = SearchPipeline(
                internal_request,
                registry=registry,
                planner_fn=self._planner_fn,
                cache_get_fn=cache_get,
                cache_store_fn=cache_store,
                verify_fn=self._verify_fn,
                cache_ttl_seconds=self._cache_ttl,
            )
            pipeline_result = pipeline.execute()
            duration_ms = int((time.monotonic() - start) * 1000)

            # If recording, surface pipeline step_reports as Run steps
            run_result: Optional[RunResult] = None
            if recorder is not None:
                for name, op_result in pipeline_result.step_reports.items():
                    recorder.add_step_result(name, op_result)
                if pipeline_result.degraded_mode:
                    recorder.event(EventType.DEGRADED_ENTERED,
                                   level='warn',
                                   payload={'mode': request.mode})
                run_result = recorder.result if recorder._result is None else recorder._result
                if recorder._run.ended_at is None:
                    recorder.__exit__(None, None, None)
                    run_result = recorder.result

            return SearchServiceResult(
                success=True,
                query=query,
                mode=request.mode,
                result=pipeline_result.to_dict(),
                degraded_mode=pipeline_result.degraded_mode,
                duration_ms=duration_ms,
                timestamp=timestamp,
                run=run_result,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            if recorder is not None:
                recorder.__exit__(type(e), e, e.__traceback__)
                run_result = recorder.result
            else:
                run_result = None
            return SearchServiceResult(
                success=False,
                query=query,
                mode=request.mode,
                error=str(e),
                duration_ms=duration_ms,
                timestamp=timestamp,
                run=run_result,
            )

    def health(self) -> dict[str, Any]:
        """Return health status for the Capability Registry.

        Reports:
        - Whether the provider registry can be built
        - How many providers are registered
        - Whether the cache directory is writable
        - Whether required credentials are present
        """
        status: dict[str, Any] = {
            'service': 'search',
            'healthy': True,
            'providers': [],
            'provider_count': 0,
            'cache_dir': str(_CACHE_FILE.parent),
            'cache_writable': True,
            'credentials': {},
            'warnings': [],
        }

        # Check provider registry
        try:
            reg = self._build_default_registry()
            names = reg.names()
            status['providers'] = names
            status['provider_count'] = len(names)
        except Exception as e:
            status['healthy'] = False
            status['warnings'].append(f"Provider registry build failed: {e}")

        # Check cache directory
        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            test_file = _CACHE_FILE.parent / '.health_check'
            test_file.write_text('ok')
            test_file.unlink()
        except OSError:
            status['cache_writable'] = False
            status['warnings'].append("Cache directory is not writable")

        # Check credentials
        status['credentials'] = {
            'SERPER_API_KEY': bool(os.environ.get('SERPER_API_KEY')),
            'SILICONFLOW_API_KEY': bool(os.environ.get('SILICONFLOW_API_KEY')),
        }

        return status

    def capabilities(self) -> list[str]:
        """Return the list of capabilities this service provides."""
        return [
            'search.pipeline',
            'search.provider.serper',
            'search.provider.searxng',
            'search.provider.arxiv',
            'search.provider.semantic_scholar',
            'search.provider.local_semantic',
            'search.provider.fetch',
        ]

    # ── Internal helpers ────────────────────────────────────

    def _build_default_registry(self) -> ProviderRegistry:
        """Build the default provider registry based on environment.

        Serper is registered only if SERPER_API_KEY is set. Others are
        always registered but return [] if their backend is unavailable.
        """
        return default_registry(include_credentials_required=False)


# ── Module-level singleton ──────────────────────────────────

_service_singleton: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """Return the module-level SearchService singleton.

    On first call, creates a service with default wiring (on-disk cache,
    default provider registry). Subsequent calls return the cached instance.
    """
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = SearchService()
    return _service_singleton


def reset_search_service() -> None:
    """Reset the singleton. Primarily for testing."""
    global _service_singleton
    _service_singleton = None


# ── Convenience function ────────────────────────────────────

def search(
    query: str,
    *,
    mode: str = 'standard',
    language: str = 'auto',
    max_sub_queries: int = 5,
    verify: bool = True,
    no_cache: bool = False,
) -> SearchServiceResult:
    """One-shot search convenience function.

    Uses the singleton SearchService. Equivalent to:

        service = get_search_service()
        result = service.search(SearchServiceRequest(query=query, mode=mode, ...))
    """
    service = get_search_service()
    request = SearchServiceRequest(
        query=query,
        mode=mode,
        language=language,
        max_sub_queries=max_sub_queries,
        verify=verify,
        no_cache=no_cache,
    )
    return service.search(request)
