from __future__ import annotations

from datetime import datetime, timezone

from modules.search.contracts import (
    ProviderCapability,
    SearchPlan,
    SearchResult,
    SearchSubQuery,
)
from modules.search.provider_policy import ProviderState
from modules.search.providers import ProviderRegistry
from modules.search.service import SearchService, SearchServiceRequest


class FakeProvider:
    requires_credentials = False
    default_timeout = 5

    def __init__(
        self,
        name: str,
        *,
        capability: ProviderCapability = ProviderCapability.WEB_SEARCH,
        state: ProviderState = ProviderState.READY,
        result_count: int = 0,
        failure: Exception | None = None,
        calls: list[str] | None = None,
    ) -> None:
        self.name = name
        self.capabilities = {capability}
        self._state = state
        self._result_count = result_count
        self._failure = failure
        self._calls = calls

    def state(self) -> ProviderState:
        return self._state

    def supports_language(self, language: str) -> bool:
        return True

    def search(self, sub_query: SearchSubQuery, deadline: datetime):
        if self._calls is not None:
            self._calls.append(self.name)
        if self._failure is not None:
            raise self._failure
        return [
            SearchResult(
                id=f"{self.name}-{index}",
                sub_query_id=sub_query.id,
                provider=self.name,
                url=f"https://example.com/{self.name}/{index}",
                title=f"Result {index}",
                snippet="example",
            )
            for index in range(self._result_count)
        ]


def _planner(**kwargs):
    query = kwargs["query"]
    return SearchPlan(
        root_query=query,
        sub_queries=[
            SearchSubQuery(
                id="sq-001",
                query=query,
                parent_query=query,
            )
        ],
    )


def _service(registry: ProviderRegistry, **kwargs) -> SearchService:
    return SearchService(
        registry=registry,
        planner_fn=_planner,
        cache_get_fn=kwargs.get("cache_get_fn", lambda key: None),
        cache_store_fn=kwargs.get("cache_store_fn", lambda key, value: None),
    )


def test_unconfigured_provider_is_skipped_and_fallback_succeeds() -> None:
    registry = ProviderRegistry()
    registry.register(
        FakeProvider("unconfigured", state=ProviderState.UNCONFIGURED)
    )
    registry.register(FakeProvider("fallback", result_count=5))

    result = _service(registry).search(
        SearchServiceRequest(query="test", verify=False, no_cache=True)
    )

    assert result.success is True
    assert result.output_usable is True
    assert result.execution_status == "partial"
    assert result.degraded_mode is True
    assert result.provider_attempts[0]["provider_state"] == "unconfigured"
    assert result.provider_attempts[0]["provider_outcome"] == "skipped"
    assert result.provider_attempts[1]["provider_outcome"] == "success"


def test_empty_provider_does_not_prevent_fallback() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider("empty", result_count=0))
    registry.register(FakeProvider("fallback", result_count=5))

    result = _service(registry).search(
        SearchServiceRequest(query="test", verify=False, no_cache=True)
    )

    assert result.success is True
    assert [item["provider_outcome"] for item in result.provider_attempts] == [
        "no_results",
        "success",
    ]


def test_provider_failure_is_visible_and_fallback_is_partial() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider("broken", failure=RuntimeError("network down")))
    registry.register(FakeProvider("fallback", result_count=5))

    result = _service(registry).search(
        SearchServiceRequest(query="test", verify=False, no_cache=True)
    )

    assert result.success is True
    assert result.execution_status == "partial"
    assert result.provider_attempts[0]["provider_outcome"] == "failed"
    assert result.provider_attempts[0]["retryable"] is True
    assert result.provider_attempts[1]["provider_outcome"] == "success"


def test_all_empty_results_are_not_reported_as_success() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider("empty-a", result_count=0))
    registry.register(FakeProvider("empty-b", result_count=0))

    result = _service(registry).search(
        SearchServiceRequest(query="test", verify=False, no_cache=True)
    )

    assert result.success is False
    assert result.output_usable is False
    assert result.execution_status == "no_results"
    assert result.error == "search completed without usable results"


def test_academic_mode_orders_academic_provider_first() -> None:
    calls: list[str] = []
    registry = ProviderRegistry()
    registry.register(FakeProvider("web", result_count=5, calls=calls))
    registry.register(
        FakeProvider(
            "academic",
            capability=ProviderCapability.ACADEMIC,
            result_count=8,
            calls=calls,
        )
    )

    _service(registry).search(
        SearchServiceRequest(
            query="paper",
            mode="academic",
            verify=False,
            no_cache=True,
        )
    )

    assert calls[0] == "academic"


def test_final_cache_hit_does_not_rewrite_cache() -> None:
    retrieved_at = datetime.now(timezone.utc).isoformat()
    stores: list[tuple[str, dict]] = []
    cache_entry = {
        "query": "cached",
        "mode": "standard",
        "retrieved_at": retrieved_at,
        "cached_at": retrieved_at,
        "results": [
            {
                "id": "cached-1",
                "provider": "cache",
                "url": "https://example.com/cached",
                "title": "Cached",
                "snippet": "cached",
                "retrieved_at": retrieved_at,
            }
        ],
    }
    service = _service(
        ProviderRegistry(),
        cache_get_fn=lambda key: cache_entry,
        cache_store_fn=lambda key, value: stores.append((key, value)),
    )

    result = service.search(
        SearchServiceRequest(query="cached", verify=False, no_cache=False)
    )

    assert result.success is True
    assert result.execution_status == "succeeded"
    assert stores == []
    assert result.result["step_reports"]["cache_store"]["status"] == "skipped"
