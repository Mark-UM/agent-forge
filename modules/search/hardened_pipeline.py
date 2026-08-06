"""Production SearchPipeline variant with observable provider fallback.

The legacy pipeline remains import-compatible for existing tests and callers.
New production wiring uses ``HardenedSearchPipeline`` so an unavailable or
empty provider never prevents the next candidate from running.  Fresh final
cache hits are consumed without writing a replacement entry, preserving the
original ``retrieved_at`` freshness boundary.
"""
from __future__ import annotations

import time

from modules.common.result import OperationResult, StepStatus, WarningInfo
from modules.search.contracts import ProviderExecution, SearchResult
from modules.search.pipeline import SearchPipeline
from modules.search.provider_policy import (
    ProviderOutcome,
    ProviderState,
    ordered_candidates,
    provider_state,
    should_stop,
)


def _annotate_execution(
    execution: ProviderExecution,
    *,
    state: ProviderState,
    outcome: ProviderOutcome,
    retryable: bool = False,
) -> ProviderExecution:
    # ProviderExecution predates the provider policy contract. Dynamic fields
    # retain source compatibility while serializers expose the new data.
    execution.provider_state = state.value
    execution.provider_outcome = outcome.value
    execution.retryable = bool(retryable)
    return execution


def serialize_provider_execution(execution: ProviderExecution) -> dict:
    data = execution.to_dict()
    data.update(
        {
            "provider_state": getattr(
                execution, "provider_state", ProviderState.READY.value
            ),
            "provider_outcome": getattr(
                execution,
                "provider_outcome",
                ProviderOutcome.SUCCESS.value
                if execution.results
                else ProviderOutcome.NO_RESULTS.value,
            ),
            "retryable": bool(getattr(execution, "retryable", False)),
        }
    )
    return data


