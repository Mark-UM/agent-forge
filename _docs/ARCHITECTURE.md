# Agent Forge Architecture

Status: pre-2.0 Windows-first foundation complete; 2.0 kernel work is next.

This document describes current executable behavior. Archived version plans are
historical design inputs and are not implementation status.

## 1. Product shape

Agent Forge is a local Agent workspace rather than a hosted Agent platform. It
combines OpenCode interaction, Python capability services, MCP adapters, local
state, and a small number of supervised loopback processes.

```text
Interaction layer
  OpenCode, Slash Commands, MCP, standalone CLIs
                       |
Service layer
  Model Gateway, Search Service, Collection, Scheduler, Memory, Delivery
                       |
Capability layer
  Providers, Browser, Vision, Fetch, Indexing, static checks
                       |
Runtime/state layer
  SQLite, private Markdown, logs, reports, browser state, Vendor environment
```

The design goal is one production entry point and one authoritative state source
per capability, with lightweight compatibility adapters where old callers still
exist.

## 2. Startup and lifecycle

`start-opencode.bat` is the supported Windows launcher.

```text
Python 3.11 validation
        |
Vendor dependency/ABI validation
        |
ignored secrets/profile loading
        |
Prompt Context detection and composition
        |
Runtime Supervisor
   +----+------------------+
   |                       |
Browser daemon        Scheduler daemon
   |                       |
   +-----------+-----------+
               |
         OpenCode foreground
```

The Supervisor:

- generates or loads local bearer tokens without logging their values;
- detects port conflicts;
- adopts an already-running authenticated service without claiming ownership;
- starts missing services and waits for authenticated health;
- records non-secret PID/log state;
- applies bounded restart policy while running a foreground command;
- stops only services that it owns.

Browser binds to `127.0.0.1:9223`; Scheduler binds to
`127.0.0.1:9225`. Both require `Authorization: Bearer ...`.

## 3. Prompt and Context

The composition order is:

```text
Base -> Task -> Context -> Profile -> Example -> Explicit Extra
```

`modules.prompt.context` detects contexts from real project signals such as file
paths, extensions, imports, package metadata, and project markers. It writes a
versioned context-state file containing a project fingerprint.
`modules.prompt.composer` validates that fingerprint and produces
`AGENTS_COMPOSED.md` before OpenCode starts.

Task classification has two modes:

- deterministic heuristic, used as the zero-cost fallback;
- Model Gateway classification, used when explicitly selected and credentials
  are available.

## 4. Model Gateway

`modules.dispatch.gateway` is the only first-party owner of DeepSeek transport.
Business modules do not construct model endpoints.

Responsibilities:

- Pro/Flash routing by task type;
- explicit model alias validation;
- message normalization and size limits;
- known-secret and common-token redaction;
- bounded metadata telemetry that excludes message bodies;
- timeout and finite retry policy;
- structured success/failure response;
- optional local Run/Step/Event telemetry.

`modules.dispatch.compat` is a thin migration adapter that preserves legacy
`urllib.request.urlopen` test seams while still delegating policy and semantics
to the Gateway.

`modules.dispatch.no_bypass` scans first-party non-test Python code in CI. The
Gateway owns transport; the scanner is allow-listed only because it contains the
endpoint-detection expressions.

## 5. Search

All production callers use `modules.search.factory.build_search_service`.

```text
Search request
   -> validate and normalize
   -> plan sub-queries
   -> choose ready providers by mode
   -> execute with fallback
   -> normalize and deduplicate
   -> rank and aggregate
   -> optional verification
   -> format truthful result status
   -> SQLite cache store
```

Search modes influence provider policy:

- `quick`: stop after the first sufficient provider result;
- `standard`: fallback when the primary is unavailable, fails, or is
  insufficient;
- `deep`: combine multiple ready providers;
- `academic`: prefer arXiv and Semantic Scholar;
- local-only paths remain isolated from outbound providers.

Provider execution distinguishes:

```text
ready / unconfigured / unavailable / degraded
success / no_results / failed / skipped
```

A pipeline that runs without exceptions but produces no usable result is not a
successful Search response.

### Search state

The authoritative cache is SQLite:

- `search_cache` stores final and provider entries in separate namespaces;
- `search_cache_migrations` records idempotent legacy imports;
- `retrieved_at` is immutable freshness evidence;
- cache reads update only `last_accessed_at`;
- expired entries are pruned without extending their lifetime.

The old `pipeline_cache.json` and `search_cache.json` files are import-only. The
historical Search CLI implementation is retained privately for log/history
compatibility; its default cache path is patched to SQLite.

## 6. Browser and Collection

The Browser daemon owns a Playwright persistent context and project-local
storage state.

Security and reliability boundaries:

- HTTP API authentication on every route;
- only absolute HTTP(S) navigation;
- credentials in URLs rejected;
- DNS/IP validation blocks loopback, private, link-local, reserved, multicast,
  and cloud metadata targets;
