---
description: Three-stage sequential code review (Code + Structure + Risk)
task_type: review
leading_words: [review, smell, judgment-call, structure, risk]
priority: 100
version: 2.0.0
---

# Code Review Task

## Trigger
- User requests reviewing code / PR / diff
- Keywords: "审查" / "review" / "PR" / "diff"
- Calling `/review` command

## Three-stage sequential review

Reviews execute **sequentially**, not in parallel. Each stage has a dedicated
read-only sub-agent. Any stage FAIL → fix and re-review (max 2 retry rounds
per stage).

### Stage 1: review-code
Does the code have logic errors, bugs, exception handling gaps, boundary
condition issues, naming problems, or style violations?

Check:
- Logic errors and incorrect control flow
- Unhandled exceptions or overly broad catch blocks
- Boundary conditions (empty input, off-by-one, overflow)
- Naming clarity and consistency
- Code style conformance to repo standards

### Stage 2: review-structure
Is the file organization, dependency integrity, file placement, module
coupling, and config consistency correct?

Check:
- File placement matches module boundaries
- Import dependencies are acyclic and minimal
- Module coupling is appropriate (no hidden circular deps)
- Config consistency across files (e.g., package.json vs tsconfig)
- No orphaned files or dead imports

### Stage 3: review-risk
Are there security risks, dangerous operations, or data loss potential?

Check:
- Security vulnerabilities (injection, SSRF, path traversal)
- Dangerous operations (force delete, overwrite without backup)
- Data loss potential (non-atomic writes, missing rollback)
- Sensitive data exposure in logs or outputs

## Process

1. **Pin the fixed point**: `git diff <fixed-point>...HEAD` (three-dot, against merge-base)
2. **Identify spec source**: issue references / PRD file / ask user
3. **Run review-code** → wait for result → if FAIL, fix and re-run (max 2 retries)
4. **Run review-structure** → wait for result → if FAIL, fix and re-run (max 2 retries)
5. **Run review-risk** → wait for result → if FAIL, fix and re-run (max 2 retries)
6. **Aggregate**: present findings under `## Code`, `## Structure`, and `## Risk` headings

## Completion criteria
- Total findings per stage reported
- Worst issue within each stage identified
- Final verdict: PASS only if all three stages pass
- Simple Q&A / chat → skip review entirely

## Agent configuration

All three review agents are defined in `.opencode/agents/`:
- `review-code.md` — uses deepseek-v4-flash, read-only (no edit/write/bash)
- `review-structure.md` — uses deepseek-v4-flash, read-only
- `review-risk.md` — uses deepseek-v4-flash, read-only

Deprecated agent names are enforced by the prompt reference checker
(`modules/prompt/tests/test_prompt_references.py`) and must not appear
in any file under `.opencode/`.
