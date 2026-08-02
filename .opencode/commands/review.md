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

For each confirmed blocking defect, explicitly call
`modules.memory.hook.append_lesson` with a short technical lesson and source
path after removing credentials and personal data. Do not write review prose or
speculation into memory, and do not record anything when the review itself could
not run.

## Post-Review Hook (v1.5 P2)

After the 3-stage review pipeline completes, **automatically log an experiment**
to enable A/B testing statistics in `/prompt stats`.

### Process

1. Parse the final Overall Score (e.g., 8.5/10) from the review summary
2. Count findings by severity:
   - `critical`: issues that must be fixed (FAIL triggers)
   - `major`: significant issues but not blocking
   - `minor`: style / suggestions
3. Detect current task type + profile from `_runtime/prompt/version.json` (or default to `coding` / `default`)
4. Call the experiments logger:

```bash
python -m modules.prompt.experiments --log \
  --task <task_type> \
  --profile <profile> \
  --description "<short task description from session>" \
  --score <overall_score> \
  --findings-critical <count> \
  --findings-major <count> \
  --findings-minor <count>
```

5. Confirm to user:
   > Experiment logged (exp-XXXX). Use `/prompt stats` to view trends.

### What gets logged

```json
{
  "experiment_id": "exp-1700000000",
  "timestamp": "2026-07-20T14:30:00",
  "prompt_version": "1.5.0",
  "task_type": "coding",
  "profile": "default",
  "contexts": ["python"],
  "examples": ["python/test-driven"],
  "task_description": "Implement fibonacci function",
  "outcome": {
    "review_score": 8.5,
    "review_findings_critical": 0,
    "review_findings_major": 1,
    "review_findings_minor": 3,
    "duration_seconds": null,
    "tokens_used": null,
    "user_satisfied": null
  },
  "user_feedback": null
}
```

### Skip conditions

- Simple Q&A / chat (no code modifications) → skip experiment logging
- User explicitly says "skip review" → skip both review and logging
- Review pipeline itself fails (network error, etc.) → log with `score=null`

### Manual logging

Users can also manually log an experiment:

```bash
python -m modules.prompt.experiments --log \
  --task coding --profile default \
  --description "custom task" \
  --score 9.0 --findings-critical 0 --findings-major 0 --findings-minor 1 \
  --satisfied yes --feedback "clean implementation"
```
