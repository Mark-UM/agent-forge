# User Guide

## Start

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

Python 3.11 is required. Set `AGENT_FORGE_PYTHON` to an explicit executable if
`python` on `PATH` is different. The launcher validates the local dependency ABI
before composing the prompt and starting OpenCode. It does not start daemons.

## Commands

| Command | Purpose | Boundary |
|---|---|---|
| `/doctor` | configuration/dependency/Memory diagnostics | does not repair |
| `/review` | sequential code/structure/risk review | procedural |
| `/deliver` | TS/Vite static checks plus review | not one Python transaction |
| `/browser` | start loopback Playwright daemon | required only for daemon-backed tools |
| `/collect` | browser-use, Browser/Vision, then static-fetch URL report | reports actual backend |
| `/search` | provider-aware Search procedure | MCP callbacks are model-coordinated |
| `/index` | ChromaDB file index/search | local model/index state required |
| `/schedule` | SQLite schedule CRUD/action extraction | timed Scheduler daemon is separate |
| `/mode` | compose task/profile prompt | writes ignored generated state |
| `/prompt` | inspect prompt version/experiments | reads ignored logs |
| `/handoff` | manage context packets | packets may contain sensitive context |

## Browser and collection

Run `/browser` and verify `http://127.0.0.1:9223/ping` for Browser tools. The
daemon binds loopback, stores cookies/local storage under `_runtime/browser`,
and uses a visible window unless `AGENT_FORGE_BROWSER_HEADLESS=true`.

Browser selection is:

1. `AGENT_FORGE_BROWSER_ENGINE` (`chromium` by default; optional `firefox` or
   `webkit`);
2. the matching Playwright-managed browser under ignored `_runtime/`;
3. `AGENT_FORGE_BROWSER_EXECUTABLE` only when explicitly supplied as an
   override.

`/collect` tries browser-use first when an LLM key is available. If that fails,
it tries the running daemon plus Vision, then static fetch. A successful fallback
includes prior backend errors. The Vision path sees a screenshot, while static
fetch cannot execute client-side JavaScript.

## Memory

Private Memory files remain ignored local Markdown. There is no automatic
conversation capture.

```powershell
python -m modules.memory.hook health
python -m modules.memory.hook review
```

`health` returns structure/count metadata only. `review` scans explicit unchecked
tasks/TODO lines and writes an ignored report without modifying source Memory.

## Scheduler

Start timed jobs with `python -m modules.scheduler.daemon`. The service binds
`127.0.0.1:9225` and persists `_runtime/scheduler/jobs.json`. Supported jobs are
file reindex, report collection, Memory review, action extraction, and exact
whitelist custom calls. If APScheduler is unavailable, creation is refused; no
false-active job is saved.

## Privacy and troubleshooting

Secrets, profile, Memory, recovery evidence, vendor packages, and `_runtime/`
are ignored plain local files—not encrypted data.

- Dependency check fails: run the installer with Python 3.11 and inspect
  `import_error`; do not trust dist-info presence alone.
- Browser start fails: run `install-browser chromium`, verify the dependency
  report, then inspect explicit engine/executable overrides.
- Vision fails: verify PyMuPDF/Pillow imports and `SILICONFLOW_API_KEY`.
- browser-use falls back: verify a supported LLM key and inspect returned errors.
- Index fails: verify ChromaDB import and writable `_runtime/search/chroma`.
- Serper fails: verify `SERPER_API_KEY` and network access.
- External skills are absent on a clean clone by design; no tracked installer
  currently recreates them.
