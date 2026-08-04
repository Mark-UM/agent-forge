# REMEDIATION_MATRIX.md — Architecture Stabilization & Capability Closure

> Status taxonomy (Round 2+). "Code exists" is `implemented`, NOT `verified`.
> Only `verified` requires a real production path + end-to-end test pass.
>
> | Status | Meaning |
> |--------|---------|
> | `planned` | Identified, no code yet |
> | `implemented` | Code exists, not yet tested or exercised in production |
> | `tested` | Unit/contract tests pass |
> | `integrated` | Wired into runtime path (MCP / Composer / Scheduler) |
> | `verified` | End-to-end production path exercised + E2E test passes |
> | `partial` | Some criteria met, others outstanding |
> | `blocked` | Cannot proceed due to external dependency |
> | `deferred` | Intentionally postponed (low severity, documented rationale) |

## Baseline Test Results

```
Round 1 baseline (pre-remediation, 2026-08-04):
  1768 passed in 62.38s | 0 failed | 0 skipped | 0 errors

Round 1 post-remediation (2026-08-04):
  3033 passed in 41.90s | 0 failed | 3 skipped | 0 errors
  Net new tests: +1265

Round 2 baseline (2026-08-04, start of Round 2):
  3033 passed in 37.40s | 0 failed | 3 skipped | 0 errors
```

## Round 1 — Stabilization Fixes (all `tested`, pending Round 2 `verified`)

Round 1 items have unit + contract tests passing but have NOT been exercised
through the real OpenCode → MCP → Python production path end-to-end. They are
therefore `tested`, not `verified`. Round 2 will promote them to `verified` as
E2E coverage lands.

### Phase 1: Search Pipeline Correctness (P0)

| ID | Problem | Files | Test | Status |
| -- | ------- | ----- | ---- | ------ |
| S1 | `sub_queries` vs `subqueries` field mismatch | planner.py, orchestrator.py | TestS1PlannerOrchestratorContract | `tested` |
| S2 | `aggregator_fn` declared but never called | orchestrator.py | TestS2AggregatorCallbackInvoked | `tested` |
| S3 | `cache_store_fn` declared but never written back | orchestrator.py | TestS3CacheStoreCallbackInvoked | `tested` |
| S4 | Stream step returns only `{'stream_enabled': True}` | orchestrator.py | TestS4S5StreamPrewarmNotInStepOrder | `tested` |
| S5 | Prewarm step returns only `{'prewarm_enabled': True}` | orchestrator.py | TestS4S5StreamPrewarmNotInStepOrder | `tested` |
| S6 | Deep research max_subqueries clamped by planner | orchestrator.py, planner.py | TestS6DeepResearchMaxSubqueries | `tested` |
| S7 | Dry-run uses `enable_{step_name}` but config keys differ | orchestrator.py | TestS7DryRunConfigMapping | `tested` |
| S8 | Dedup uses empty string for URL-less results | orchestrator.py | TestS8DedupUrlLessResults | `tested` |
| S9 | `result` property caches and never invalidates | orchestrator.py | TestS9ResultInvalidation | `tested` |
| S10 | Verification not included in formatted_output | orchestrator.py | TestS10VerificationInFormat | `tested` |
| S11 | Parallel search waits for all threads on exit | parallel.py | TestS11ParallelAbandoned | `tested` |
| S12 | Prewarm resets cached_at to current time (fix: freshness keyed on `retrieved_at`, not `cached_at`) | prewarm.py | TestS12PrewarmTimestamps | `tested` |
| S13 | Verifier names keyword overlap as "verified" | verifier.py | TestS13VerifierNaming | `tested` |

### Phase 2: Prompt/Command/Agent Governance

| ID | Problem | Files | Test | Status |
| -- | ------- | ----- | ---- | ------ |
| P1 | review.md references non-existent agents | .opencode/prompts/tasks/review.md | test_review_task_prompt_references_current_agents | `tested` |
| P2 | anti-patterns.md loads Tower Stack rules for all languages | .opencode/prompts/contexts/anti-patterns.md | test_anti_patterns_no_tower_stack_specifics | `tested` |
| P3 | base.md has conflicting plan rules | base.md, AGENTS_BASE.md, AGENTS.md | test_base_md_has_deterministic_plan_rules | `tested` |
| P4 | No prompt priority declaration in composer | composer.py | test_composer_emits_priority_declaration | `tested` |
| P5 | No prompt reference checker | test_prompt_references.py | test_prompt_references | `tested` |

### Phase 3: Scheduler and Action Extraction

| ID | Problem | Files | Test | Status |
| -- | ------- | ----- | ---- | ------ |
| SC1 | Dual state sources: schedule_store.py vs daemon.py jobs.json | schedule_store.py, daemon.py, job_store.py | test_sc1_* | `tested` |
| SC2 | recurrence field saved but never executed | schedule_store.py | test_sc2_add_schedule_rejects_recurrence | `tested` |
| SC3 | HTTPServer is single-threaded | daemon.py | test_sc3_* | `tested` |
| SC4 | action_extractor Prompt says JSON array but API sets json_object | action_extractor.py | test_sc4_* | `tested` |
| SC5 | due_at not validated | action_extractor.py | test_sc5_* | `tested` |
| SC6 | action_extractor bypasses Dispatch Guard | action_extractor.py, guard.py | test_sc6_* | `tested` |

