# AgentForge

AgentForge is a Windows-oriented OpenCode workspace containing project
instructions, Prompt composition, MCP definitions, custom tools, reusable
skills, Python utilities, and optional local daemons.

This repository describes the working tree as of 2026-08-02. “Configured”,
“enabled”, “installed”, and “reachable” are separate states.

## Current inventory

| Area | Repository/working-tree fact |
|---|---|
| Models | `deepseek/deepseek-v4-pro` plus `deepseek/deepseek-v4-flash`; Vision requests `Qwen/Qwen3-VL-Plus` through SiliconFlow |
| MCP | 16 definitions: 14 enabled and 2 disabled |
| Plugins | 3 OpenCode plugin references; two use `latest` |
| Slash Commands | 11 Markdown procedures |
| Custom tools | 1 Vision export and 9 Browser exports |
| Review Subagents | 3 read-only definitions: code, structure, risk |
| Skills | 6 repository-owned skills; this machine also has 67 ignored junctions from 4 external repositories |
| Python | 13 domains, 122 first-party files (102 `.py`: 51 source and 51 test files) |
| Local services | Browser daemon at `127.0.0.1:9223`; Scheduler daemon at `127.0.0.1:9225` |

External skill junction contents are not part of Git. Their current local count
is an environment fact, not a guarantee for a clean clone.

## What is implemented

- five-layer Prompt composition, task/context detection, and experiment logs;
- three procedural, read-only review Subagents;
- image/PDF Vision and clipboard adaptation;
- a persistent Playwright/Firefox Browser daemon and nine HTTP client tools;
- Search privacy, dated history/cache, scoring, planning, aggregation, academic
  MCP servers, and callback-based orchestration helpers;
- stdlib `fetch`, read-only-by-default SQLite, and time MCP servers;
- schedule CRUD, action extraction, optional ChromaDB indexing, and an optional
  APScheduler daemon;
- TypeScript/Vite scaffold generation plus integration, UI, and delivery static
  checks;
- explicit library functions for appending local lessons/ADRs.

## Boundaries that must remain visible

- `/search` is a model-executed procedure. `SearchOrchestrator` exists but no
  production binding injects OpenCode MCP callbacks into it.
- Browser custom tools never start the daemon; run `/browser` first.
- The browser-use branch in `agent_wrapper.py` is a placeholder. Collection
  currently succeeds only through the separately running Browser daemon
  fallback.
- `memory_review` is a Scheduler placeholder; `pattern_extract` is reserved but
  has no execution branch.
- The memory hook is not registered automatically. Another caller must invoke
  it.
- Browser/Firefox paths remain machine-specific constants in
  `modules/browser/daemon.py`.
- Real secrets, profiles, memory, runtime data, external skills, caches, and
  vendored packages are intentionally absent from Git.

## Layout

```text
.opencode/          commands, agents, prompts, custom tools, owned/local skills
markconfig/         tracked safe examples and ignored local configuration
modules/            Python libraries, CLIs, MCP servers, and daemons
_data/memory/       tracked template/policy and ignored private memory
_docs/              maintained architecture, operations, audit, and roadmap
_runtime/           ignored generated state, logs, databases, reports, handoffs
opencode.json       OpenCode models, plugins, permissions, and MCP definitions
start-opencode.bat  portable repository-root launcher for Windows
```

## Setup

Requirements: Python 3.11+, Node.js/npm, OpenCode, and Firefox.

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
npm ci --prefix .opencode
python -m playwright install firefox
Copy-Item markconfig/secrets.example.json markconfig/secrets.json
Copy-Item markconfig/profile.example.md markconfig/profile.md
.\start-opencode.bat
```

The launcher expects `python`, `npm`/`npx`, and `opencode` on `PATH`. Adjust the
Firefox executable/profile constants in `modules/browser/daemon.py` and the two
Search health-check executable probes in `modules/search/search.py` for a
different machine. Some MCP processes require additional Python/npm packages or
API keys beyond merely being enabled in `opencode.json`.

## Documentation

- [`ONBOARDING.md`](ONBOARDING.md) — maintainer entry and invariants.
- [`_docs/ARCHITECTURE.md`](_docs/ARCHITECTURE.md) — current source of
  architectural truth.
- [`_docs/USER_GUIDE.md`](_docs/USER_GUIDE.md) — operational behavior.
- [`_docs/DEVELOPER.md`](_docs/DEVELOPER.md) — maintenance and verification.
- [`_docs/NON_CODE_AUDIT.md`](_docs/NON_CODE_AUDIT.md) — non-code issues found,
  resolved items, and remaining gaps.
- [`_docs/roadmap/ROADMAP.md`](_docs/roadmap/ROADMAP.md) — unfinished work only.
- [`_docs/roadmap/archive/README.md`](_docs/roadmap/archive/README.md) — rules
  for interpreting historical plans.

Historical reports and plans are evidence of earlier decisions, not proof of
current behavior or test status.
