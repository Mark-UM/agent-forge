# AgentForge Onboarding

Use this file as the maintainer entry point. Establish current behavior from
code/configuration before consulting archived plans.

## Reading order

1. `AGENTS.md` and `AGENTS_BASE.md`;
2. generated `AGENTS_COMPOSED.md`, when the launcher has created it;
3. ignored `markconfig/profile.md`, when present;
4. `README.md` and `_docs/ARCHITECTURE.md`;
5. the task/context Prompt relevant to the change.

`_docs/history/` and `_docs/roadmap/archive/` are never current specifications.

## Invariants

- Never commit real secrets, profiles, private memory, runtime state, databases,
  logs, external skill junctions/clones, caches, or vendored dependencies.
- Image/PDF recognition uses the custom Vision path required by project policy.
- Review Subagents are read-only and the three stages run sequentially when the
  workflow requires review.
- Slash Commands are Markdown procedures unless an individual command explicitly
  invokes a Python CLI.
- Runtime writes go under `_runtime/`; maintained public documentation goes
  under `_docs/`.
- Optional dependencies must be declared in `requirements.txt`; test-only
  dependencies belong in `requirements-dev.txt`.
- When prose conflicts with code/config, correct the maintained prose and record
  the gap rather than preserving an unsupported claim.

## Component map

| Path | Responsibility |
|---|---|
| `opencode.json` | model/plugin/MCP configuration and project permissions |
| `start-opencode.bat` | local secret export, Prompt composition, OpenCode launch |
| `.opencode/prompts/` | base/profile/task/context/example Prompt layers |
| `.opencode/commands/` | 11 agent-interpreted procedures |
| `.opencode/agents/` | 3 read-only review definitions |
| `.opencode/tools/` | Vision tool plus Browser HTTP clients |
| `.opencode/skills/` | 6 owned skills and optional ignored external junctions |
| `modules/search/` | Search v4.4 libraries and three configured MCP servers |
| `modules/mcp/` | local fetch/SQLite/time MCP servers |
| `modules/browser/`, `modules/vision/` | Browser daemon and Vision adapters |
| `modules/prompt/`, `modules/dispatch/` | Prompt composition and model-role policy |
| `modules/orchestrator/`, `modules/scheduler/` | local automation helpers/daemon |
| `modules/memory/` | explicit append/health library; no automatic registration |
| `modules/bootstrap/` | nine-file TypeScript/Vite scaffold generator |
| `modules/integration_check/`, `modules/ui_check/`, `modules/delivery/` | opinionated static checks |

## Main flows

- Startup: launcher resolves its own repository directory, reads four selected
  secrets, composes `AGENTS_COMPOSED.md`, then starts OpenCode.
- Search: `/search` selects currently enabled MCP providers and may call Python
  helpers. The standalone orchestrator needs injected callbacks.
- Browser: `/browser` starts the localhost daemon; tools only send HTTP calls.
- Collection: current code probes an unfinished browser-use path, then falls
  back to Browser daemon → screenshot → Vision → aggregation → report.
- Delivery: static Python checks and the three review Subagents are separate
  mechanisms coordinated by `/deliver`.

## Known gaps

- production `/search` callback wiring;
- browser-use LLM integration;
- Scheduler `memory_review` and `pattern_extract` implementations;
- portable Firefox path/profile configuration;
- tracked external-skill installer and reproducible plugin/MCP version pinning;
- CI and automated documentation/config consistency checks.

The active roadmap tracks these items without version promises.