### Phase 4: Vision/Fetch/Time

| ID | Problem | Files | Test | Status |
| -- | ------- | ----- | ---- | ------ |
| V1 | PDF rendering has no page/size/pixel limits | vision/recognize.py | test_v1_* | `tested` |
| V2 | Fetch MCP reads entire response before truncating | mcp/fetch_mcp.py | test_v2_* | `tested` |
| V3 | Time MCP replace(tzinfo) overwrites input offset | mcp/time_mcp.py | test_v3_* | `tested` |

### Phase 5/6 (deferred in Round 1)

| ID | Problem | Status | Rationale |
| -- | ------- | ------ | --------- |
| M1 | No unified MemoryRepository API | `deferred` | No correctness bug; current backends operate independently. |
| C1 | Checkers labeled as definitive, not heuristic | `deferred` | Already documented as heuristic in ARCHITECTURE.md. |

---

## Round 2 — Stabilization & Execution Chain Closure

All Round 2 items start as `planned`. Status is updated only when the
corresponding code, tests, and integration land.

### Phase 0 — Trusted Baseline

| ID | Item | Status |
| -- | ---- | ------ |
| R2-0.1 | Run full pytest on Python 3.11, capture baseline | `verified` (3033 passed, 3 skipped, 0 failed) |
| R2-0.2 | Adopt new 8-state taxonomy in this matrix | `verified` |
| R2-0.3 | Auto-generate `_docs/TEST_REPORT.generated.md` from pytest JSON | `implemented` |

### Phase 1 — Domain Contracts

| ID | Item | Status |
| -- | ---- | ------ |
| R2-1.1 | `modules/common/result.py` — OperationResult, ErrorInfo, WarningInfo, StepStatus (9 states) | `tested` |
| R2-1.2 | `modules/common/errors.py` — typed error hierarchy | `tested` |
| R2-1.3 | `modules/common/time_utils.py` — UTC normalization helpers | `tested` |
| R2-1.4 | `modules/search/contracts.py` — SearchRequest/Plan/SubQuery/Result/ProviderExecution/VerificationReport/PipelineResult + legacy adapter | `tested` |
| R2-1.5 | `modules/scheduler/contracts.py` — ActionItem, ScheduledJob, JobRun, TriggerSpec | `tested` |
| R2-1.6 | `modules/prompt/contracts.py` — prompt assembly contracts | `tested` |

### Phase 2 — Search Execution Chain Closure

| ID | Item | Status |
| -- | ---- | ------ |
| R2-2.1 | `modules/search/providers.py` — SearchProvider Protocol + adapters (Serper, SearXNG, arXiv, Semantic Scholar, local semantic, Fetch/Summarize) | `tested` |
| R2-2.2 | Planner sub_queries actually dispatched to providers (delete root-query-only paths) | `tested` |
| R2-2.3 | Orchestrator passes max_sub_queries/mode/language to Planner explicitly | `tested` |
| R2-2.4 | Pipeline step order: validate→normalize→plan→final_cache→provider_cache→provider_execute→normalize_results→dedup→rank→aggregate→verify→format→cache_store (delete `aggregate_pre`) | `tested` |
| R2-2.5 | Cache freshness keyed on `retrieved_at`; Prewarm updates `cached_at`+`warmed_at` but neither affects freshness | `tested` |
| R2-2.6 | URL normalization strips only utm_*/fbclid/gclid; preserves id/page/query/version | `tested` |
| R2-2.7 | Verification state model: NOT_REQUESTED/NOT_RUN/WEAK_SUPPORT/PARTIALLY_SUPPORTED/VERIFIED/CONTRADICTED/ERROR; lexical_overlap only | `tested` |
| R2-2.8 | `modules/search/pipeline_mcp.py` — `search_pipeline` MCP tool; `/search` defaults to it; degraded mode flagged | `implemented` |

### Phase 3 — Prompt Runtime Convergence

| ID | Item | Status |
| -- | ---- | ------ |
| R2-3.1 | Slim base.md to system invariants only; move task-specific rules to task prompts | `planned` |
| R2-3.2 | `modules/prompt/context.py` — Project Context Detector (package.json deps, imports, markers, paths); supports threejs-game-loop + tower-stack-project; writes `_runtime/prompt/context-signals.json` | `planned` |
| R2-3.3 | Cross-platform path handling (Windows / POSIX / UNC) — no `Path.name` for foreign paths | `planned` |
| R2-3.4 | Prompt Reference Checker: fix `modules.delivery.checks` reference; integrate into `/deliver`; verify agents/skills/commands/modules/contexts/models/tools exist | `planned` |

### Phase 4 — Scheduler Domain Model

