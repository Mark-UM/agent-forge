# Non-Code Audit

Audit date: 2026-08-02. Scope: tracked documentation, Markdown commands/skills,
JSON manifests/configuration, dependency declarations, ignore policy, recovery
evidence, and repository organization. This document records current verified
state; archived plans remain historical evidence only.

## Resolved inconsistencies

| Problem found | Current resolution |
|---|---|
| Several documents independently claimed to be the current architecture | `_docs/ARCHITECTURE.md` is the single maintained architecture reference; superseded `PROJECT_DOC.md`, `SYSTEM.md`, and `TECHNICAL.md` were removed |
| Active, completed, and speculative upgrade plans were mixed together | unfinished work is in `_docs/roadmap/ROADMAP.md`; historical proposals and acceptance reports are under `_docs/roadmap/archive/` with explicit status warnings |
| Browser docs claimed auto-start, system Firefox discovery, and a personal persistent profile | commands and guides now state explicit daemon startup, Playwright-managed Chromium, ignored cookie/local-storage state, and exact engine/executable overrides |
| Collection documentation described a placeholder browser-use branch as functional | the branch now executes the installed `browser_use.Agent`; docs describe its credential requirement and actual daemon/static-fetch fallbacks |
| Memory documentation implied automatic conversation capture | all active docs now state that lesson/ADR writes are explicit, review is read-only, and private Memory remains ignored |
| Scheduler documentation advertised nonexistent or false-success work | active docs list only the five implemented job types and explain degraded refusal, restore-error state, and atomic persistence |
| Runtime requirements did not map to actual imports | `requirements.txt` pins seven direct packages; `requirements.lock.txt` pins the complete 152-distribution Python 3.11 environment |
| Empty vendor package directories could appear healthy | Dependency health performs real imports, validates local source origin and ABI metadata, and resolves `browser_use.Agent` rather than trusting its lazy namespace |
| Launcher secret extraction and Python selection were machine-fragile | the launcher accepts `AGENT_FORGE_PYTHON`, requires Python 3.11, uses a tested batch-safe secret loader, and refuses unhealthy local dependencies |
| Tracked personal paths/facts and broad filesystem MCP scope contradicted privacy claims | real secrets/profile/Memory remain ignored, tracked examples are safe, obsolete `markconfig/paths.json` was removed, and filesystem scope is the repository |
| Generated/runtime/vendor/upstream skill trees polluted portability | ignore rules cover those trees; owned assets and optional local environment state are distinguished in the inventory |

## Recovery integrity

- The deletion affected `modules/` and `vendor/`; private `_data/memory` was not
  targeted or rewritten.
- `_runtime/module-recovery` still contains 118 files. Their Git object hashes
  match all 118 `modules/` entries in the local OpenCode snapshot index with
  zero missing entries and zero mismatches.
- The local snapshot is not a GitHub checkout. The older GitHub tree contained
  only four relevant module files and was not used to overwrite the recovery.
- `_runtime/cleanup-quarantine-20260802` remains preserved and ignored.
- `vendor/python-libs` is a reconstruction, not a byte-for-byte recovery. It is
  now a Python 3.11 (`cpython-311`) environment whose 152 installed
  distributions exactly match the tracked lock file.

## Current verified configuration

- First-party `modules/` inventory: 124 files, including 107 Python files (53
  source and 54 tests).
- OpenCode extension inventory: 11 commands, 3 review agents, 2 TypeScript tool
  files with 10 exports, 6 owned skills, and 16 MCP definitions (14 enabled).
- Local service bindings: Browser `127.0.0.1:9223`; Scheduler
  `127.0.0.1:9225`.
- Dependency imports are local and healthy for APScheduler 3.11.3,
  browser-use 0.13.6, ChromaDB 1.5.9, Playwright 1.62.0, Pillow 12.2.0,
  PyMuPDF 1.28.0, and mcp-server-git 2026.7.10.
- Browser-use telemetry and cloud sync default to disabled in the project
  activation layer. Reports/screenshots are confined to ignored runtime or OS
  temporary storage.

## Remaining known gaps

- No CI currently runs the documented verification suite.
- Several npm/plugin references are runtime-resolved rather than locked.
- SearXNG uses a fixed public instance; provider availability is external.
- No tracked installer recreates optional upstream skill clones/junctions.
- Browser and Scheduler lifecycle supervision is manual.
- Playwright browser binaries are intentionally ignored and must be installed
  on a clean clone with `python -m modules.bootstrap.dependencies
  install-browser chromium`.
- Configuration/import success does not prove that every API key, MCP, model,
  public endpoint, or external network path is currently reachable.

## Verification evidence

The following checks completed successfully on 2026-08-02 with the configured
Python 3.11 interpreter:

- `python -m pytest -q`: **1,768 passed**, zero failed, zero skipped;
- `python -m compileall -q modules`;
- all 15 tracked JSON files parsed as UTF-8 JSON;
- Dependency report: compatible `cpython-311`, all seven required imports local
  and healthy;
- full lock comparison: all 152 lock entries equal the installed vendor freeze;
- headless Chromium smoke: real page/context creation, DOM read, and clean close;
- BrowserUse, DeepSeek-compatible, and Anthropic LLM adapter construction;
- Memory health: all expected files present, with content excluded from output;
- Scheduler health: APScheduler 3.11.3 and the five documented job types;
- `start-opencode.bat --version`: dependency gate, secret loading, prompt
  composition, and OpenCode 1.18.4 startup path succeeded;
- `git diff --check`, recovery snapshot hash comparison, and ignore/privacy
  checks.

No claim is made here that external MCP/API endpoints were contacted or that
archived benchmarks remain reproducible.
