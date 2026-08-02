# AgentForge Roadmap

Status date: 2026-08-02. Only unfinished or proposed work belongs here; archived
plans have no delivery commitment.

## Priority 0 — Integrity and reproducibility

- Add CI for dependency installation, Python tests, JSON validation, secret
  scanning, and documentation/config count/path checks.
- Make subprocess output decoding explicit and replace wall-clock-sensitive
  Search fixtures so the current Windows test baseline is deterministic.
- Pin or deliberately lock plugin and npm MCP versions currently using `latest`
  or unbounded `npx -y`.
- Move Firefox executable/profile constants and Search health-check Python
  executable probes to ignored local configuration or environment variables,
  and remove the original repository path from test helpers.
- Add a tracked, license-aware installer for approved external skill
  repositories and junction creation.
- Decide whether the fixed public SearXNG instance is acceptable or must become
  local configuration.

Exit: a clean clone is reproducible without editing source constants and CI
detects secrets, broken docs, and dependency drift.

## Priority 1 — Close advertised workflow gaps

- Bind `/search` to real provider callbacks or explicitly retain a purely
  procedural architecture and remove redundant orchestrator claims.
- Implement browser-use LLM integration or remove its dead probe/requirement
  path from collection.
- Replace/remove Scheduler `memory_review` and `pattern_extract` placeholders;
  do not return success for unimplemented work.
- Decide and document whether memory append functions should have actual event
  registration; current behavior is explicit calls only.
- Turn `/deliver` into a reproducible executable coordinator or preserve its
  procedural boundary consistently.

Exit: each advertised workflow has one authoritative executable contract or one
clearly procedural contract.

## Priority 2 — Reliability, safety, and observability

- Add structured retry budgets/circuit breakers for external Search providers.
- Unify event/result schemas across Search, Prompt, Dispatch, Scheduler, Vision,
  and collection.
- Add lifecycle supervision and graceful shutdown for Browser/Scheduler daemons.
- Define retention/cleanup and backup/restore policy for sensitive runtime data.
- Replace broad regex authority heuristics (for example generic `.org`/`.io`
  promotion) with evidence-based source policy.

Exit: failures are bounded, observable, and recoverable without undocumented
manual process/state handling.

## Priority 3 — Simplification

- Consolidate repeated MCP JSON-RPC plumbing without losing standalone CLIs.
- Separate reusable static checks from project-specific TypeScript/Vite rules.
- Introduce typed Search stage/result contracts instead of loose dictionaries.
- Keep `vendor/` absent unless a reproducible, licensed offline build process is
  introduced.

## Deferred research

Multi-Agent coding clusters, autonomous self-evolution/pattern extraction,
federated learning, and broad desktop automation remain exploratory ideas.
Original proposals are under `archive/` and are not implementation commitments.
