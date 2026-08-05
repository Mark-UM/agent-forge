"""Canonical production wiring for SearchService.

Every production entry point calls ``build_search_service`` instead of
constructing SearchService ad hoc. Tests may still inject explicit dependencies.
The production cache is SQLite; legacy JSON cache files are import-only and are
never written by this factory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from modules.search.cache_store import (
    DEFAULT_DB_PATH,
    DEFAULT_TTL_SECONDS,
    SearchCacheRepository,
)
from modules.search.contracts import SearchResult
from modules.search.providers import ProviderRegistry, default_registry
from modules.search.service import SearchService


def build_planner_fn() -> Optional[Callable[..., Any]]:
    try:
        from modules.search.planner import plan_query
    except ImportError:
        return None

    def planner_fn(
        query: str,
        max_sub_queries: int = 5,
        mode: str = "standard",
        language: str = "auto",
    ) -> dict:
        del mode, language
        return plan_query(query, max_subqueries=max_sub_queries)

    return planner_fn


def build_verify_fn() -> Optional[Callable[[str, list[SearchResult]], Any]]:
    try:
        from modules.search.verifier import verify_against_authority
    except ImportError:
        return None

    def verify_fn(query: str, results: list[SearchResult]) -> dict:
        legacy_results = [
            {
                "url": result.url,
                "title": result.title,
                "snippet": result.snippet,
                "source": result.provider,
            }
            for result in results
        ]
        return verify_against_authority(query, legacy_results)

    return verify_fn


def build_search_service(
    *,
    registry: Optional[ProviderRegistry] = None,
    planner_fn: Optional[Callable[..., Any]] = None,
    verify_fn: Optional[Callable[[str, list[SearchResult]], Any]] = None,
    cache_get_fn: Optional[Callable[[str], Optional[dict]]] = None,
    cache_store_fn: Optional[Callable[[str, dict], None]] = None,
    cache_repository: Optional[SearchCacheRepository] = None,
    cache_db_path: Path | str = DEFAULT_DB_PATH,
    cache_ttl_seconds: int = DEFAULT_TTL_SECONDS,
    migrate_legacy_cache: bool = True,
) -> SearchService:
    """Build the one supported production SearchService configuration.

    Explicit cache callbacks remain available for tests and specialised
    embeddings. When either callback is omitted, the missing callback is filled
    from one authoritative :class:`SearchCacheRepository`. Legacy JSON files
    are migrated idempotently before the service starts, but are left untouched
    and are never used as a write target.
    """

    repository = cache_repository
    if cache_get_fn is None or cache_store_fn is None:
        repository = repository or SearchCacheRepository(cache_db_path)
        if migrate_legacy_cache:
            repository.migrate_legacy_defaults()
        sqlite_get, sqlite_store = repository.result_adapters(
            ttl_seconds=cache_ttl_seconds
        )
        if cache_get_fn is None:
            cache_get_fn = sqlite_get
        if cache_store_fn is None:
            cache_store_fn = sqlite_store

    service = SearchService(
        registry=registry or default_registry(include_credentials_required=False),
        planner_fn=planner_fn if planner_fn is not None else build_planner_fn(),
        verify_fn=verify_fn if verify_fn is not None else build_verify_fn(),
        cache_get_fn=cache_get_fn,
        cache_store_fn=cache_store_fn,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    # Exposed only for health reporting and deterministic tests; callers should
    # continue to use SearchService rather than reaching into the repository.
    service._cache_repository = repository  # type: ignore[attr-defined]
    return service


__all__ = ["build_planner_fn", "build_search_service", "build_verify_fn"]
