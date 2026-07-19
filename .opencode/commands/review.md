---
description: Trigger full code review pipeline (review-code → review-structure → review-risk)
agent: build
subtask: true
---

# /review — Full Code Review Pipeline

Trigger the multi-stage review pipeline on recent code changes.

## Pipeline

1. **review-code** subagent: Check logic errors, bugs, exception handling, boundary conditions, naming, code style.
2. **review-structure** subagent: Check file organization, dependency integrity, file placement, module coupling, config consistency.
3. **review-risk** subagent: Check security risks, dangerous operations, data loss potential.

## Execution

Call each subagent sequentially. After all three complete, produce a summary report:

```
## Review Summary
| Stage | Result | Score | Key Findings |
|-------|--------|-------|--------------|
| Code  | PASS/FAIL | x/10 | ... |
| Structure | PASS/FAIL | x/10 | ... |
| Risk | PASS/FAIL | x/10 | ... |

## Overall: PASS/FAIL
## Required Fixes (if any):
1. ...
```

If any stage FAILs, list the specific issues that need fixing.
