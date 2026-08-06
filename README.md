# Agent Forge

Agent Forge is a private, Windows-first local Agent workspace built around
OpenCode. It provides one practical execution layer for prompting, model
routing, search, browser collection, PDF/image understanding, local scheduling,
private memory, delivery checks, and GitHub-oriented development work.

The **pre-2.0 Windows-first foundation is complete**. Development is now
entering the 2.0 Lightweight Multi-Agent Kernel; the new kernel will reuse
rather than replace the working single-Agent capabilities described here.

## What is implemented

### Prompt and Context

- layered Prompt composition for Base, Task, Context, Profile, Example, and
  explicit extra instructions;
- automatic project-context detection with a project fingerprint;
- task classification with a deterministic local fallback;
- generated `AGENTS_COMPOSED.md` used at OpenCode startup.

### Unified model access

- one DeepSeek-compatible Model Gateway for Pro/Flash routing;
- bounded timeout and retry behavior;
- outbound secret redaction;
- structured results and local Run/Step/Event telemetry;
- a CI no-bypass check that prevents first-party business modules from
  reintroducing direct DeepSeek endpoints.

### Search

- typed Search requests, plans, provider executions, results, and verification;
- mode-aware provider ordering and fallback;
- explicit distinction between execution success and usable output;
- one production Search factory shared by MCP, CLI, and library entry points;
- authoritative SQLite result/provider cache;
- old `pipeline_cache.json` and `search_cache.json` are import-only migration
  sources and are no longer production write targets.

### Browser, Collection, and Vision

- authenticated loopback Playwright Browser daemon;
- fail-closed URL/DNS/IP checks and subresource interception;
- visible DOM extraction, bounded lazy-load scrolling, screenshot fallback, and
  secure static Fetch fallback;
- bounded image/PDF recognition through Qwen3-VL;
- page-aware cross-batch PDF reduction with provenance, duplicate removal, and
  explicit degraded output for partial documents;
- unguarded `browser-use` is disabled by default and never receives a direct
  DeepSeek transport.

### Scheduler and Runtime

- SQLite-backed Cron, Date, and Interval jobs;
- one authoritative `run_id` for every execution attempt;
- timezone-aware input normalized to UTC;
- authenticated Browser and Scheduler services;
- Runtime Supervisor for start, status, bounded restart, stop, logs, and
  foreground-command cleanup;
- `start-opencode.bat` runs OpenCode through the Supervisor.

### Private Memory

- explicit atomic lessons and ADR-style decisions;
- read-only Memory health and open-action review;
- approval-gated Memory Candidates with pending, approved, rejected, and
  expired states;
- candidate content, source, and metadata are checked for common secrets before
  SQLite persistence;
- durable candidate Memory is written only after explicit approval.

### Delivery and project checks

- generic, Python, TypeScript/Vite, Three.js, and Tower Stack profiles;
- specialized rules activate only when project evidence or an explicit profile
  requires them;
- historical strict UI/Delivery checkers remain explicit compatibility commands,
  not universal defaults.

## Canonical runtime flow

```text
start-opencode.bat
        |
        +-- validate Python 3.11 and local Vendor dependencies
        +-- load ignored local secrets and profile
        +-- compose AGENTS_COMPOSED.md
        +-- Runtime Supervisor
               |
               +-- authenticated Browser daemon
               +-- authenticated Scheduler daemon
               +-- OpenCode foreground process
```

The principal capability path is:

```text
OpenCode / CLI / MCP
        |
        +-- Model Gateway
        +-- Search Service
        +-- Collection + Vision
        +-- Scheduler
        +-- Memory CLI
        +-- Delivery Profiles
        |
        +-- Run / Step / Event telemetry
        |
        +-- _runtime/mcp-sqlite.db and bounded local artifacts
```

## Windows setup

Use **Python 3.11**. Native dependencies installed for another Python ABI are
not interchangeable.

```powershell
python --version
python -m modules.bootstrap.dependencies install
python -m modules.bootstrap.dependencies check --json
python -m modules.bootstrap.dependencies install-browser chromium
Copy-Item markconfig/secrets.example.json markconfig/secrets.json
Copy-Item markconfig/profile.example.md markconfig/profile.md
.\start-opencode.bat
```

The bootstrap installer builds the ignored `vendor/python-libs` environment from
`requirements.lock.txt` and records the Python ABI. Browser binaries are kept
under ignored runtime storage.

## Useful commands

```powershell
# Search
python -m modules.search.search service-health
python -m modules.search.search pipeline --query "your query" --mode standard

# Runtime services
python -m modules.runtime.supervisor status --json
python -m modules.runtime.supervisor start --services scheduler,browser --json
python -m modules.runtime.supervisor stop --json

# Memory
python -m modules.memory.cli health
python -m modules.memory.cli candidates list
python -m modules.memory.cli candidates add "A reusable lesson" --kind lesson
python -m modules.memory.cli candidates approve <candidate-id>

# Profile-aware delivery checks
python -m modules.delivery --project-root . --json

# Repository validation
python -m compileall -q modules
python -m modules.registry validate --strict
python -m modules.dispatch.no_bypass
python -m pytest -q
```

## Local state

| Location | Purpose |
|---|---|
| `markconfig/secrets.json` | ignored local credentials |
| `markconfig/profile.md` | ignored local preferences |
| `_data/memory/` | private local Memory files |
| `_runtime/mcp-sqlite.db` | Scheduler, Search cache, and candidate state |
| `_runtime/supervisor/` | non-secret process state and service logs |
| `_runtime/reports/` | generated collection and review reports |
| `_runtime/search/` | non-authoritative history, migration inputs, and derived metrics |
| `vendor/python-libs/` | ignored Python 3.11 Vendor environment |

## Essential safety boundaries

Agent Forge deliberately keeps the safety model small:

1. secrets and private content must not enter Git, logs, telemetry, or outbound
   prompts;
2. file writes stay inside the intended workspace and destructive operations
   must be bounded and recoverable;
3. untrusted URLs cannot reach loopback, private, link-local, or cloud metadata
   targets;
4. network requests, file sizes, retries, subprocesses, and execution time have
   explicit limits.

The project does not pursue enterprise RBAC, distributed infrastructure, or
approval ceremony for ordinary local read-only work.

## Validation

GitHub Actions treats Windows as the release platform and runs:

- fast security and integration contracts;
- strict Registry validation;
- Model Gateway no-bypass validation;
- the complete Windows pytest suite;
- a clean Vendor install, Chromium installation, and real Browser/Scheduler
  Supervisor smoke.

The authoritative report is generated from the JUnit XML for the exact tested
commit and uploaded as a workflow artifact. See
[`_docs/TEST_REPORT.generated.md`](_docs/TEST_REPORT.generated.md) for the
reporting contract and last finalized baseline.

## Documentation

- [`ONBOARDING.md`](ONBOARDING.md) — maintainer invariants.
- [`_docs/ARCHITECTURE.md`](_docs/ARCHITECTURE.md) — current architecture.
- [`_docs/USER_GUIDE.md`](_docs/USER_GUIDE.md) — operating behavior.
- [`_docs/DEVELOPER.md`](_docs/DEVELOPER.md) — maintenance workflow.
- [`_docs/AI_MAINTAINER_PLAYBOOK.md`](_docs/AI_MAINTAINER_PLAYBOOK.md) — verified AI-maintenance lessons.
- [`_docs/roadmap/ROADMAP.md`](_docs/roadmap/ROADMAP.md) — unfinished work and the 2.0 release train.
