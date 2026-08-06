# Agent Forge Test Report

This tracked document records the validation contract and the finalized pre-2.0
foundation code baseline. The authoritative report for the current branch head is
rendered from pytest JUnit XML inside GitHub Actions and uploaded with the
workflow artifacts as Markdown, JSON, and XML.

A Git commit cannot contain its own final hash without becoming
self-referential. For that reason this file does not pretend to be the live
report for the commit that edits it.

## Finalized pre-2.0 foundation code baseline

| Field | Value |
|---|---|
| Foundation code commit | `b0d32fe114f1880646ece528ab1150e3070da6f5` |
| GitHub Actions run | `31066789367` |
| Platform | `windows-latest`, Python 3.11 |
| Tests | 2522 |
| Passed | 2519 |
| Failed assertions | 0 |
| Errors | 0 |
| Skipped | 3 |
| Pytest duration | 123.643 seconds |
| Fast Windows contracts | Passed |
| Strict Registry validation | Passed |
| Model Gateway no-bypass check | Passed |
| Gateway contracts | Passed |
| Capability contracts | Passed |
| Clean Vendor/Chromium/Supervisor smoke | Passed |

This is the finalized foundation code baseline. Later docs-only commits do not
change it: their authoritative validation comes from the GitHub Artifact
generated for their own exact head SHA. A Git commit cannot stably record its
own final SHA, so this tracked report does not self-reference the commit that
edits it.

## Current authoritative artifacts

The Windows CI workflow produces:

```text
_runtime/test-results/windows-pytest.xml
_runtime/test-results/TEST_REPORT.generated.md
_runtime/test-results/report.json
```

The reports contain:

- exact tested commit SHA;
- GitHub Actions run ID;
- platform and Python version;
- tests, passed, failed, errors, skipped, and duration;
- a machine-readable success flag.

They are uploaded under the `exact-windows-test-results` artifact. Supervisor
logs from the clean Windows runtime smoke are uploaded separately.

## Release gate

A pre-2.0 release candidate is valid only when all of the following pass for the
same branch head:

1. Python module compilation;
2. strict Capability Registry validation;
3. Model Gateway no-bypass validation;
4. Windows fast security/integration contracts;
5. complete Windows pytest with unhandled worker-thread exceptions elevated to
   errors;
6. clean Vendor install from `requirements.lock.txt`;
7. Chromium installation through the project bootstrap command;
8. authenticated Browser and Scheduler Supervisor start/status/stop.

Real model credentials, private user data, long-running desktop behavior, and
destructive recovery tests are intentionally excluded from shared CI and remain
local validation tasks.

## Reproduce the unit-test gate

```powershell
python -m pip install --upgrade pip pytest -r requirements.txt
python -m compileall -q modules
python -m modules.registry validate --strict
python -m modules.dispatch.no_bypass
python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning
```

## Reproduce the clean Windows runtime smoke

```powershell
.\scripts\windows-release-smoke.ps1
```

The script installs the ignored Vendor environment, validates its ABI, installs
Chromium, starts the authenticated local Browser and Scheduler services, checks
both through the Runtime Supervisor, and stops owned processes in `finally`.