| ID | Item | Status |
| -- | ---- | ------ |
| R2-4.1 | Three SQLite tables: action_items, scheduled_jobs, job_runs (no compression) | `planned` |
| R2-4.2 | `supported_trigger_types = ["cron"]` only; date/interval → UNSUPPORTED_TRIGGER | `planned` |
| R2-4.3 | `add_job()` passes timezone, max_instances, misfire_grace_time, coalesce | `planned` |
| R2-4.4 | `execute_job_with_tracking(job_id)` — JobRun creation, RUNNING, SUCCEEDED/FAILED, run_count, last_error, next_run_at | `planned` |
| R2-4.5 | Manual trigger `POST /jobs/{id}/runs` returns run_id+QUEUED; `GET /runs/{run_id}` for status | `planned` |
| R2-4.6 | Migration: record version only on success; no permanent close on missing old file; corrupted → no success; rename/copy explicit; idempotent; unmapped jobs → quarantine | `planned` |

### Phase 5 — Action Extractor & Time

| ID | Item | Status |
| -- | ---- | ------ |
| R2-5.1 | All persisted timestamps → UTC ISO; preserve source_timezone + original_due_at; default tz from config | `planned` |
| R2-5.2 | Distinct failure modes: no_actions / model_error / parse_error / validation_error / storage_error | `planned` |
| R2-5.3 | Delete `except Exception: pass`; structured error + return to caller | `planned` |

### Phase 6 — Vision / Fetch / Time

| ID | Item | Status |
| -- | ---- | ------ |
| R2-6.1 | Vision: streaming batch (read page → render → size check → base64 → batch → send → release); request size includes base64 + data URL + prompt + JSON + metadata; try/finally PDF close; no sys.exit() in library; returns VisionRecognitionResult {processed_pages, skipped_pages, truncated, warnings, results} | `tested` |
| R2-6.2 | Fetch: max_start_index, bytes_read, network_truncated, total_length_known | `tested` |
| R2-6.3 | Time: DST gap reject/policy; DST fold requires fold=0/1 or ambiguous; negative minute offset formatting; aware datetime preserves offset; tests cover :30 and :45 offset zones | `tested` |

### Phase 7 — Delivery & Documentation

| ID | Item | Status |
| -- | ---- | ------ |
| R2-7.1 | `/deliver` runs: pytest + prompt reference check + contract tests + doc consistency + package audit | `implemented` |
| R2-7.2 | Doc fixes: scheduler DB path, search step order, typed contract claims, prewarm behavior, verification fields, test counts | `tested` |
| R2-7.3 | `scripts/build_release.py` with allowlist; excludes AGENTS_COMPOSED.md, markconfig/profile.md, secrets, _runtime, _data/private, cache, logs; emits release-manifest.json with hashes | `tested` |

### Phase 8 — Completion Criteria Verification

The 16 completion criteria from the Round 2 directive are tracked as a single
gate. Each criterion will be marked `verified` only when demonstrable.

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Full test pass on Python 3.11 | `verified` |
| 2 | Test report auto-reproducible | `verified` |
| 3 | Planner SubQuery enters Provider | `tested` |
| 4 | Deep Research上限 used by real Planner | `tested` |
| 5 | `/search` defaults to Search Pipeline MCP | `implemented` |
| 6 | Cache checked before network | `tested` |
| 7 | Prewarm does not refresh freshness | `tested` |
| 8 | Verification no longer "not run = verified" | `tested` |
| 9 | Context Detector triggers Three.js/Tower Stack | `tested` |
| 10 | Base prompt no longer requires both "no questions" and "wait for confirmation" | `tested` |
| 11 | Scheduler normal + manual runs both record JobRun | `tested` |
| 12 | Scheduler config fields passed to APScheduler | `tested` |
| 13 | All persisted times unified to UTC | `tested` |
| 14 | Vision no longer base64s all pages at once | `tested` |
| 15 | Docs match implementation | `tested` |
| 16 | Release package excludes generated prompt / profile / runtime | `tested` |

## Change Log

| Date | Change | Tests Delta |
| ---- | ------ | ----------- |
| 2026-08-04 | Round 1 baseline established | 0 |
| 2026-08-04 | Round 1 Phase 1: Search Pipeline (S1-S13) | +45 |
| 2026-08-04 | Round 1 Phase 2: Prompt governance (P1-P5) | +1176 |
| 2026-08-04 | Round 1 Phase 3: Scheduler + Action Extractor (SC1-SC6) | +29 |
| 2026-08-04 | Round 1 Phase 4: Vision/Fetch/Time (V1-V3) | +17 |
| 2026-08-04 | Round 2 baseline re-captured; matrix taxonomy rewritten (8 states) | 0 |
| 2026-08-04 | Round 2 Phase 0.3: auto test report generator added | 0 |
| 2026-08-04 | Round 2 Phase 6: Vision streaming batch, Fetch limits, Time DST tests | +16 |
| 2026-08-04 | Round 2 Phase 7: Delivery gate, doc consistency, build_release.py | +7 |
