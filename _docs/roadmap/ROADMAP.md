# AgentForge Roadmap

Status date: 2026-08-02. Only unfinished/proposed work belongs here. Archived
plans are design history, not delivery commitments.

## Priority 0 — Continuous integrity

- Add CI for Python 3.11 dependency installation, tests, JSON validation,
  secret scanning, manifest/document path checks, and lock drift.
- Pin or deliberately vendor-lock OpenCode plugins and npm MCP packages that
  still use `latest` or unbounded `npx -y` resolution.
- Add a tracked, license-aware installer for approved external skill
  repositories and junction creation.
- Decide whether the fixed public SearXNG endpoint is acceptable or must become
  ignored local configuration.
- Add a safe cleanup utility that selects only cache files by explicit suffix,
  produces a dry-run inventory, validates every resolved target, and creates a
  recoverable manifest before deletion.

Exit: a clean clone is reproducible in CI and destructive maintenance cannot
silently broaden its target set.

## Priority 1 — Executable workflow contracts

- Bind Search orchestration to explicit provider callbacks or keep `/search`
  purely procedural and reduce duplicate orchestration claims.
- Turn `/deliver` into one reproducible executable coordinator, or continue to
  document its procedural/heuristic boundary consistently.
- Decide whether explicit Memory lesson calls from `/review` are sufficient or
  require a typed event interface. Automatic conversation capture remains out
  of scope unless separately designed and consented to.

Exit: every advertised workflow has one authoritative executable contract or a
clearly procedural contract.

## Priority 2 — Reliability and observability

- Add structured retry budgets and circuit breakers for external providers.
- Define shared event/result envelopes across Search, Prompt, Dispatch,
  Scheduler, Vision, and collection.
- Add lifecycle supervision, health polling, and graceful restart policy for
  Browser and Scheduler daemons.
- Define retention, backup, and restore policy for private runtime databases,
  reports, Memory, and recovery evidence.
- Replace broad regex authority heuristics with an evidence-based source policy.

## Priority 3 — Simplification

- Consolidate repeated MCP JSON-RPC plumbing while retaining standalone CLIs.
- Separate reusable static checks from project-specific TypeScript/Vite rules.
- Introduce typed Search stage/result contracts instead of loose dictionaries.
- Evaluate a smaller browser-use dependency profile if upstream supports one;
  the current `[core]` environment is reproducible but large.

## Deferred research

Multi-agent coding clusters, autonomous self-evolution/pattern extraction,
federated learning, and broad desktop automation remain exploratory. Original
proposals are under `archive/` and must not be interpreted as current features.
