# AgentForge

AgentForge is a Windows-oriented OpenCode workspace containing prompt policy,
MCP definitions, custom tools, reusable skills, Python utilities, and two
optional local daemons. This repository describes the working tree as of
2026-08-04; configured, installed, authenticated, reachable, and tested are
separate states.

## Current inventory

| Area | Repository fact |
|---|---|
| Models | `deepseek/deepseek-v4-pro`, `deepseek/deepseek-v4-flash`; Vision uses `Qwen/Qwen3-VL-Plus` through SiliconFlow |
| MCP | 16 definitions: 14 enabled, 2 disabled |
| Plugins | 3 OpenCode references; some remain runtime-resolved |
| Commands | 11 Markdown procedures |
| Custom tools | 2 TypeScript files exporting 1 Vision and 9 Browser tools |
| Review agents | 3 read-only definitions: code, structure, risk |
| Skills | 6 repository-owned skills; ignored upstream clones/junctions are local environment state |
| Python modules | 125 first-party files; 108 Python files (53 source, 55 tests) |
| Local services | Browser `127.0.0.1:9223`; Scheduler `127.0.0.1:9225` |

## Implemented behavior

- five-layer prompt composition with explicit priority declaration, routing
  helpers, and experiment logs;
- image/PDF Vision with configurable resource limits (file size, page count,
  pixel caps, request batching) plus clipboard adaptation;
- Playwright/Chromium Browser daemon with project-local cookie/storage state;
- URL collection through browser-use, Browser/Vision, and static-fetch
  fallbacks with backend/error provenance;
- Search pipeline with typed contract model (`sub_queries` canonical field),
  in-pipeline `aggregator_fn`/`cache_store_fn` invocation, verification in
  formatted output, DST-aware prewarm timestamps, and parallel timeout with
  `abandoned` marking;
- stdlib fetch (chunked read with byte limit), read-only-by-default SQLite,
  and time MCP (aware datetime via `astimezone()`, DST gap detection);
- APScheduler jobs with single SQLite state source (legacy `jobs.json`
  migrated), action extraction with `{"actions": [...]}` schema and
  `due_at` ISO 8601 validation, ChromaDB indexing;
- explicit atomic Memory lessons/ADRs, health checks, and read-only task review;
- TypeScript/Vite scaffold generation and static integration/UI/delivery checks;
- prompt reference checker validating all cross-references (agents, skills,
  commands, modules) across the repository.

## Important boundaries

- `/search`, `/review`, and `/deliver` are model-executed procedures. Their
  Markdown coordinates code/tools; it is not a hidden transactional engine.
- Browser tools do not auto-start the daemon. Run `/browser` for daemon-backed
  operations. Static fetch remains available to `/collect` without it.
- browser-use requires a supported LLM credential. A package import alone does
  not prove that autonomous collection can run.
- Memory writes are explicit. There is no automatic conversation capture.
- Private memory, secrets, profile, runtime state, dependency files, caches,
  and upstream skill clones are ignored rather than encrypted.
- Archived plans and acceptance reports record earlier intent. Only
  `_docs/ARCHITECTURE.md` describes current architecture.

## Layout

```text
.opencode/          commands, agents, prompts, tools, and skills
markconfig/         tracked safe examples plus ignored local configuration
modules/            Python libraries, CLIs, MCP servers, and daemons
_data/memory/       tracked policy/example plus ignored private memory
_docs/              maintained docs, recovery history, and archived plans
_runtime/           ignored logs, databases, reports, recovery evidence
vendor/python-libs/ ignored, reproducibly rebuilt Python environment
```

## Setup

Use Python 3.11. Python 3.12/3.13 wheels are not interchangeable with the
project's native Python 3.11 packages.

```powershell
python --version
python -m modules.bootstrap.dependencies install
python -m modules.bootstrap.dependencies check --json
python -m modules.bootstrap.dependencies install-browser chromium
python -m pip install -r requirements-dev.txt
npm ci --prefix .opencode
Copy-Item markconfig/secrets.example.json markconfig/secrets.json
Copy-Item markconfig/profile.example.md markconfig/profile.md
.\start-opencode.bat
```

The dependency installer writes the fully resolved set into ignored
`vendor/python-libs`, records the interpreter ABI, and defaults to
`requirements.lock.txt`. Set `AGENT_FORGE_PYTHON` when `python` on `PATH` is not
the intended Python 3.11 executable. The launcher refuses missing, broken, or
ABI-incompatible dependencies.

The Browser daemon defaults to the Playwright-managed Chromium installed under
ignored `_runtime/playwright-browsers`. `AGENT_FORGE_BROWSER_ENGINE` selects
`chromium`, `firefox`, or `webkit`; `AGENT_FORGE_BROWSER_EXECUTABLE` is an
explicit system-browser override. Clean shutdowns persist cookies and local
storage under the ignored `AGENT_FORGE_BROWSER_PROFILE` state directory.

## Documentation

- [`ONBOARDING.md`](ONBOARDING.md) — maintainer invariants.
- [`_docs/ARCHITECTURE.md`](_docs/ARCHITECTURE.md) — current architecture.
- [`_docs/USER_GUIDE.md`](_docs/USER_GUIDE.md) — operating behavior.
- [`_docs/DEVELOPER.md`](_docs/DEVELOPER.md) — maintenance and verification.
- [`_docs/NON_CODE_AUDIT.md`](_docs/NON_CODE_AUDIT.md) — documentation/config audit.
- [`_docs/history/2026-08-02-recovery-audit.md`](_docs/history/2026-08-02-recovery-audit.md) — deletion evidence and recovery confidence.
- [`_docs/roadmap/ROADMAP.md`](_docs/roadmap/ROADMAP.md) — unfinished work only.
