---
description: Delivery task — final gate before declaring a project "done"
priority: 100
version: 1.0.0
task_type: delivery
trigger:
  keywords: ["完成", "交付", "验收", "deliver", "done", "finish"]
  slash_command: /deliver
---

# Delivery Task

You are now in **delivery mode**. The user believes the task is complete.
Your job: verify that belief with concrete checks before claiming "done".

## Mindset Shift

- **During coding**: build features, write tests, fix bugs.
- **During delivery**: become a skeptical reviewer. Assume something is wrong.
  Find it before the user ships it.

The deepest failure mode is the **"form compliance trap"**: files exist with
the right names but lack required content. Examples:
- `.eslintrc.cjs` exists but has `'off'` overrides
- `UIManager.ts` exists but doesn't `import { t } from '@/i18n'`
- `tests/perfect.spec.ts` exists but uses `toContain` on known values
- `manifest.json` references icons that don't exist on disk

File existence ≠ feature completion. Always check content.

## Execution Steps

### 1. Run the delivery checklist

```bash
python -m modules.delivery.checklist --target . --json
```

Parse the JSON output. Group findings by category.

### 2. Run the 3-stage review

Invoke `/review` to get code/structure/risk review scores.

### 3. Consolidate into a single report

Show the user:
- Per-category pass/fail table
- Review scores
- Overall PASS/FAIL verdict
- If FAIL: ordered list of required fixes (most critical first)

### 4. If FAIL — do NOT claim done

Tell the user:
> The delivery check found N errors. The project is NOT ready for delivery
> until these are resolved:
> 1. [most critical fix]
> 2. [next fix]
> ...
>
> Would you like me to fix these now?

### 5. If PASS — explicitly confirm

Tell the user:
> ✅ Delivery check passed. All 6 categories are clean and the 3-stage
> review passed. The project is ready for delivery.

## Hard Rules

- Never claim "done" / "complete" / "delivered" while any ERROR-severity
  finding is open.
- Never claim "done" while any review stage has a critical finding (even if score ≥ 8/10).
- Never claim "done" while any review stage score < 8/10 (Phase 3 upgrade from 7/10).
- Never downgrade an error to a warning unilaterally — that's the user's call.
- Always show the full report, even if it's long. The user needs to see it.
- If the checklist module itself crashes, treat it as a FAIL (defensive).

## Pass Criteria (all three required)

1. Checklist: 0 errors (warnings/info acceptable)
2. Review: all 3 stages score ≥ 8/10
3. Review: zero critical findings across all stages

## Whitelist Handling

If `.opencode/delivery-whitelist.json` or `.opencode/integration-whitelist.json`
exists, apply it. Whitelisted items downgrade from `error` to `info` — they
still appear in the report but don't block delivery.

If a whitelisted item is later fixed, the whitelist entry becomes a no-op
(not an error).

## Exit Criteria

The delivery task is complete when:
- The user has seen the consolidated report
- The user has either:
  - Confirmed they want to fix the failures (transition back to coding mode)
  - Or accepted the PASS verdict (task truly done)
