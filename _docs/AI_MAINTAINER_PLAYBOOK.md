# Agent Forge AI Maintainer Playbook

This file is the durable, repository-scoped operating memory for AI maintainers.
Read it only when maintaining Agent Forge itself. Update it only with reusable,
verified lessons. Never record secrets, personal information, temporary access
URLs, raw prompts, or unverified guesses.

## Current maintenance context

- Repository: `Mark-UM/agent-forge`
- Stable foundation branch: `main`
- Finalized foundation code baseline:
  `b0d32fe114f1880646ece528ab1150e3070da6f5`
- Week 0 PR: `#1`, merged by Squash Merge on 2026-08-06
- There is no active hardening branch. New work starts from current `main` on a
  new `agent/*` branch.
- Current development target: 2.0 Lightweight Multi-Agent Kernel.
- Primary supported release environment: Windows with Python 3.11.
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

1. Start every task from the latest `main` on a new `agent/*` branch; do not
   develop directly on `main`.
2. Read the current PR head and changed-file list before editing. When a check
   fails, inspect its exact Windows job logs and identify the failing assertion
   or runtime boundary.
3. Change the smallest coherent production seam; update tests when the test is
   stale or asserts the wrong contract.
4. Let GitHub Actions validate the exact new head. A previously green commit is
   not evidence for the current head.
5. Keep the PR in Draft until all required Windows jobs pass.
6. After merge, verify the `main` push workflows for the new merge commit.
7. Documentation reports record a finalized code baseline; current authoritative
   values come from the CI Artifact for the exact head under validation.

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

### Validate Windows release scripts with Windows PowerShell 5.1

In Windows PowerShell, a variable followed immediately by a colon must use
`$()`, `${}`, or the format operator so the Parser cannot treat the colon as
part of the variable reference. Validate Windows release scripts with the
`powershell.exe` 5.1 Parser, not only `pwsh`. Claim the Windows Release Smoke is
green only after the complete locked Vendor install, Chromium install, and
authenticated Supervisor service lifecycle has actually passed, including
process cleanup.

### Rebuild Windows Vendor only after owned services stop

Windows cannot replace a loaded `.pyd` file. Before rebuilding
`vendor/python-libs`, stop the Browser and Scheduler services owned by the
Runtime Supervisor. Only terminate PIDs explicitly marked as managed in
`state.json` whose command lines match Agent Forge services; never automatically
kill unknown or adopted processes. Confirm loopback ports are released before
removing Vendor. Both the Windows PowerShell 5.1 Parser and the complete Smoke
must pass after the rebuild.

## Week 0 completion checklist

- [x] Windows fast contracts pass.
- [x] Windows full suite passes with no unhandled thread exceptions.
- [x] Model Gateway no-bypass is enforced.
- [x] Production Search uses the SQLite Cache Repository and no longer writes
      legacy pipeline/search JSON caches.
- [x] Vision Reducer is wired into PDF recognition.
- [x] Memory candidate operations are exposed through the main Memory CLI.
- [x] Profile-aware Delivery checker is the canonical delivery/UI entry point.
- [x] Clean Windows Vendor install and Chromium install pass.
- [x] Browser/Scheduler Supervisor lifecycle passes.
- [x] README, Architecture, Roadmap, PR description, and test report align with
      the finalized foundation.

Evidence:

- Foundation code commit: `b0d32fe114f1880646ece528ab1150e3070da6f5`
- Windows release run: `31066789367`
- Tests: 2522 total, 2519 passed, 3 skipped, 0 failures, 0 errors
- PR: #1 merged
- Main push workflows: Gateway contracts, Capability contracts, Windows release
  gate, and CI — success

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

### 2026-08-06 — Week 0 foundation completed

- PR #1 was Squash Merged; the main foundation code baseline is
  `b0d32fe114f1880646ece528ab1150e3070da6f5`.
- Windows Release Gate run `31066789367` reported 2522 tests, 2519 passed, 3
  skipped, 0 failures, and 0 errors.
- Local Windows Smoke completed its Vendor, Chromium, Browser, Scheduler, and
  Supervisor lifecycle, including cleanup.
- The old hardening branch was deleted. The next phase is the 2.0 Lightweight
  Multi-Agent Kernel.
- Real Provider credentials, private data, and visible-browser acceptance remain
  local-only. Destructive backup and restore drills are later Roadmap work, not
  a 2.0 prerequisite.
