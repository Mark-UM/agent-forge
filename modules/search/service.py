"""Unified Search Service with truthful success and provider telemetry.

MCP, CLI, and command adapters use this service. Pipeline completion is not
reported as success unless usable search results exist.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules.common.run import EventType, RunRecorder, RunResult
from modules.search.contracts import SearchMode, SearchRequest, SearchResult
from modules.search.hardened_pipeline import (
    HardenedSearchPipeline,
    serialize_provider_execution,
)
from modules.search.pipeline import DEFAULT_CACHE_TTL_SECONDS
from modules.search.provider_policy import (
    ProviderOutcome,
    ProviderState,
    provider_state,
)
from modules.search.providers import ProviderRegistry, default_registry

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


@dataclass(frozen=True)
class SearchServiceRequest:
    query: str
    mode: str = "standard"
    language: str = "auto"
    max_sub_queries: int = 5
    verify: bool = True
    no_cache: bool = False


@dataclass(frozen=True)
class SearchServiceResult:
    success: bool
    query: str
    mode: str
    result: Optional[dict] = None
    error: Optional[str] = None
    degraded_mode: bool = False
    duration_ms: int = 0
    timestamp: str = ""
    run: Optional[RunResult] = None
    output_usable: bool = False
    execution_status: str = "failed"
    provider_attempts: tuple[dict, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_usable": self.output_usable,
            "execution_status": self.execution_status,
            "query": self.query,
            "mode": self.mode,
            "result": self.result,
            "error": self.error,
            "degraded_mode": self.degraded_mode,
            "provider_attempts": list(self.provider_attempts),
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            "run": self.run.to_dict() if self.run else None,
        }


class SearchServiceError(Exception):
    pass


class SearchServiceValidationError(SearchServiceError):
    pass


_CACHE_FILE = _PROJECT_ROOT / "_runtime" / "search" / "pipeline_cache.json"


def _load_cache() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = _CACHE_FILE.with_suffix(f".json.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, _CACHE_FILE)
    except OSError:
        pass


def _cache_get(key: str) -> Optional[dict]:
    value = _load_cache().get(key)
    return value if isinstance(value, dict) else None


def _cache_store(key: str, entry: dict) -> None:
    cache = _load_cache()
    cache[key] = entry
    _save_cache(cache)


def _classify_execution(
    *,
    output_usable: bool,
    degraded: bool,
    attempts: list[dict],
) -> str:
    outcomes = {attempt.get("provider_outcome") for attempt in attempts}
    if output_usable:
        return "partial" if degraded or outcomes - {ProviderOutcome.SUCCESS.value} else "succeeded"
    if ProviderOutcome.FAILED.value in outcomes and not (
        ProviderOutcome.NO_RESULTS.value in outcomes
    ):
        return "failed"
    return "no_results"


class SearchService:
    """Single production seam for Search execution."""

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
        self._registry = registry
        self._planner_fn = planner_fn
        self._verify_fn = verify_fn
        self._cache_get_fn = cache_get_fn or _cache_get
        self._cache_store_fn = cache_store_fn or _cache_store
        self._cache_ttl = cache_ttl_seconds

    def search(
        self,
        request: SearchServiceRequest,
        *,
        record_run: bool = False,
    ) -> SearchServiceResult:
        started = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        if not isinstance(request, SearchServiceRequest):
            raise SearchServiceValidationError("request must be SearchServiceRequest")
        if not request.query or not request.query.strip():
            raise SearchServiceValidationError("query must not be empty")
        if request.max_sub_queries < 1:
            raise SearchServiceValidationError("max_sub_queries must be at least 1")

        query = request.query.strip()
        try:
            mode = SearchMode(request.mode)
        except ValueError as exc:
            raise SearchServiceValidationError(
                f"invalid mode {request.mode!r}; valid modes: {[item.value for item in SearchMode]}"
            ) from exc

        registry = self._registry or self._build_default_registry()
        internal_request = SearchRequest(
            query=query,
            mode=mode,
            language=request.language,
            max_sub_queries=request.max_sub_queries,
            verify=request.verify,
        )
        cache_get = None if request.no_cache else self._cache_get_fn
        cache_store = None if request.no_cache else self._cache_store_fn

        recorder: Optional[RunRecorder] = None
        if record_run:
            recorder = RunRecorder(
                run_type="search",
                metadata={
                    "query": query,
                    "mode": request.mode,
                    "language": request.language,
                    "no_cache": request.no_cache,
                },
            )
            recorder.__enter__()
            if request.no_cache:
                recorder.event(
                    EventType.CACHE_MISS,
                    payload={"reason": "no_cache flag set"},
                )

        try:
            pipeline = HardenedSearchPipeline(
                internal_request,
                registry=registry,
                planner_fn=self._planner_fn,
                cache_get_fn=cache_get,
                cache_store_fn=cache_store,
                verify_fn=self._verify_fn,
                cache_ttl_seconds=self._cache_ttl,
            )
            pipeline_result = pipeline.execute()
            attempts = [
                serialize_provider_execution(execution)
                for execution in pipeline_result.provider_executions
            ]
            output_usable = bool(pipeline_result.results)
            degraded = pipeline_result.degraded_mode or any(
                attempt.get("provider_outcome") != ProviderOutcome.SUCCESS.value
                for attempt in attempts
            )
            execution_status = _classify_execution(
                output_usable=output_usable,
                degraded=degraded,
                attempts=attempts,
            )
            serialized = pipeline_result.to_dict()
            serialized["provider_executions"] = attempts
            serialized["success"] = output_usable
            serialized["output_usable"] = output_usable
            serialized["execution_status"] = execution_status

            run_result: Optional[RunResult] = None
            if recorder is not None:
                for name, operation in pipeline_result.step_reports.items():
                    recorder.add_step_result(name, operation)
                if degraded:
                    recorder.event(
                        EventType.DEGRADED_ENTERED,
                        level="warn",
                        payload={"mode": request.mode},
                    )
                recorder.__exit__(None, None, None)
                run_result = recorder.result

            error = None
            if not output_usable:
                error = (
                    "all search providers failed"
                    if execution_status == "failed"
                    else "search completed without usable results"
                )
            return SearchServiceResult(
                success=output_usable,
                output_usable=output_usable,
                execution_status=execution_status,
                query=query,
                mode=request.mode,
                result=serialized,
                error=error,
                degraded_mode=degraded,
                provider_attempts=tuple(attempts),
                duration_ms=int((time.monotonic() - started) * 1000),
                timestamp=timestamp,
                run=run_result,
            )
        except Exception as exc:
            run_result = None
            if recorder is not None:
                recorder.__exit__(type(exc), exc, exc.__traceback__)
                run_result = recorder.result
            return SearchServiceResult(
                success=False,
                output_usable=False,
                execution_status="failed",
                query=query,
                mode=request.mode,
                error=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
                timestamp=timestamp,
                run=run_result,
            )

    def health(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "service": "search",
            "healthy": True,
            "providers": [],
            "provider_count": 0,
            "ready_provider_count": 0,
            "cache_dir": str(_CACHE_FILE.parent),
            "cache_writable": True,
            "credentials": {
                "SERPER_API_KEY": bool(os.environ.get("SERPER_API_KEY")),
                "SILICONFLOW_API_KEY": bool(os.environ.get("SILICONFLOW_API_KEY")),
            },
            "warnings": [],
        }
        try:
            registry = self._build_default_registry()
            providers = []
            for provider in registry.all():
                state = provider_state(provider)
                providers.append({"name": provider.name, "state": state.value})
            status["providers"] = providers
            status["provider_count"] = len(providers)
            status["ready_provider_count"] = sum(
                item["state"] == ProviderState.READY.value for item in providers
            )
            if not status["ready_provider_count"]:
                status["healthy"] = False
                status["warnings"].append("no search provider is ready")
        except Exception as exc:
            status["healthy"] = False
            status["warnings"].append(f"provider registry build failed: {exc}")

        try:
            _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            test_file = _CACHE_FILE.parent / ".health_check"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
        except OSError:
            status["cache_writable"] = False
            status["warnings"].append("cache directory is not writable")
        return status

    def capabilities(self) -> list[str]:
        return [
            "search.pipeline",
            "search.provider.serper",
            "search.provider.searxng",
            "search.provider.arxiv",
            "search.provider.semantic_scholar",
            "search.provider.local_semantic",
            "search.provider.fetch",
        ]

    def _build_default_registry(self) -> ProviderRegistry:
        return default_registry(include_credentials_required=False)


_service_singleton: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """Return the canonical production service without changing package layout."""

    global _service_singleton
    if _service_singleton is None:
        # Lazy import avoids the factory -> service import cycle during module
        # initialization and preserves legacy tests that import search.py and
        # orchestrator.py as top-level modules from modules/search.
        from modules.search.factory import build_search_service

        _service_singleton = build_search_service()
    return _service_singleton


def reset_search_service() -> None:
    global _service_singleton
    _service_singleton = None


def search(
    query: str,
    *,
    mode: str = "standard",
    language: str = "auto",
    max_sub_queries: int = 5,
    verify: bool = True,
    no_cache: bool = False,
) -> SearchServiceResult:
    return get_search_service().search(
        SearchServiceRequest(
            query=query,
            mode=mode,
            language=language,
            max_sub_queries=max_sub_queries,
            verify=verify,
            no_cache=no_cache,
        )
    )
