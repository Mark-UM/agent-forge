---
description: System health diagnostics — check OpenCode infrastructure status
agent: build
---

Run a health check on the current OpenCode setup. Keep configuration state,
package availability, credentials, and network reachability as separate facts.

1. Confirm `python` on `PATH` is Python 3.11 or newer.
2. Confirm `modules/vision/recognize.py` and `modules/browser/daemon.py` exist;
   file presence alone does not prove their optional dependencies/API access.
3. Confirm ignored local `markconfig/secrets.json` and `markconfig/profile.md`
   exist, without printing their contents. There is no active `paths.json`.
4. Confirm `AGENTS.md` exists and report whether generated
   `AGENTS_COMPOSED.md` is currently present.
5. Parse `opencode.json`; report total/enabled/disabled MCP definitions. Probe
   connectivity only when the corresponding process/key/network is available.
6. Count the six repository-owned skills separately from ignored external
   repositories and junctions.
7. Run `python -c "from modules.memory.hook import check_memory_health; import json; print(json.dumps(check_memory_health(), indent=2))"`.
   The nine private memory files are optional local state, so missing files are
   reported as missing rather than evidence that tracked repository files are
   incomplete.

Report each check as PASS, FAIL, UNAVAILABLE, or NOT CONFIGURED with a brief
evidence note. Never expose secret values.
