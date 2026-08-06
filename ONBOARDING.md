# Agent Forge Onboarding

Use this file as the maintainer entry point. Establish current behavior from
code, configuration, and current manifests before consulting plans. When prose
conflicts with executable behavior, code and configuration are authoritative and
the maintained prose must be corrected.

## Current status

- The pre-2.0 Windows-first foundation is complete.
- 2.0 Lightweight Multi-Agent Kernel planning is active.
- 2.0 implementation has not started.
- The working single-Agent, CLI, MCP, and OpenCode paths remain the current
  product behavior.

See `_docs/roadmap/ROADMAP.md` and its linked 2.0 planning documents. Planning
content describes proposed contracts and gates, not implemented capability.

## Reading order

1. `AGENTS.md` and `AGENTS_BASE.md`;
2. generated `AGENTS_COMPOSED.md`, when the launcher has created it;
3. ignored `markconfig/profile.md`, when present;
4. `README.md` and `_docs/ARCHITECTURE.md`;
5. `_docs/DEVELOPER.md` and `_docs/USER_GUIDE.md`;
6. the active Roadmap and task-specific Prompt context.

`_docs/history/` and `_docs/roadmap/archive/` are design history, not current
specifications.

## Maintainer invariants

- Never commit real secrets, profiles, private Memory, runtime state, databases,
  logs, external skill junctions/clones, caches, or vendored dependencies.
- Runtime writes stay under `_runtime/`; maintained public documentation stays
  under `_docs/` unless an existing top-level document is canonical.
- Model requests use `modules.dispatch.gateway`; first-party business modules do
  not own model endpoints.
- Search callers use `modules.search.factory.build_search_service`.
- Browser and Scheduler lifecycle is managed by
  `modules.runtime.supervisor` during normal operation.
- One state domain has one authority. Compatibility and migration paths must not
  become permanent dual-write paths.
- Optional capability failure is reported as unavailable or degraded, never as
  fabricated success.
- New dependencies must be justified, locked, and validated on Windows with
  Python 3.11 before production adoption.

## Component map

| Path | Current responsibility and canonical entry |
|---|---|
| `start-opencode.bat` | Python/Vendor gate, ignored local configuration, Prompt composition, Runtime Supervisor, OpenCode launch |
| `opencode.json` | OpenCode model, plugin, MCP, and project permission configuration |
| `.opencode/prompts/` | tracked Prompt layers used by `modules.prompt.composer` |
| `.opencode/commands/`, `.opencode/agents/`, `.opencode/skills/`, `.opencode/tools/` | OpenCode procedures, read-only review roles, skills, and tool wrappers |
| `modules.dispatch/` | Model Gateway, compatibility adapter, and no-bypass validation |
| `modules.common/` | shared security helpers and Run/Step/Event telemetry contracts |
| `modules.search/` | Search **v4.6.0**; canonical production factory, Provider fallback, and SQLite cache contracts |
| `modules.runtime/` | unified Browser/Scheduler start, status, bounded restart, stop, adoption, and cleanup |
| `modules.browser/`, `modules.vision/` | authenticated Browser daemon and bounded image/PDF recognition |
| `modules.mcp/` | secure Fetch, SQLite, and Time MCP servers |
| `modules.scheduler/`, `modules.memory/` | authenticated scheduling and approval-gated private Memory |
| `modules.registry/` | strict Manifest and capability validation |
| `modules.orchestrator/` | current collection, action extraction, indexing, and scheduling adapters; not the planned 2.0 Kernel |
| `modules.delivery/`, `modules.integration_check/`, `modules.ui_check/` | profile-aware and compatibility static checks |
| `modules.bootstrap/` | dependency, ABI, Browser installation, and scaffold support |

## Current flows

- **Startup:** `start-opencode.bat` validates Python 3.11 and Vendor state,
  loads ignored local configuration without printing secrets, composes
  `AGENTS_COMPOSED.md`, and runs OpenCode through the Runtime Supervisor.
- **Search:** MCP, CLI, and library callers share
  `modules.search.factory.build_search_service`; readiness and fallback are
  explicit, and empty usable output is not reported as success.
- **Browser:** the authenticated Browser daemon is started and observed through
  the Runtime Supervisor. Tools do not own an independent lifecycle.
- **Collection:** normal order is authenticated Browser daemon, then secure
  static Fetch. `browser-use` is disabled by default and is available only as an
  explicit reduced-security override.
- **Delivery:** profile-aware static checks and read-only review Agents are
  separate mechanisms coordinated by maintained procedures.

## Known gaps and active planning

- The 2.0 typed Task/Handoff/Artifact/Approval Kernel is planned but not
  implemented.
- Framework selection remains open until equivalent OpenAI Agents SDK and
  PydanticAI PoCs are measured through the existing Model Gateway boundary.
- Reproducible pinning for external plugins, MCP packages, and optional skills
  remains incomplete.
- SiliconFlow `Qwen/Qwen3-VL-Plus` local acceptance currently fails with a
  `RuntimeError`; the cause is unverified and the issue does not block the first
  text-only 2.0 loop.
- GitHub `main` technical protection is a governance concern until protection
  and required checks are verified as enabled.
