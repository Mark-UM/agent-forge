# Agent Forge AI Maintainer Playbook

This file is the durable, repository-scoped operating memory for AI maintainers.
Read it only when maintaining Agent Forge itself. Update it only with reusable,
verified lessons. Never record secrets, personal information, temporary access
URLs, raw prompts, or unverified guesses.

## Current maintenance context

- Repository: `Mark-UM/agent-forge`
- Active hardening branch: `agent/full-system-hardening`
- Active review: Draft PR `#1`
- Primary supported environment for the current release train: Windows with
  Python 3.11
- `main` remains protected from direct maintenance changes; all work stays on a
  branch until the latest Windows checks pass.

This section may be updated when the active release branch changes. Historical
lessons below should remain stable.

## Product priorities

1. Functional behavior and a smooth local workflow come first.
2. Reuse lightweight, maintained open-source components through thin adapters.
3. Keep one production entry point and one authoritative state source per
   capability.
4. Preserve only the essential safety boundaries:
   - no secrets or private content in Git, logs, telemetry, or outbound prompts;
   - writes remain inside the intended workspace and destructive actions are
     bounded and recoverable;
   - untrusted network targets cannot reach loopback/private/metadata services;
   - processes, requests, file sizes, retries, and execution time are bounded.
5. Avoid enterprise ceremony, duplicate abstractions, speculative dashboards,
   and rule-count or test-count vanity metrics.

## Reliable GitHub maintenance workflow

1. Read the current PR head and changed-file list before editing.
2. Inspect the latest failing Windows job logs and identify the exact failing
   assertion or runtime boundary.
3. Change the smallest coherent production seam; update tests when the test is
   stale or asserts the wrong contract.
4. Commit only to the active hardening branch.
5. Let GitHub Actions validate the exact new head. A previously green commit is
   not evidence for the current head.
6. Keep the PR in Draft until all required Windows jobs pass.
7. After a green release candidate, synchronize README, architecture, roadmap,
   and the generated test report with the tested commit SHA.

## Windows verification order

Run the cheapest checks first:

```powershell
python -m compileall -q modules
python -m modules.registry validate --strict
python -m pytest -q <changed-module-tests>
python -m pytest -q
```

For a clean-machine release check:

```powershell
python --version
python -m modules.bootstrap.dependencies install
python -m modules.bootstrap.dependencies check --json
python -m modules.bootstrap.dependencies install-browser chromium
.\start-opencode.bat
```

The clean-machine install, real browser lifecycle, real model credentials,
long-running daemon behavior, and destructive recovery checks require a local
Windows environment. Ordinary source changes and deterministic tests should be
completed through the GitHub branch and hosted Windows runner first.

## Architecture invariants

- Model calls: business modules use `modules.dispatch.gateway`; model endpoint,
  retry, routing, redaction, and telemetry logic must not be reimplemented.
- Search: MCP, CLI, and library entry points use the production Search factory.
  SQLite is the authoritative cache; legacy JSON files are import-only.
- Scheduler: SQLite is authoritative for jobs and runs. A manual asynchronous
  trigger creates exactly one `run_id`, and the worker updates that same row.
- Browser and Scheduler: loopback services require bearer authentication and
  are managed through the Runtime Supervisor in normal operation.
- Vision: batched PDF output is reduced with page provenance; partial usable
  output is successful-but-degraded rather than empty or falsely complete.
- Memory: candidates are checked before persistence and require explicit
  approval before durable memory is written.
- Registry: strict discovery must report malformed or missing manifests instead
  of silently omitting them.
- Delivery checks: generic rules are universal; framework/project rules are
  activated only by real project evidence.

## Validated failure patterns and fixes

### Position-aware Vision edge removal

Do not combine first-line and last-line counters. A paragraph at the end of one
batch and the start of the next is a boundary duplicate, not both a repeated
header and footer. One-line batch output is substantive content and must never
be classified as page chrome merely because it is both the first and last line.
Keep the first substantive paragraph and remove only later duplicates.

### Secret detection before storage creation

Validate content, provenance, and metadata before opening SQLite. This ensures a
rejected candidate leaves no database artefact. Accept common human spellings
such as `API key`, `api_key`, and `api-key`; cover known environment values and
specific token/private-key formats without turning the project into a large DLP
system.

### Telemetry tests must distinguish labels from payloads

A safe routing label such as `task_type=implementation` may share a word stem
with user content. Tests should assert that the exact message body and known
secret sentinels are absent, not ban ordinary substrings that are valid metadata.

### Inventory tests must evolve with real modules

When a first-party module is intentionally added, update the canonical expected
inventory. Do not hide a valid module from discovery to satisfy a stale count.
Prefer checking required names and strict manifest validity over relying only on
an unexplained number.

### Async tests must wait for owned workers

A test that starts a background scheduler thread must wait for its returned
`run_id` to reach a terminal state before a temporary database fixture is torn
down. Otherwise later tests receive misleading thread exceptions against a
removed database.

## Week 0 completion checklist

- [ ] Latest Windows fast contracts pass.
- [ ] Latest Windows full suite passes with no unhandled thread exceptions.
- [ ] All direct DeepSeek business callers use Model Gateway.
- [ ] Static no-bypass check prevents endpoint reintroduction.
- [ ] Production Search uses SQLite Cache Repository and no longer writes legacy
      pipeline/search JSON caches.
- [ ] Vision Reducer is wired into PDF recognition.
- [ ] Memory candidate operations are exposed through the main Memory CLI.
- [ ] Profile-aware Delivery checker is the canonical delivery/UI entry point.
- [ ] Windows clean-install and real-service smoke-test instructions are ready
      for the local Codex pass.
- [ ] README, Architecture, Roadmap, PR description, and test report match the
      tested commit.

## Session log

### 2026-08-05 — Week 0 completion work started

- Confirmed the active Draft PR and inspected the exact latest Actions failures.
- Corrected Vision batch reduction so substantive single-line and boundary text
  is preserved.
- Expanded pre-persistence Memory secret checks and included source/metadata in
  the checked envelope.
- Corrected the Gateway telemetry test to distinguish safe routing metadata from
  the original message body.
- Updated the Registry inventory for the Runtime module.

Append only outcomes that were actually committed and verified. Replace pending
claims with exact Windows workflow results when available.
