# Agent Forge Roadmap

Status date: 2026-09-27 (foundation evidence remains dated 2026-08-06).

This file contains unfinished product work. Archived proposals are design
history, not delivery commitments.

## Release policy

Agent Forge uses a weekly **2.x release train**, not a destructive semantic
major every week:

```text
2.0 -> 2.1 -> 2.2 -> ... -> 2.6 -> 3.0
```

Each weekly release should deliver one user-visible capability slice, preserve
working entry points, and remove at least as much obsolete plumbing as it adds.
3.0 is the stable consolidation after the 2.x train.

## Foundation completion

Status: Completed on 2026-08-06.

Evidence:

- foundation code commit: `b0d32fe114f1880646ece528ab1150e3070da6f5`;
- final Windows release run: `31066789367`;
- 2522 tests, 2519 passed, 3 skipped, 0 failures, and 0 errors;
- clean Vendor/Chromium/Browser/Scheduler/Supervisor smoke passed;
- PR #1 merged;
- main push workflows passed.

The pre-2.0 foundation is closed. The 2.0 Kernel has since reached Slice 3;
the dated foundation evidence above remains historical. Live credentials and
private data remain local-only acceptance and are not shared CI inputs. Destructive
backup and restore drills remain later Roadmap work rather than a 2.0
prerequisite.

## 2.0 — Lightweight Multi-Agent Kernel

Status (2026-09-27): Slices 1-3 merged; F0 Fusion ADR proposed. F1-F7
integration and the complete 2.0 release remain unfinished.

2.0 design and acceptance documents:

- [`2.0_EXECUTION_PLAN.md`](2.0_EXECUTION_PLAN.md) — delivery slices,
  architecture principles, compatibility, security, observability, and
  Definition of Done;
- [`2.0_CONTRACTS_AND_STATE.md`](2.0_CONTRACTS_AND_STATE.md) — conceptual typed
  contracts, state machines, SQLite authority, idempotency, and failure
  semantics;
- [`2.0_FRAMEWORK_POC.md`](2.0_FRAMEWORK_POC.md) — equivalent OpenAI Agents SDK
  and PydanticAI PoC plan, retained as historical research;
- [`../adr/MYHARNESS_EXECUTOR_ADR.md`](../adr/MYHARNESS_EXECUTOR_ADR.md) —
  proposed Agent Forge Kernel / MyHarness Executor authority and F0-F7 gates;
- [`2.0_ACCEPTANCE_MATRIX.md`](2.0_ACCEPTANCE_MATRIX.md) — traceable test,
  failure-injection, Windows, and release evidence requirements.

The original planning documents predate the merged implementation. The Fusion
ADR selects a future Executor direction without enabling a runtime or adding
dependencies. Read the Slice ADRs and code for implemented behavior.

Goal: move from one coding Agent plus review procedures to a small, typed Agent
kernel without introducing a second platform.

Deliver:

- `Task`, `AgentSpec`, `Handoff`, `Artifact`, and `Approval` contracts;
- Coordinator, Worker, and combined Reviewer/Tester roles;
- Agent-as-tool and explicit Handoff execution;
- shared Model Gateway, Run/Step/Event, Search, Browser, Memory, and Scheduler;
- finite step, token, time, and retry budgets;
- one SQLite task/checkpoint authority;
- a simple single-file task completed end to end.

Executor strategy:

- Agent Forge keeps its self-owned Kernel and unique Task authority;
- first integration probe: pinned MyHarness in a disposable read-only Workspace;
- OpenAI Agents SDK and PydanticAI are not production dependencies of this path;
- source, license, protocol, Gateway, permission, and Windows gates precede
  production enablement;
- ordinary short tasks remain direct function/tool calls.

Non-goals:

- A2A federation;
- dashboards;
- distributed queues;
- autonomous modification of `main`;
- ten or more permanent Agent roles.

SiliconFlow Vision local acceptance remains a separate non-blocking Provider
issue. The first 2.0 text end-to-end Task must not depend on Vision.

## 2.1 — Durable execution

Goal: pause, resume, cancel, and recover multi-step work.

Deliver:

