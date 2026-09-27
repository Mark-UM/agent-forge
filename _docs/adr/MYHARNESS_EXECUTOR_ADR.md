# ADR: Agent Forge Kernel with a MyHarness Coding Executor

Status: proposed for F0 review (documentation decision; no Executor is enabled).

Date: 2026-09-27. Baseline: `main` at
`4880c4b7b0b6a560bc89a2858bb892f9c26ffd3f` (Slice 3 and P2 merged).

## Decision

Agent Forge owns the Task control plane. MyHarness is the first candidate Coding
Executor behind an Agent Forge-owned, versioned Executor contract. The systems
remain separate repositories and processes. MyHarness may run an Agent loop,
tools, local coding session, compaction, and local checkpoint mechanics, but it
cannot become a second Task, Handoff, Approval, Artifact, Budget, Model routing,
or audit authority.

The production direction is the self-owned Agent Forge Kernel plus a MyHarness
Executor Adapter. OpenAI Agents SDK and PydanticAI are not production dependencies
for this direction. Their earlier comparison remains historical research, not a
prerequisite for this integration. This ADR does not install MyHarness, change
the Kernel runtime, or declare the Executor ready for production.

## Context and evidence boundary

The merged Kernel has an `AgentRuntime.execute(AgentCommand,
CancellationToken) -> AgentResult` seam and a deterministic implementation
([agents.py](../../modules/kernel/agents.py)). `KernelExecutionEngine` injects
that runtime while retaining Task state transitions
([execution.py](../../modules/kernel/execution.py)). Slice 3 added persisted
Handoff, Artifact, Approval, and domain audit authority
([Slice 3 ADR](../roadmap/2.0_SLICE3_LIFECYCLE_ADR.md)). Its Artifact API still
accepts a trusted caller's `workspace_root`; Task-to-Workspace binding, real
write execution, a Reviewer loop, and full budget accounting are future work.

The [original 2.0 plan](../roadmap/2.0_EXECUTION_PLAN.md) predates these
merges. The [framework PoC plan](../roadmap/2.0_FRAMEWORK_POC.md) did not select
a framework. This ADR chooses a control-plane/Executor boundary; it does not
claim that the F1-F7 contracts or their failure tests already exist.

