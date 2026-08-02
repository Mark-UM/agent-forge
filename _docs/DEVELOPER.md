# Developer Guide

Read `ONBOARDING.md` and `_docs/ARCHITECTURE.md` before changing behavior.

## Environment

- Python 3.11+ and dependencies from `requirements.txt`;
- pytest from `requirements-dev.txt`;
- Node.js/npm, OpenCode, and `.opencode/package-lock.json`;
- Firefox plus Playwright Firefox for the local Browser daemon.

The launcher/config use repository-relative process paths, but
`modules/browser/daemon.py` still contains machine-specific Firefox constants.

## Source and artifact rules

- Preserve lazy imports/fallbacks where optional packages are intentional.
- Put generated state under `_runtime/` and never commit it.
- Keep real profile/memory/secrets ignored and do not duplicate personal facts
  into tracked prompts.
- Add external dependencies to the appropriate requirements/package manifest.
- Use atomic replacement for durable local JSON/Markdown writes.
- Bound network operations with timeouts and explicit degraded results.
- Keep exactly one maintained architecture document.
- Place superseded plans under `_docs/roadmap/archive/` with an archive notice.
- Manifests describe current components/limitations; they are not acceptance
  reports.

## Extension points

| Extension | Location |
|---|---|
| custom tools | `.opencode/tools/*.ts` |
| review Subagents | `.opencode/agents/*.md` |
| Slash Commands | `.opencode/commands/*.md` |
| Prompt layers | `.opencode/prompts/` |
| repository-owned skills | `.opencode/skills/<name>/SKILL.md` |
| Python components | `modules/<domain>/` |

When adding/changing an extension, update its direct runtime description,
manifest (when present), architecture boundary, dependencies, ignore policy,
and relevant tests.

## Verification

Choose checks proportionate to the change:

```powershell
python -m pytest
python -m modules.prompt.composer --list-tasks
python -m modules.scheduler.daemon --check
python -m modules.search.orchestrator "example" --dry-run
python -m modules.delivery.checklist --target <ts-vite-project> --json
```

Static integration/UI/delivery tools are designed for their configured
TypeScript/Vite conventions; running them against AgentForge itself is not a
meaningful universal quality gate.

After code modifications, use the sequential code → structure → risk review
defined by project instructions. Documentation-only changes still require
link/path/count/config validation.

Before committing documentation, derive counts from the tree, derive MCP state
from `opencode.json`, distinguish optional/configured/working, and remove test
claims not backed by a current run.