class HardenedSearchPipeline(SearchPipeline):
    """Search pipeline with deterministic provider fallback and telemetry."""

    def execute(self):
        """Run the canonical steps without refreshing a final cache hit."""
        self.results = []
        self.provider_executions = []
        self.warnings = []
        self.step_reports = {}
        self.degraded_mode = False
        self._result = None

        validation = self.validate_request()
        if not validation.success:
            return self._build_result()

        self.normalize_query()
        self.plan_step()

        cache_hit = False
        if self._cache_get_fn is not None:
            lookup = self.final_cache_lookup()
            cache_hit = bool(
                lookup.success and lookup.data and lookup.data.get("hit")
            )

        if not cache_hit:
            self.provider_cache_lookup()
            self.provider_execute()

        self.normalize_results()
        self.deduplicate()
        self.rank()
        self.aggregate()
        self.verify()
        self.format()
        if cache_hit:
            skipped = OperationResult.skipped(
                reason="final cache hit; preserve original retrieved_at",
                step="cache_store",
            )
            self._record_step("cache_store", skipped)
        else:
            self.cache_store()
        return self._build_result()

    def provider_execute(self) -> OperationResult:
        pending = list(getattr(self, "_pending_subqueries", []))
        if not pending:
            result = OperationResult.success_with(
                data={
                    "attempted": 0,
                    "providers_with_results": 0,
                    "reason": "all sub-queries served from cache",
                },
                step="provider_execute",
            )
            self._record_step("provider_execute", result)
            return result

        deadline = self._deadline()
        initial_result_count = len(self.results)
        attempted = 0
        providers_with_results = 0
        failed = 0
        skipped = 0
        no_results = 0

        for sub_query in pending:
            subquery_result_count = 0
            subquery_successful_providers = 0
            candidates = ordered_candidates(self.registry, self.request, sub_query)
            if not candidates:
                skipped += 1
                self.warnings.append(f"no search provider for sub-query {sub_query.id}")
                self.provider_executions.append(
                    _annotate_execution(
                        ProviderExecution(
                            provider="none",
                            sub_query_id=sub_query.id,
                            status=StepStatus.SKIPPED,
                            error="no provider candidate",
                        ),
                        state=ProviderState.UNAVAILABLE,
                        outcome=ProviderOutcome.SKIPPED,
                    )
                )
                continue

            for provider in candidates:
                state = provider_state(provider)
                if state is not ProviderState.READY:
                    skipped += 1
                    self.provider_executions.append(
                        _annotate_execution(
                            ProviderExecution(
                                provider=provider.name,
                                sub_query_id=sub_query.id,
                                status=StepStatus.SKIPPED,
                                error=f"provider is {state.value}",
                            ),
                            state=state,
                            outcome=ProviderOutcome.SKIPPED,
                        )
                    )
                    continue

                attempted += 1
                started = time.monotonic()
                try:
                    results = provider.search(sub_query, deadline)
                    if results is None:
                        results = []
                    if not isinstance(results, list):
                        raise TypeError(
                            f"provider {provider.name} returned {type(results).__name__}, expected list"
                        )
                    for item in results:
                        if not isinstance(item, SearchResult):
                            raise TypeError(
                                f"provider {provider.name} returned a non-SearchResult item"
                            )
                        item.sub_query_id = sub_query.id
                        if not item.provider:
                            item.provider = provider.name
                    duration_ms = (time.monotonic() - started) * 1000.0
                except Exception as exc:
                    duration_ms = (time.monotonic() - started) * 1000.0
                    failed += 1
                    self.degraded_mode = True
                    self.warnings.append(
                        f"provider {provider.name} failed on {sub_query.id}: {exc}"
                    )
                    self.provider_executions.append(
                        _annotate_execution(
                            ProviderExecution(
                                provider=provider.name,
                                sub_query_id=sub_query.id,
                                status=StepStatus.FAILED,
                                error=str(exc),
                                duration_ms=duration_ms,
                            ),
                            state=ProviderState.DEGRADED,
                            outcome=ProviderOutcome.FAILED,
                            retryable=True,
                        )
                    )
                    continue

                if not results:
                    no_results += 1
                    self.provider_executions.append(
                        _annotate_execution(
                            ProviderExecution(
                                provider=provider.name,
                                sub_query_id=sub_query.id,
                                status=StepStatus.SUCCEEDED,
                                results=[],
                                duration_ms=duration_ms,
                            ),
                            state=ProviderState.READY,
                            outcome=ProviderOutcome.NO_RESULTS,
                        )
                    )
                    continue

                self.results.extend(results)
                providers_with_results += 1
                subquery_successful_providers += 1
                subquery_result_count += len(results)
                self.provider_executions.append(
                    _annotate_execution(
                        ProviderExecution(
                            provider=provider.name,
                            sub_query_id=sub_query.id,
                            status=StepStatus.SUCCEEDED,
                            results=results,
                            duration_ms=duration_ms,
                        ),
                        state=ProviderState.READY,
                        outcome=ProviderOutcome.SUCCESS,
                    )
                )
                if should_stop(
                    self.request.mode,
                    result_count=subquery_result_count,
                    successful_provider_count=subquery_successful_providers,
                ):
                    break

            if subquery_result_count == 0:
                self.warnings.append(
                    f"all candidates completed without results for sub-query {sub_query.id}"
                )

        new_result_count = len(self.results) - initial_result_count
        data = {
            "attempted": attempted,
            "providers_with_results": providers_with_results,
            "new_result_count": new_result_count,
            "failed": failed,
            "skipped": skipped,
            "no_results": no_results,
            "total_pending": len(pending),
        }

        fallback_used = failed > 0 or skipped > 0 or no_results > 0
        if new_result_count > 0:
            if fallback_used:
                result = OperationResult.degraded(
                    data=data,
                    step="provider_execute",
                    reason="provider fallback or partial failure occurred",
                )
                result.add_warning(
                    WarningInfo(
                        code="provider_fallback",
                        message="one or more provider candidates were skipped, empty, or failed",
                    )
                )
                self.degraded_mode = True
            else:
                result = OperationResult.success_with(data=data, step="provider_execute")
        elif failed > 0 and attempted == failed:
            result = OperationResult.failed(
                code="all_providers_failed",
                message="all attempted search providers failed",
                details=data,
                step="provider_execute",
            )
            result.data = data
            self.degraded_mode = True
        else:
            result = OperationResult(
                success=False,
                status=StepStatus.DEGRADED,
                data=data,
                warnings=[
                    WarningInfo(
                        code="no_search_results",
                        message="providers completed but returned no usable results",
                    )
                ],
                metadata={"step": "provider_execute"},
            )
            self.degraded_mode = True

        self._record_step("provider_execute", result)
        return result


__all__ = ["HardenedSearchPipeline", "serialize_provider_execution"]
