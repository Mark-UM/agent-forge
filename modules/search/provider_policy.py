"""Provider availability, execution outcome, and mode-aware fallback policy.

This module is deliberately independent from individual provider adapters.  It
can classify both first-party providers and injected test doubles without
changing the legacy ``SearchProvider.search() -> list[SearchResult]`` surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from modules.search.contracts import (
    ProviderCapability,
    SearchMode,
    SearchRequest,
    SearchSubQuery,
)
from modules.search.providers import ProviderRegistry, SearchProvider


class ProviderState(str, Enum):
    READY = "ready"
    UNCONFIGURED = "unconfigured"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class ProviderOutcome(str, Enum):
    SUCCESS = "success"
    NO_RESULTS = "no_results"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ModePolicy:
    capability_order: tuple[ProviderCapability, ...]
    minimum_results: int
    minimum_successful_providers: int


MODE_POLICIES: dict[SearchMode, ModePolicy] = {
    SearchMode.QUICK: ModePolicy(
        capability_order=(
            ProviderCapability.WEB_SEARCH,
            ProviderCapability.ACADEMIC,
            ProviderCapability.SEMANTIC,
        ),
        minimum_results=1,
        minimum_successful_providers=1,
    ),
    SearchMode.STANDARD: ModePolicy(
        capability_order=(
            ProviderCapability.WEB_SEARCH,
            ProviderCapability.ACADEMIC,
            ProviderCapability.SEMANTIC,
        ),
        minimum_results=5,
        minimum_successful_providers=1,
    ),
    SearchMode.DEEP: ModePolicy(
        capability_order=(
            ProviderCapability.WEB_SEARCH,
            ProviderCapability.ACADEMIC,
            ProviderCapability.SEMANTIC,
        ),
        minimum_results=10,
        minimum_successful_providers=2,
    ),
    SearchMode.ACADEMIC: ModePolicy(
        capability_order=(
            ProviderCapability.ACADEMIC,
            ProviderCapability.SEMANTIC,
            ProviderCapability.WEB_SEARCH,
        ),
        minimum_results=8,
        minimum_successful_providers=2,
    ),
}


def provider_state(provider: SearchProvider) -> ProviderState:
    """Return a provider's operational state without executing a search.

    Providers may expose ``state()`` or ``availability()`` in future.  Until
    all adapters implement that contract, the compatibility checks below cover
    the current first-party provider shapes.
    """

    custom = getattr(provider, "state", None)
    if callable(custom):
        raw = custom()
        return raw if isinstance(raw, ProviderState) else ProviderState(str(raw))

    availability = getattr(provider, "availability", None)
    if callable(availability):
        raw = availability()
        return raw if isinstance(raw, ProviderState) else ProviderState(str(raw))

    if getattr(provider, "requires_credentials", False):
        api_key = getattr(provider, "_api_key", None)
        if not api_key:
            return ProviderState.UNCONFIGURED

    name = getattr(provider, "name", "")
    if name in {"searxng", "local_semantic"}:
        if getattr(provider, "_search_fn", None) is None:
            return ProviderState.UNCONFIGURED

    resolver = getattr(provider, "_resolve_fn", None)
    if callable(resolver):
        try:
            if resolver() is None:
                return ProviderState.UNAVAILABLE
        except Exception:
            return ProviderState.UNAVAILABLE

    return ProviderState.READY


def _append_unique(output: list[SearchProvider], providers: Iterable[SearchProvider]) -> None:
    seen = {provider.name for provider in output}
    for provider in providers:
        if provider.name in seen:
            continue
        output.append(provider)
        seen.add(provider.name)


def ordered_candidates(
    registry: ProviderRegistry,
    request: SearchRequest,
    sub_query: SearchSubQuery,
) -> list[SearchProvider]:
    """Return deterministic provider candidates for a sub-query.

    Explicit request providers constrain the set.  A planner hint is moved to
    the front but never prevents fallback.  FETCH/SUMMARIZE-only providers are
    not search candidates.
    """

    candidates: list[SearchProvider] = []
    explicit_names = request.providers or []
    if explicit_names:
        _append_unique(
            candidates,
            [provider for name in explicit_names if (provider := registry.get(name))],
        )
    else:
        policy = MODE_POLICIES[request.mode]
        for capability in policy.capability_order:
            _append_unique(candidates, registry.by_capability(capability))

    if sub_query.provider_hint:
        hinted = registry.get(sub_query.provider_hint)
        if hinted is not None:
            candidates = [hinted] + [
                provider for provider in candidates if provider.name != hinted.name
            ]

    filtered: list[SearchProvider] = []
    for provider in candidates:
        caps = getattr(provider, "capabilities", set())
        if caps and caps.issubset(
            {ProviderCapability.FETCH, ProviderCapability.SUMMARIZE, ProviderCapability.LOCAL_CACHE}
        ):
            continue
        try:
            if not provider.supports_language(request.language):
                continue
        except Exception:
            continue
        filtered.append(provider)
    return filtered


def should_stop(
    mode: SearchMode,
    *,
    result_count: int,
    successful_provider_count: int,
) -> bool:
    policy = MODE_POLICIES[mode]
    return (
        result_count >= policy.minimum_results
        and successful_provider_count >= policy.minimum_successful_providers
    )


__all__ = [
    "MODE_POLICIES",
    "ModePolicy",
    "ProviderOutcome",
    "ProviderState",
    "ordered_candidates",
    "provider_state",
    "should_stop",
]
