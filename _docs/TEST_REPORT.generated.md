# Agent Forge Test Report

This tracked document records the validation contract and the last finalized
source baseline. The authoritative report for the current branch head is
rendered from pytest JUnit XML inside GitHub Actions and uploaded with the
workflow artifacts as Markdown, JSON, and XML.

A Git commit cannot contain its own final hash without becoming
self-referential. For that reason this file does not pretend to be the live
report for the commit that edits it.

## Last finalized source baseline

| Field | Value |
|---|---|
| Source commit | `c0bd6ba1e2fac66acea226f39644df4d97e8698a` |
| GitHub Actions run | `30989584832` |
| Platform | `windows-latest`, Python 3.11 |
| Tests | 2521 |
| Passed | 2518 |
| Failed assertions | 0 |
| Errors | 0 |
| Skipped | 3 |
| Pytest duration | 34.747 seconds |
| Fast Windows contracts | Passed |
| Strict Registry validation | Passed |
| Model Gateway no-bypass check | Passed |
| Gateway contracts | Passed |
| Capability contracts | Passed |

This baseline validated the complete source integration before the final
release-document and clean-runtime-smoke additions.

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

They are uploaded under the `windows-pytest-results` artifact. Supervisor logs
from the clean Windows runtime smoke are uploaded separately.

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