- redirects and Playwright subresources are validated;
- service workers and WebSockets are disabled by default;
- arbitrary page JavaScript is disabled unless explicitly enabled;
- request bodies, selectors, scripts, screenshots, and response sizes are
  bounded.

Collection backend order is:

```text
explicit unguarded browser-use override (optional)
        -> authenticated Browser daemon
        -> secure static Fetch
```

Normal operation uses the Browser daemon. Collection extracts visible DOM text,
performs a bounded number of lazy-load scrolls, and adds screenshot/Vision
recognition only when requested or when DOM content is insufficient.

The optional `browser-use` backend accepts only its upstream Browser Use or
Anthropic clients. It is disabled by default and never receives a direct
DeepSeek transport.

## 7. Vision

Vision supports images, clipboard images, and bounded PDF rendering.

Limits include:

- PDF file bytes;
- maximum PDF pages;
- per-page pixels;
- total rendered pixels;
- encoded request bytes;
- images per API batch;
- model response size through the provider boundary.

For multiple API batches, `modules.vision.reducer`:

- sorts page ranges;
- preserves page-source comments;
- removes repeated positional headers and footers;
- keeps the first substantive duplicate paragraph and removes later copies;
- aggregates headings and tables;
- reports overlaps, missing pages, failed batches, and truncation as degraded
  output.

A document with usable recognized sections can be successful and degraded at
the same time. A single API batch keeps the historical plain-text output format.

## 8. Scheduler

Scheduler domain objects are distinct:

- `ScheduledJob`: trigger and task configuration;
- `JobRun`: one execution attempt;
- Action schedules: extracted user-facing actions.

SQLite is authoritative for jobs and runs. Supported triggers are:

- Cron;
- one-shot Date;
- fixed Interval.

Timezone precedence is request value, `AGENT_FORGE_USER_TZ`, local system zone,
then UTC. Persisted execution times are UTC.

An asynchronous manual trigger creates one queued `run_id`. The worker updates
that same record through running to a terminal state. Date jobs become completed
or error after their one execution.

## 9. Memory

Memory has two deliberate layers:

### Explicit durable APIs

- atomic lesson append;
- ADR-style decision append with counter reconciliation;
- structure-only health checks;
- read-only open-action reports.

### Candidate approval queue

Candidate content, source, and metadata are normalized and checked before the
SQLite schema is initialized. Pending candidates can be approved, rejected, or
expired. Only approval writes durable Markdown under private Memory.

The canonical CLI is `python -m modules.memory.cli`.

Automatic conversation capture remains out of scope.

## 10. Delivery profiles

`python -m modules.delivery` is the canonical static-check entry point.

Profiles:

```text
generic
python
typescript-vite
threejs
tower-stack
```

Generic checks always apply. Specialized checks require explicit project
evidence or `--profile`. The legacy Delivery checklist and UI Enforcer remain
available for compatibility but are not universal gates.

## 11. Registry and observability

Each first-party module has a canonical Manifest. Strict Registry validation
checks parse errors, directory/name consistency, entry points, capabilities,
storage paths, and duplicate declarations. Invalid manifests remain visible as
errors instead of disappearing from discovery.

Run/Step/Event provides a shared local envelope for Search, model calls, and
Scheduler adapters. State reduction distinguishes succeeded, degraded,
failed, timed out, cancelled, and abandoned runs.

This is operational telemetry, not a remote tracing platform.

## 12. Authoritative state map

| Domain | Authority |
|---|---|
| Search result/provider cache | `_runtime/mcp-sqlite.db` |
| Scheduler jobs and runs | `_runtime/mcp-sqlite.db` |
| Memory candidates | `_runtime/mcp-sqlite.db` |
| Durable private Memory | `_data/memory/*.md` |
| Prompt context state | `_runtime/prompt/` |
| Runtime service ownership | `_runtime/supervisor/state.json` |
| Browser cookies/storage | ignored Browser profile directory |
| Search history and derived quality metrics | `_runtime/search/` |
| Reports and review artifacts | `_runtime/reports/` |

## 13. Release validation

Windows is the current release platform. A release candidate must pass, for one
branch head:

1. Python compilation;
2. strict Registry validation;
3. Model Gateway no-bypass validation;
4. fast security/integration contracts;
5. the complete Windows pytest suite with unhandled thread exceptions treated
   as errors;
6. a clean Vendor install from `requirements.lock.txt`;
7. Chromium installation;
8. real authenticated Browser and Scheduler Supervisor start/status/stop.

JUnit XML, generated Markdown/JSON test reports, and Supervisor logs are retained
as GitHub Actions artifacts.

## 14. Pre-2.0 boundary

The completed foundation supports reliable single-Agent execution and reusable
capabilities. It does **not** implement the planned Multi-Agent Kernel yet.

2.0 kernel work is the next phase. It may add a small
Coordinator/Worker/Reviewer model with typed Task, Handoff, Artifact, Approval,
and checkpoint objects, but those components are not implemented in the current
repository. The kernel will reuse the existing Model Gateway, Search, Browser,
Scheduler, Memory, Run/Step/Event, and SQLite layers rather than replacing them
with a second platform.
