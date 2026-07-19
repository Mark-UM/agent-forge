---
description: System health diagnostics — check OpenCode infrastructure status
agent: build
---

Run a health check on the current OpenCode setup. Check:
1. DeepSeek API connectivity (can we reach the API?)
2. Python availability (is Python 3.11+ accessible at the configured path?)
3. Vision module (does modules/vision/recognize.py exist?)
4. Browser module (does modules/browser/daemon.py exist?)
5. markconfig/ integrity (are secrets.json, profile.md, paths.json present?)
6. AGENTS.md presence
7. MCP servers loaded (count enabled MCPs in opencode.json)
8. Skills loaded (count skills directories in .opencode/skills/)

Report each check as PASS/FAIL with a brief note.
