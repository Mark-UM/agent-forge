---
description: Start the local Playwright/Firefox Browser daemon
agent: build
---

# /browser — Start Browser Daemon

Start the daemon as a background process from the repository root:

```powershell
Start-Process python -ArgumentList '-m','modules.browser.daemon' -WindowStyle Hidden
```

Then call `http://127.0.0.1:9223/ping`. If it responds, report the current
status. If the port is already serving the daemon, do not start another copy.

The custom Browser tools only call the HTTP API; they never auto-start this
process. The daemon uses the Firefox executable/profile constants currently
defined in `modules/browser/daemon.py` and requires Playwright.
