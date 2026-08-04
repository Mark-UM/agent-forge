---
description: Check OpenCode configuration, dependencies, and local state
agent: build
---

Run a health check on the current OpenCode setup. Keep configuration state,
package availability, credentials, and network reachability as separate facts.

## Phase 1: Environment

1. Confirm the selected interpreter is Python 3.11 (the supported ABI).
2. Run `python -m modules.bootstrap.dependencies check --json`. This performs
   real imports, so metadata-only or ABI-broken vendor packages are failures.

## Phase 2: Capability Registry (R2-B.4)

3. Run `python -m modules.registry validate`. All modules must have valid
   manifests. Report any missing or invalid manifests.
4. Run `python -m modules.registry health --json`. This executes each module's
   declared health checks and credential checks. Report:
   - Module name, overall status (healthy/degraded/unhealthy/unknown)
   - Credential availability (env vars present/missing, never values)
   - Failed health check commands and their stderr
5. Run `python -m modules.registry list --json` to get the full module inventory
   with capabilities, entrypoints, and storage declarations.

## Phase 3: Configuration

6. Confirm ignored local `markconfig/secrets.json` and `markconfig/profile.md`
   exist, without printing their contents. There is no active `paths.json`.
7. Confirm `AGENTS.md` exists and report whether generated
   `AGENTS_COMPOSED.md` is currently present.
8. Parse `opencode.json`; report total/enabled/disabled MCP definitions. Probe
   connectivity only when the corresponding process/key/network is available.
9. Count the repository-owned skills separately from ignored external
   repositories and junctions.

## Phase 4: Memory & Storage

10. Run `python -m modules.memory.hook health`.
    The nine private memory files are optional local state, so missing files are
    reported as missing rather than evidence that tracked repository files are
    incomplete.
11. For each module with `storage` declared in its manifest, verify the storage
    path exists (or report as "not yet initialized" for first-run scenarios).

## Output Format

Report each check as PASS, FAIL, UNAVAILABLE, or NOT CONFIGURED with a brief
evidence note. Never expose secret values.

For machine-readable output, use:
```bash
python -m modules.registry health --json > _runtime/doctor/health-report.json
```

## Recovery

If any module is UNHEALTHY:
1. Check its health_check stderr in the JSON output
2. Verify credentials are set (env vars)
3. Run `python -m modules.registry validate` to confirm manifest integrity
4. If storage is missing, initialize it per the module's documentation