The proposed MyHarness content pin is
`5be723be1b5c34cae2abe6fea5718f0407f91760`. On this date, the plan's
original `h3327725338-star/MyHarness` remote returns repository-not-found.
The exact commit is accessible in
[`Mark-UM/MyHarness`](https://github.com/Mark-UM/MyHarness/commit/5be723be1b5c34cae2abe6fea5718f0407f91760),
which reports an Apache-2.0 license. Its pinned
[package manifest](https://github.com/Mark-UM/MyHarness/blob/5be723be1b5c34cae2abe6fea5718f0407f91760/package.json)
requires Node `>=22.19.0`; pinned [JSON mode](https://github.com/Mark-UM/MyHarness/blob/5be723be1b5c34cae2abe6fea5718f0407f91760/packages/coding-agent/docs/json.md)
and [SDK](https://github.com/Mark-UM/MyHarness/blob/5be723be1b5c34cae2abe6fea5718f0407f91760/packages/coding-agent/docs/sdk.md)
documents exist. That repository's availability does not prove integration safety.
F2 must verify fetchability, license/notices, exact Git object, executable
behavior, and source provenance before creating a lock or invoking it.

## Authority boundary

| Domain | Authority | Executor contribution |
|---|---|---|
| Task status, ownership, Handoff, terminal outcome | Agent Forge Kernel SQLite | Evidence only; local subagents remain Agent-as-tool |
| Budget and permission | Agent Forge Kernel | Consume only an explicitly bounded subset; request exact actions |
| Approval | Agent Forge Kernel | Execute only the matching consumed authorization |
| Artifact identity, digest, provenance, validation | Agent Forge Kernel | Produce candidate files and references |
| Model routing, credentials, retry policy, usage authority | `modules.dispatch.gateway` | Model consumer through an Agent Gateway Adapter |
| Cross-task Context and private Memory | Agent Forge canonical services | Receive a selected, bounded Context packet |
| Run/Step/Event and domain audit | Agent Forge | Emit bounded events and correlation references |
| Coding loop, local session, symbols, compaction | MyHarness | Executor-local mechanics with no Task authority |
| Git checkpoint mechanism | MyHarness, if enabled | Rollback aid; Kernel stores only an external reference |

A MyHarness session reporting success is never sufficient to mark a Kernel Task
successful. A mutating Task needs the Kernel's Artifact validation, Reviewer
decision, budget reconciliation, and legal terminal transition. Executor output
and checkpoint state cannot rewrite persisted Task truth after a crash.

## Executor and transport contract

F1 defines a framework-neutral `ExecutorRuntime`, `ExecutorCapabilities`,
`ExecutorRequest`, `ExecutorResult`, `ExecutorEvent`, and `ExecutorHealth`. The
contract carries a protocol version, request/Task IDs, required capabilities,
Task-bound Workspace reference, allowed tools/paths, finite budgets,
cancellation, and correlation IDs. Results distinguish success, degraded,
failure, cancellation, and timeout, with bounded usage and candidate evidence.
The Kernel rejects unknown protocol versions, missing capabilities, malformed
or oversized events, invalid result types, and budget/permission dimensions the
Adapter cannot enforce. A deterministic FakeExecutor comes first; existing
`DeterministicAgentRuntime` behavior stays compatible until migration is proven.

F2 may use `myharness --mode json` in a disposable read-only experiment. JSONL
is an observation format, not a permission boundary. F3 replaces that probe
with a persistent Node SDK bridge only after the F2 failure matrix passes. The
bridge must establish `hello` (protocol, exact commit, Node version, negotiated
features) before `execute`, `cancel`, or `shutdown`; unknown or inconsistent
features fail closed. No production request may silently fall back to a CLI,
another Provider, or broader tools.

Events normalize lifecycle, turns, tool requests/results, compaction, usage,
checkpoint, candidate Artifact, and final outcome. Correlation and sequence
allow auditing; bounded events exclude raw prompts, credentials, unrestricted
tool output, and private Memory. Event delivery failure must not become a
successful Task result. F1-F3 will define exact schemas, backpressure,
idempotency, and process cleanup tests rather than treating this prose as an
implemented wire format.

## Model and dependency policy

The integrated production path is MyHarness -> AgentForgeProvider -> Bridge ->
Agent Forge Gateway -> selected Provider. No independent production Provider
credential, direct model endpoint, retry policy, or remote trace is passed into
MyHarness. F4 must add an Agent-compatible Gateway protocol for assistant tool
calls and tool results; the current text-oriented Gateway is not proof that this
already works. F2 alone may use a fixed or fake Provider in its isolated test
fixture, and its result cannot satisfy production Gateway no-bypass acceptance.

Use a separate MyHarness checkout at one verified Git SHA; never vendor its
tree into Agent Forge or follow `main` automatically. A later versioned lock
records the chosen accessible repository, SHA, protocol version, runtime floor,
and reviewed license/notices. Since the original upstream is unavailable, F2
must explicitly choose and document the accessible source before any runtime
dependency is installed. MyHarness absence or SHA mismatch means capability
unavailable, not an alternate unreviewed implementation.

## Workspace, permission, budget, and Context

Agent Forge assigns and persists the Task -> Workspace binding. Before
execution, the Adapter verifies that the resolved root matches that assignment
and gives the Executor only its allowed paths. Slice 3 path and digest checks
remain necessary but do not authorize a caller-selected root or provide
handle-level protection against hostile concurrent filesystem replacement.
Before Artifact registration, Agent Forge resolves the final path, checks
containment and protected components, reads bytes, and recomputes SHA-256.
Changed bytes require a new Artifact identity and explicit supersession.

F2 grants only read/search/symbol capabilities in a disposable Workspace, with
no production credentials, private data, external mutation, or user repository
write access. F5 maps finite remaining Kernel limits to turn, wall-clock,
context, tool, child-agent, output, and retry limits; the effective limit may
only tighten. Missing enforcement for a required dimension blocks launch.
F5 also owns an exact-action Permission Broker. Project Trust and a startup
tool allowlist do not replace this authorization. F6 may add a single-file
write only after Task-bound Workspace, exact-action Approval consumption,
pre-mutation checkpoint, cancellation, and Artifact validation are proven.
Automatic commit, push, PR readiness, and merge remain disabled.

The Bridge receives an allowlisted environment rather than the full parent
environment. Integrated mode disables unreviewed project extensions, project
Provider/auth configuration, automatic installs, and tools outside the
negotiated allowlist. Network-capable tools retain Agent Forge's existing
Gateway, Search, Browser, Fetch, and MCP boundaries; the Bridge may not become
an alternate unchecked network client. Project Trust alone is not a sandbox.

The Context packet contains selected project rules, relevant decisions,
Artifact references, and authorized Memory extracts under a finite size
budget. The Executor may compact its local coding session but never receives
the complete private Memory store or owns cross-task Context.

## Session, cancellation, and recovery

One Executor invocation is correlated with a Kernel Task attempt; a MyHarness
session is an internal execution detail. Cancellation propagates to the Node
bridge, active session/subagents/tools, and owned process tree. The Kernel does
not mark cancellation complete while a write-capable child can still mutate.
On bridge crash or uncertain external side effect, mark the attempt interrupted
or failed under Kernel transitions and reconcile explicitly. Never infer Task
success from a recovered session or blindly retry a non-idempotent tool. These
are acceptance requirements, not claims of current 2.0 durability.

## Upgrade, rollback, and stop gates

For each proposed MyHarness upgrade: select a new immutable SHA; inspect
license/notices, SDK/JSON/events/tools, extensions, Provider/auth, Git/network
behavior; run bridge contracts and the Windows failure matrix; then run the
single-file E2E and exact-head release gate. Update the lock only after all
checks pass. A failed upgrade keeps the prior verified SHA. If the pinned
source disappears, preserve the locked object or disable the capability;
never resolve a floating branch as a substitute.

Rollback disables the Executor Adapter and returns to the deterministic or
other existing Agent Forge path without rewriting Kernel Task records. Preserve
audit and Artifact history. Do not automatically delete Workspaces or replay
uncertain side effects. Halt the integration if the Executor requires its own
production API key, cannot enforce the required limits/permissions, cannot
cancel owned processes, or treats local session state as Kernel authority.

## Delivery gates

| Phase | Required evidence before proceeding |
|---|---|
| F0 | Docs agree on authority, dependency, source pin, and unresolved gates; no runtime change |
| F1 | Typed contract and FakeExecutor with compatibility and failure tests |
| F2 | Exact-source read-only JSON PoC; malformed output, crash, timeout, cancellation, overflow, and no-write/no-secret evidence |
| F3 | SDK bridge handshake, event/permission surface, bounded process cleanup on Windows |
| F4 | Agent Gateway protocol and no-bypass proof with tool-call conversations |
| F5 | Finite budget, exact permission, bounded Context, and cancellation mapping |
| F6 | One Task-bound Workspace file mutation, checkpoint, Artifact and Approval integration |
| F7 | Success and rejected-review E2E plus exact-head Windows release gate |

F0 is intentionally documentation-only. It establishes the design boundary
and stop conditions; each later phase still needs its own reviewed PR and
evidence before production capability is enabled.
