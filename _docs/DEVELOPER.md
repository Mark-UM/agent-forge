# Developer Guide

Read `ONBOARDING.md`, `_docs/ARCHITECTURE.md`, and the applicable
`.opencode/prompts/tasks/*.md` before changing behavior.

## Environment

- Python 3.11 exactly for the supported local binary ABI;
- ignored runtime dependencies installed by
  `python -m modules.bootstrap.dependencies install`;
- pytest from `requirements-dev.txt`;
- Node.js/npm, OpenCode, and `.opencode/package-lock.json`;
- Playwright-managed Chromium for the Browser daemon (other engines optional).

`requirements.txt` pins direct runtime choices. `requirements.lock.txt` pins the
resolved local environment. Do not hand-copy package directories into vendor.
After changing direct dependencies, rebuild in an empty target, verify real
imports, and regenerate the lock from that target.

## Source and artifact rules

- Put generated state under `_runtime/`; never commit it.
- Keep real profile, Memory, and secrets ignored; do not duplicate personal
  facts into tracked prompts/docs/tests.
- Use atomic replacement for durable local JSON/Markdown writes.
- Bound network operations and return explicit degraded results.
- Keep daemons on loopback unless a separate authenticated design is approved.
- Validate resolved paths by directory boundaries, not string prefixes.
- Keep exactly one maintained architecture document.
- Put superseded plans under `_docs/roadmap/archive/` with an archive notice.
- Treat manifests as current contracts, not acceptance reports.
- Never perform recursive deletion from a pipeline whose file filter has not
  been independently verified and inventoried.

## Extension points

| Extension | Location |
|---|---|
| custom tools | `.opencode/tools/*.ts` |
| review agents | `.opencode/agents/*.md` |
| commands | `.opencode/commands/*.md` |
| prompt layers | `.opencode/prompts/` |
| owned skills | `.opencode/skills/<name>/SKILL.md` |
| Python components | `modules/<domain>/` |

When changing an extension, update its direct runtime description, manifest,
architecture boundary, dependencies, ignore policy, and relevant tests.

## Verification

```powershell
python -m modules.bootstrap.dependencies check --json
python -m compileall -q modules
python -m pytest -q
python -m modules.memory.hook health
python -m modules.scheduler.daemon --check
python -m modules.search.orchestrator "example" --dry-run
git diff --check
```

The integration/UI/delivery checkers target configured TypeScript/Vite
conventions; running them against AgentForge is not a universal quality gate.

After code changes, perform the sequential code, structure, and risk review
required by project instructions. Derive documentation counts from the tree,
derive MCP state from `opencode.json`, distinguish installed/reachable states,
and cite only test results from the current run.
