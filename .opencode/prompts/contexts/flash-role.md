---
description: Flash model role restriction — review/utility only, no independent module completion (Phase 2 Direction 4)
priority: 99
version: 2.0.0
trigger: always loaded (constitutional layer for multi-model collaboration)
---

# Flash Model Role Guard (enforced)

> **Source**: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 4
> **Root cause addressed**: Flash's quality collapse when used as an IMPLEMENTER
>  rather than a REVIEWER. Source doc Section 2.3 isolates 5 negative impacts of
>  Flash collaboration (naming drift, engineering regression, soft assertions,
>  performance missing, integration bugs avoided only by accident).
> **Enforcement**: `modules/dispatch/guard.py` — auto-redirects forbidden Flash
>  tasks to Pro with warning log (Decision 2).

## Policy Principle

Flash is a **REVIEWER/UTILITY**, never an **IMPLEMENTER**. The arrow of quality
flows Pro → Flash → Pro, never Flash → Pro.

- Pro writes code → Flash reviews code → Pro fixes based on Flash's findings
- Flash does NOT write code that Pro must review (Flash quality collapses here)
- Flash does NOT make architectural decisions (Pro does)
- Flash does NOT write tests (Flash produces soft assertions)

## Flash Allowed Tasks (✅)

These tasks are in the `FLASH_ALLOWED_TASKS` allowlist in `modules/dispatch/guard.py`:

### Utility Tasks (Flash is fine — fast, cheap, has fallback)
- ✅ `classify` — task type classification (modules/prompt/classify.py)
- ✅ `quality_scoring` — LLM-based scoring 0-10 (modules/search/quality.py)
- ✅ `i18n` — translation (modules/search/i18n.py)
- ✅ `summarize` — URL content summarization (modules/search/summarize.py)
- ✅ `aggregator` — result synthesis (modules/search/aggregator.py — has mechanical fallback)

### Review Tasks (Flash as REVIEWER — its core strength)
- ✅ `review_code` — 3-stage review: code (agents/review-code.md)
- ✅ `review_structure` — 3-stage review: structure (agents/review-structure.md)
- ✅ `review_risk` — 3-stage review: risk (agents/review-risk.md)
- ✅ `integration_check` — pre-delivery integration link check (Phase 3 skill)
- ✅ `doc_alignment_check` — comparing code vs doc naming (future)

## Flash Forbidden Tasks (❌)

These tasks are in the `FLASH_FORBIDDEN_TASKS` denylist. The guard will
**auto-redirect to Pro with warning log** when these are dispatched to Flash:

### Implementation Tasks (Pro only — Flash quality collapses)
- ❌ `implement_core` — core algorithm modules (StackEngine/ScoreManager/StateMachine)
- ❌ `implement_ui` — UI architecture (UIManager/panels/components)
- ❌ `implement_render` — complex rendering (PostProcessing/BloomPass/shaders)
- ❌ `implement_contract` — types.ts / contract files (naming-critical)
- ❌ `implement_test` — test files (Flash produces soft assertions)
- ❌ `implement_config` — engineering config (ESLint/CI/Vite/vitest)

### Reasoning Tasks (Pro only — requires deep understanding)
- ❌ `planner` — MindSearch query decomposition (entry point, quality matters)
- ❌ `architect` — system design / architecture decisions
- ❌ `refactor` — multi-file refactoring

## Model Selection Rule (per user constraint)

| Task category | Model | Reason |
|---|---|---|
| Core / most tasks | `deepseek-reasoner` (Pro) | User constraint: 核心甚至是大部分任务使用 v4 pro |
| Utility / review tasks | `deepseek-chat` (Flash) | User constraint: 剩下的使用 v4 flash |
| Other models | ❌ Not allowed | User constraint: 只能使用这两个模型 |

## Dispatch Rule (for opencode agent system)

When opencode dispatches a coding task, evaluate against the lists above:
- If task is in Forbidden list → MUST use Pro model
- If task is in Allowed list → Flash is acceptable
- If unclear → default to Pro (safer)

## Multi-Model Collaboration Principle

1. **Pro is the architect & implementer** — writes all production code, makes
   all architectural decisions, designs test cases with boundary coverage
2. **Flash is the reviewer & utility worker** — finds Pro's bugs (init-not-called,
   event-without-subscribe, soft assertions), performs fast utility tasks
   (classify, score, translate, summarize)
3. **The arrow flows Pro → Flash → Pro** — Pro implements, Flash reviews,
   Pro fixes. Never Flash → Pro (Flash's code quality is too low for Pro to
   waste time reviewing)

## Self-Check Before Dispatching to Flash

- [ ] Is this task in the Allowed list above? If yes → Flash is fine
- [ ] Is this task in the Forbidden list? If yes → MUST use Pro
- [ ] Is this task unclear? Default to Pro (safer)
- [ ] Am I asking Flash to write production code? → NO, use Pro
- [ ] Am I asking Flash to make architectural decisions? → NO, use Pro
- [ ] Am I asking Flash to write tests? → NO, use Pro (Flash produces soft assertions)

## Guard Enforcement

The `modules/dispatch/guard.py` module enforces this policy programmatically:
- `guard.check_task_allowed(task_type)` — boolean check (no side effects)
- `guard.resolve_model(task_type, caller=...)` — returns "deepseek-chat" or
  "deepseek-reasoner" (auto-redirects with warning log for forbidden tasks)
- `guard.call_flash(task_type, prompt, ...)` — safe entry point (handles
  redirect transparently)
- Guard log: `_runtime/dispatch/guard_log.jsonl` (all redirects & blocks)
