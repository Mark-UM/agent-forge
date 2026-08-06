# Agent Forge Developer Guide

Agent Forge is maintained through branches and Draft Pull Requests. Windows with
Python 3.11 is the current release platform.

Read `_docs/AI_MAINTAINER_PLAYBOOK.md` before repository-level maintenance. It
contains verified lessons and invariants; it must never contain secrets,
personal data, raw prompts, or temporary credentials.

## 1. Development setup

```powershell
python --version
python -m modules.bootstrap.dependencies install
python -m modules.bootstrap.dependencies check --json
python -m modules.bootstrap.dependencies install-browser chromium
python -m pip install --upgrade pip pytest -r requirements.txt
```

Local credentials and preferences belong only in ignored `markconfig` files.

## 2. Validation order

Run the cheapest checks first:

```powershell
python -m compileall -q modules
python -m modules.registry validate --strict
python -m modules.dispatch.no_bypass
python -m pytest -q <changed-module-tests>
```

Before considering a branch ready:

```powershell
python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning
.\scripts\windows-release-smoke.ps1
```

The complete suite treats leaked background-thread failures as errors. The
runtime smoke installs the Vendor environment, installs Chromium, starts the
real authenticated Browser and Scheduler services, verifies Supervisor status,
and stops owned processes.

## 3. GitHub workflow

1. Keep changes off `main`.
2. Use one coherent branch for one release slice.
3. Keep the PR Draft while checks are failing or integrations are incomplete.
4. Inspect the exact latest Windows Actions head; a previous green commit is not
   evidence for the current one.
5. Use the JUnit artifact when the console log is too large.
6. Update docs before the final validation commit.
7. After all required checks pass, update the PR description without changing
   the branch head.

The authoritative release workflow is `.github/workflows/windows-release-gate.yml`.
It publishes exact Markdown, JSON, and XML reports containing the tested commit
and Actions run ID.

## 4. Production entry points

| Domain | Canonical entry |
|---|---|
| Model requests | `modules.dispatch.gateway` |
| Search | `modules.search.factory.build_search_service` |
| Browser | `modules.browser.daemon` through Runtime Supervisor |
| Scheduler | `modules.scheduler.secure_daemon` through Runtime Supervisor |
| Collection | `modules.orchestrator.agent_wrapper` |
| Vision | `modules.vision.recognize` |
| Memory | `python -m modules.memory.cli` |
| Delivery/UI checks | `python -m modules.delivery` |
| Capability validation | `python -m modules.registry validate --strict` |

New production code must not create an alternative endpoint, cache, Scheduler
state source, or daemon lifecycle path.

## 5. State authorities

| Domain | Authority |
|---|---|
| Search result/provider cache | SQLite `search_cache` |
| Search cache migration ledger | SQLite `search_cache_migrations` |
| Scheduler jobs and runs | SQLite scheduler tables |
| Memory candidates | SQLite `memory_candidates` |
| Durable private Memory | `_data/memory/*.md` |
| Runtime process ownership | `_runtime/supervisor/state.json` |
| Prompt context/composition | `_runtime/prompt/` |

Legacy Search cache JSON is import-only. A test may explicitly inject a temporary
JSON path to exercise compatibility, but default production behavior must not
write it.

## 6. Model Gateway rules

- Business modules use Gateway or the thin `dispatch.compat` migration adapter.
- Only Gateway owns the DeepSeek endpoint.
- Never place message bodies or raw model responses in Run metadata.
- Structured request options belong in `GatewayRequest.extra_body`; reserved
  model/messages/stream keys cannot be overridden.
- Preserve meaningful timeout/HTTP diagnostics through compatibility adapters.
- Run `python -m modules.dispatch.no_bypass` after touching model clients.

## 7. Async and fixture rules

A test that starts a named background worker must wait for that worker before
its temporary database or directory is torn down. Establish a fixture dependency
so waiting happens before resource cleanup; a session-wide sleep is not a fix.

Production asynchronous APIs must return an authoritative identifier before
starting work and update that same record through terminal state.

## 8. Windows-specific rules

- Keep Python 3.11 ABI checks.
- Declare `tzdata` directly for IANA timezones.
- Use atomic `os.replace` with temporary-file cleanup.
- Treat failed cache/report writes as non-fatal only when the data is derived and
  recoverable; authoritative state failures must surface.
- Verify subprocess groups and cleanup through the Runtime Supervisor.
- Browser executables are installed under ignored runtime storage.

## 9. Adding a module

1. Add a canonical `manifest.json`.
2. Match the Manifest name to the directory name.
3. Declare only real entry points and storage.
4. Add the module to the current Registry inventory test.
5. Run strict validation.
6. Add capability tests before advertising the module in README.

Do not create a module merely to hold one small helper. Prefer an existing
domain package unless the new component has a distinct production lifecycle or
state contract.

## 10. Dependency policy

Prefer maintained open-source components through thin adapters when they remove
meaningful custom code or unlock a measured capability.

Core startup should remain small. Heavy or experimental dependencies belong in
optional profiles. Do not add two primary frameworks for the same role.

Evaluation criteria:

- Windows support;
- license compatibility;
- recent maintenance;
- startup and memory cost;
- replaceability;
- real reduction of first-party complexity;
- testability without real credentials.

## 11. Reporting

Never use a hand-written test count as release evidence.

Generate reports from JUnit:

```powershell
python scripts/render_windows_test_report.py `
  --junit _runtime/test-results/windows-pytest.xml `
  --commit <sha> `
  --run-id <actions-run-id> `
  --markdown _runtime/test-results/TEST_REPORT.generated.md `
  --json _runtime/test-results/report.json
```

The tracked `_docs/TEST_REPORT.generated.md` explains the reporting contract and
last finalized source baseline. Current-head evidence is always the workflow
artifact.
