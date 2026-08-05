# Agent Forge User Guide

Agent Forge is operated locally on Windows through `start-opencode.bat`. The
launcher validates Python/Vendor state, loads ignored local configuration,
composes the active Prompt, and runs OpenCode through the Runtime Supervisor.

## 1. First setup

Use Python 3.11:

```powershell
python --version
python -m modules.bootstrap.dependencies install
python -m modules.bootstrap.dependencies check --json
python -m modules.bootstrap.dependencies install-browser chromium
Copy-Item markconfig/secrets.example.json markconfig/secrets.json
Copy-Item markconfig/profile.example.md markconfig/profile.md
```

Edit only the ignored local files:

- `markconfig/secrets.json` for credentials;
- `markconfig/profile.md` for user preferences.

Never place real secrets in tracked Markdown, Manifest, test, or example files.

## 2. Start and stop

```powershell
.\start-opencode.bat
```

Normal startup supervises authenticated Browser and Scheduler services. Missing
optional capabilities may be reported as degraded rather than preventing the
OpenCode session.

Inspect services:

```powershell
python -m modules.runtime.supervisor status --json
```

Manual lifecycle commands:

```powershell
python -m modules.runtime.supervisor start --services scheduler,browser --json
python -m modules.runtime.supervisor restart --services scheduler,browser --json
python -m modules.runtime.supervisor stop --json
```

The Supervisor stops only processes it owns. An already-running authenticated
service is adopted for health observation and is not killed on exit.

## 3. Search

The Search Pipeline MCP, CLI, and Python API use the same production Service.

```powershell
python -m modules.search.search service-health
python -m modules.search.search pipeline --query "your query" --mode standard
python -m modules.search.search pipeline --query "paper topic" --mode academic
```

Modes:

- `quick`: first sufficient ready provider;
- `standard`: normal fallback;
- `deep`: multiple providers and aggregation;
- `academic`: arXiv/Semantic Scholar first.

A completed pipeline with no usable results is reported as `no_results`, not as
success.

Search cache state is stored in `_runtime/mcp-sqlite.db`. Old JSON caches are
migration inputs only. Search history and derived quality metrics remain under
`_runtime/search/`.

## 4. Browser and collection

The Browser daemon is authenticated and loopback-only. Use OpenCode Browser
commands or the Collection pipeline during normal work.

Standalone status:

```powershell
python -m modules.browser.daemon --status
python -m modules.orchestrator.agent_wrapper status
```

Standalone collection:

```powershell
python -m modules.orchestrator.agent_wrapper collect https://example.com
```

Collection normally uses:

```text
authenticated Browser daemon -> secure static Fetch
```

It extracts visible DOM text, performs bounded lazy-load scrolling, and uses a
screenshot/Vision fallback when required. Private, loopback, link-local, and
cloud metadata targets are blocked.

`browser-use` is an explicit reduced-security override and is disabled by
default. It is not required for normal collection.

## 5. Vision and PDF

```powershell
python -m modules.vision.recognize image.png "Describe this image"
python -m modules.vision.recognize document.pdf "Summarize this document"
```

PDF rendering and API batches have file, page, pixel, and request-size limits.
Multi-batch output contains page-source comments and reports missing or failed
page ranges as degraded output.

Set limits through `VISION_MAX_*` environment variables only when the local
machine has enough memory and the larger request is intentional.

## 6. Scheduler

Scheduler supports Cron, one-shot Date, and fixed Interval triggers. Job and
execution state live in `_runtime/mcp-sqlite.db`.

All persisted execution timestamps are UTC. Input timezone precedence is:

```text
request timezone -> AGENT_FORGE_USER_TZ -> system timezone -> UTC
```

`tzdata` is a direct dependency so IANA timezones also work on Windows.

Manual runs return one `run_id`; poll that same ID until it reaches a terminal
state.

## 7. Private Memory

Health and review:

```powershell
python -m modules.memory.cli health
python -m modules.memory.cli review
```

Candidate workflow:

```powershell
python -m modules.memory.cli candidates add "Reusable lesson" --kind lesson
python -m modules.memory.cli candidates list --status pending
python -m modules.memory.cli candidates approve <candidate-id>
python -m modules.memory.cli candidates reject <candidate-id> --note "obsolete"
python -m modules.memory.cli candidates expire
```

Candidate data is checked for common secrets before SQLite persistence. Durable
Markdown is written only after explicit approval. Agent Forge does not capture
all conversations automatically.

## 8. Delivery checks

Run the profile-aware checker:

```powershell
python -m modules.delivery --project-root . --json
```

Auto-detected profiles include generic, Python, TypeScript/Vite, Three.js, and
Tower Stack. Specialized rules are not applied to unrelated projects.

Historical strict checkers remain explicit compatibility commands:

```powershell
python -m modules.delivery.checklist --help
python -m modules.ui_check.enforcer --help
```

## 9. Local state and recovery

Important ignored state:

```text
_runtime/mcp-sqlite.db
_runtime/supervisor/
_runtime/reports/
_runtime/search/
_runtime/prompt/
_data/memory/
vendor/python-libs/
```

Before maintenance that changes SQLite schemas or private Memory, stop the
Supervisor and make a local backup. Never delete broad directory patterns. A
cleanup operation should list exact resolved targets first and preserve a
recovery manifest.

## 10. Troubleshooting

### Dependency or ABI failure

```powershell
python -m modules.bootstrap.dependencies install
python -m modules.bootstrap.dependencies check --json
```

### Browser binary missing

```powershell
python -m modules.bootstrap.dependencies install-browser chromium
```

### Service port occupied

```powershell
python -m modules.runtime.supervisor status --json
```

The Supervisor reports whether the port is occupied by the expected
authenticated service or by an unrelated process.

### Search returns no results

Use `service-health`, inspect provider readiness/credentials, and distinguish
`no_results` from provider failure. Do not treat an empty array as a successful
provider call.

### Local release smoke

```powershell
.\scripts\windows-release-smoke.ps1
```

This validates Vendor installation, Chromium, and real Browser/Scheduler
Supervisor lifecycle without using model credentials.
