"""Canonical production wiring for SearchService.

Every production entry point should call ``build_search_service`` instead of
constructing SearchService ad hoc. Tests may still inject explicit dependencies.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

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
) -> SearchService:
    """Build the one supported production SearchService configuration."""

    return SearchService(
        registry=registry or default_registry(include_credentials_required=False),
        planner_fn=planner_fn if planner_fn is not None else build_planner_fn(),
        verify_fn=verify_fn if verify_fn is not None else build_verify_fn(),
        cache_get_fn=cache_get_fn,
        cache_store_fn=cache_store_fn,
    )


__all__ = ["build_planner_fn", "build_search_service", "build_verify_fn"]
