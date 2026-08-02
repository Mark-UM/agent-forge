---
description: Check OpenCode configuration, dependencies, and local state
agent: build
---

Run a health check on the current OpenCode setup. Keep configuration state,
package availability, credentials, and network reachability as separate facts.

1. Confirm the selected interpreter is Python 3.11 (the supported ABI).
2. Run `python -m modules.bootstrap.dependencies check --json`. This performs
   real imports, so metadata-only or ABI-broken vendor packages are failures.
3. Confirm `modules/vision/recognize.py` and `modules/browser/daemon.py` exist;
   file presence alone does not prove their optional dependencies/API access.
4. Confirm ignored local `markconfig/secrets.json` and `markconfig/profile.md`
   exist, without printing their contents. There is no active `paths.json`.
5. Confirm `AGENTS.md` exists and report whether generated
   `AGENTS_COMPOSED.md` is currently present.
6. Parse `opencode.json`; report total/enabled/disabled MCP definitions. Probe
   connectivity only when the corresponding process/key/network is available.
7. Count the six repository-owned skills separately from ignored external
   repositories and junctions.
8. Run `python -m modules.memory.hook health`.
   The nine private memory files are optional local state, so missing files are
   reported as missing rather than evidence that tracked repository files are
   incomplete.

Report each check as PASS, FAIL, UNAVAILABLE, or NOT CONFIGURED with a brief
evidence note. Never expose secret values.
