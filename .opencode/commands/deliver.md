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
    └─ 6. Architecture checks  (no any, default export, window, Core imports)
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

### Step 3 — Produce consolidated report

Combine the delivery checklist JSON + review summary into a single report:

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

### Review Summary
| Stage | Result | Score | Key Findings |
|-------|--------|-------|--------------|
| Code | PASS/FAIL | x/10 | ... |
| Structure | PASS/FAIL | x/10 | ... |
| Risk | PASS/FAIL | x/10 | ... |

## Overall: PASS / FAIL

## Required Fixes (if any):
1. [Category] file:line — finding — recommended fix
2. ...
```

## Pass/Fail Rules

- **PASS**: 0 errors in checklist AND all 3 review stages PASS (score ≥ 8/10) AND zero critical findings in review
- **FAIL**: any error in checklist OR any review stage FAILs (score < 8/10) OR any critical finding in review

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