- checkpoint after every completed subtask;
- restart from the latest valid checkpoint;
- cancellation and timeout propagation;
- isolated Git worktree Workspace per coding task;
- bounded rollback to the pre-task tree;
- optional LangGraph adapter only for long-running durable workflows.

## 2.2 — Capability-based Agent aggregation

Goal: select and combine Agents by capability rather than hard-coded role
chains.

Deliver:

- capability query over the existing Registry;
- cost, latency, readiness, and permission-aware Agent selection;
- parallel execution only for dependency-independent tasks;
- typed Artifact merge instead of chat-text concatenation;
- Reviewer/Tester added dynamically when task risk warrants it;
- deterministic conflict and retry rules.

## 2.3 — Context, Memory, and Search convergence

Goal: one retrieval surface for working context and private knowledge.

Deliver:

- SQLite FTS5 as the default lightweight local index;
- optional `sqlite-vec` behind a feature flag;
- ChromaDB retained only as an optional compatibility/profile dependency;
- Session working memory distinct from approved long-term Memory;
- shared retrieval result contracts across Search, Context, and Memory;
- token-budget-aware context assembly;
- explicit source and freshness evidence.

## 2.4 — External Agent interoperability

Goal: connect independent Agents only when cross-process or cross-system
collaboration is actually needed.

Deliver:

- optional A2A Client/Server adapter;
- Agent Card generated from Capability Registry data;
- MCP capability mapping;
- external Agent allowlist and data-sharing policy;
- no exposure of private Memory or internal tool implementation by default.

Internal Coordinator/Worker communication continues to use typed function
Handoffs, not A2A.

## 2.5 — Evaluation and controlled improvement

Goal: improve measurable task outcomes without self-modifying production code.

Deliver:

- a small repository-specific benchmark of real maintenance tasks;
- a bounded SWE-bench subset through an optional sandbox executor;
- success, cost, duration, retry, and regression metrics;
- failure clustering and proposed improvement Candidates;
- automatic branch/PR proposals only after tests;
- explicit human approval before merge or durable policy changes.

OpenHands may be evaluated as an optional Docker/Workspace execution adapter;
it must not become a startup dependency.

## 2.6 — Operator experience and release speed

Goal: make the existing system easier to inspect and upgrade without a heavy web
platform.

Deliver:

- compact CLI/TUI Run tree using Rich;
- pause, resume, cancel, and approve commands;
- `uv`-based optional developer workflow and reproducible dependency profiles;
- automated release notes from merged PRs and capability changes;
- one-command backup and restore for private SQLite/Memory state;
- migration preview and rollback.

## 3.0 — Stable Agent Workspace

3.0 means the following pieces work together and are supportable:

```text
lightweight Agent Kernel
+ pluggable Multi-Agent roles
+ MCP tool ecosystem
+ recoverable Workspace execution
+ local-first Search and Memory
+ optional external Agent interoperability
+ measurable weekly release process
```

3.0 is not defined by the number of Agents, Skills, rules, documents, or
frameworks.

## Engineering constraints

### Keep

- private local operation;
- bounded file/network/process behavior;
- one authority per state domain;
- thin adapters around maintained open-source components;
- Windows-first release checks;
- explicit degraded behavior.

### Avoid

- enterprise RBAC and Kubernetes;
- duplicate model clients or caches;
- framework-specific rules applied to unrelated projects;
- remote tracing enabled by default;
- approval gates for ordinary read-only local work;
- hand-written test-count claims;
- multiple overlapping orchestration engines;
- dashboards before core task completion is reliable.

## Candidate open-source components

Evaluate components through short PoCs and retain only one solution per role:

| Need | Preferred direction |
|---|---|
| lightweight Agent/Handoff kernel | self-owned Agent Forge Kernel |
| coding Executor candidate | pinned MyHarness behind the Executor contract |
| durable long tasks | optional LangGraph adapter |
| provider compatibility | LiteLLM SDK inside Model Gateway, no Proxy by default |
| MCP plumbing | official MCP Python SDK |
| local text retrieval | SQLite FTS5 |
| optional vector retrieval | `sqlite-vec` feature flag |
| terminal inspection | Rich |
| isolated software execution | optional OpenHands adapter |
| Python environment/dev speed | `uv` and Ruff |

Every addition must replace real custom code or unlock a measurable capability;
star count alone is not an adoption criterion.
