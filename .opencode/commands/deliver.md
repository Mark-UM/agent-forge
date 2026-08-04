---
description: Final delivery gate — runs integration checks + 3-stage review + delivery checklist
agent: build
subtask: false
---

# /deliver — Final Delivery Gate

Run the full delivery pipeline on the current project. Use this before claiming a
task is "done" / "complete" / "ready for review".

## Pipeline

```
/deliver [target]
    │
    ├─ 1. Engineering checks   (file existence + content)
    ├─ 2. Resource checks      (manifest, public/, no inline onclick)
    ├─ 3. Integration checks   (delegates to modules/integration_check)
    ├─ 4. i18n wiring checks   (UIManager imports t, setLocale refresh)
    ├─ 5. Test quality checks  (thresholds, boundary, no soft assert)
    ├─ 6. Architecture checks  (no any, default export, window, Core imports)
    ├─ 7. Full pytest suite    (R2-7.1: all tests must pass)
    ├─ 8. Prompt reference check (R2-7.1: cross-refs valid, no dead names)
    ├─ 9. Contract tests       (R2-7.1: domain contracts enforced)
    ├─ 10. Documentation consistency (R2-7.1: docs match implementation)
    └─ 11. Package audit       (R2-7.1: release package excludes sensitive files)
    │
    ▼
Consolidated PASS/FAIL report
```

## Execution

### Step 1 — Run the checklist module

```bash
python -m modules.delivery.checklist --target . --json
```

If `--whitelist .opencode/delivery-whitelist.json` should be applied, add it.
If `--integration-whitelist .opencode/integration-whitelist.json` exists, add it.

### Step 2 — Run the 3-stage review pipeline

Invoke `/review` on recent code changes. The review pipeline runs:
1. `review-code` subagent
2. `review-structure` subagent
3. `review-risk` subagent

### Step 3 — Run full pytest suite (R2-7.1)

```bash
python -m pytest --tb=short -q
```

All tests must pass. Skipped tests are acceptable but must have valid reasons.
Record: collected, passed, failed, skipped, error counts.

### Step 4 — Run prompt reference check (R2-7.1)

```bash
python -m pytest modules/prompt/tests/test_prompt_references.py -v
```

This validates:
- Agent references → agent file exists
- Prompt context references → context file exists
- Skill references to Python modules → module file exists
- Model references → model configured in opencode.json
- Deprecated component names have no residuals
- Composed prompt contains priority declaration

### Step 5 — Run contract tests (R2-7.1)

```bash
python -m pytest modules/common/tests/test_contracts.py modules/search/tests/test_contracts.py modules/common/tests/test_scheduler_contracts.py modules/common/tests/test_prompt_contracts.py -v
```

All domain contract tests must pass.

### Step 6 — Documentation consistency check (R2-7.1)

Verify that documentation matches implementation:
- Scheduler database path matches `job_store.py` actual path
- Search Step Order matches `pipeline.py` actual step order
- Typed Contract declarations match actual dataclass fields
- Prewarm behavior matches actual cache update logic
- Verification fields match actual `VerificationStatus` enum
- Test counts in docs match auto-generated test report

### Step 7 — Package audit (R2-7.1)

```bash
python scripts/build_release.py --dry-run
```

Verify that the release package excludes:
- `AGENTS_COMPOSED.md` (generated prompt)
- `markconfig/profile.md` (personal profile)
- Secrets (`markconfig/secrets.json`, any `*secret*`, `*credential*`)
- `_runtime/` (runtime state)
- `_data/private/` (private data)
- `cache/`, `logs/` (transient data)

### Step 8 — Produce consolidated report

Combine the delivery checklist JSON + review summary + test results into a single report:

```
## Delivery Report

### Checklist Summary
| Category | Errors | Warnings | Info | Status |
|----------|--------|----------|------|--------|
| Engineering | N | N | N | PASS/FAIL |
| Resource | N | N | N | PASS/FAIL |
| Integration | N | N | N | PASS/FAIL |
| i18n | N | N | N | PASS/FAIL |
| Test Quality | N | N | N | PASS/FAIL |
| Architecture | N | N | N | PASS/FAIL |

### Test Summary
| Suite | Collected | Passed | Failed | Skipped | Status |
|-------|-----------|--------|--------|---------|--------|
| Full pytest | N | N | N | N | PASS/FAIL |
| Prompt refs | N | N | N | N | PASS/FAIL |
| Contracts | N | N | N | N | PASS/FAIL |

### Review Summary
| Stage | Result | Score | Key Findings |
|-------|--------|-------|--------------|
| Code | PASS/FAIL | x/10 | ... |
| Structure | PASS/FAIL | x/10 | ... |
| Risk | PASS/FAIL | x/10 | ... |

### Documentation & Package
| Check | Status |
|-------|--------|
| Doc consistency | PASS/FAIL |
| Package audit | PASS/FAIL |

## Overall: PASS / FAIL

## Required Fixes (if any):
1. [Category] file:line — finding — recommended fix
2. ...
```

## Pass/Fail Rules

- **PASS**: 0 errors in checklist AND all 3 review stages PASS (score ≥ 8/10) AND zero critical findings in review AND all test suites pass AND documentation consistent AND package audit clean
- **FAIL**: any error in checklist OR any review stage FAILs (score < 8/10) OR any critical finding in review OR any test suite fails OR documentation inconsistent OR package audit fails

If FAIL: list required fixes. Do NOT claim "done" until all ERROR-severity items
are resolved and zero critical findings remain. WARNING/INFO items are user-discretionary.

## When NOT to run `/deliver`

- Simple Q&A / chat
- Single-file edits without behavior change
- User explicitly says "skip delivery" / "快速过"

## Auto-trigger

The skill auto-loads when the user says:
- "完成" / "交付" / "验收"
- "deliver" / "done" / "finish"

But the full pipeline only runs when the user explicitly invokes `/deliver`
or confirms they want the full check.
