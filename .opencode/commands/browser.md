---
description: Start the local Playwright Browser daemon
agent: build
---

# /browser — Start Browser Daemon

Start the daemon in the repository root:

```powershell
Start-Process python -ArgumentList '-m','modules.browser.daemon' -WindowStyle Hidden
```

Verify `http://127.0.0.1:9223/ping`. Do not start a duplicate process when it
already responds. Browser tools call the loopback HTTP API and never auto-start
the daemon.

The daemon defaults to the Playwright-managed Chromium installed by
`python -m modules.bootstrap.dependencies install-browser chromium`. It stores
cookie/local-storage state under an ignored project-local directory. Optional
settings are `AGENT_FORGE_BROWSER_ENGINE`, `AGENT_FORGE_BROWSER_EXECUTABLE`,
`AGENT_FORGE_BROWSER_PROFILE`, `AGENT_FORGE_BROWSER_HEADLESS`,
`AGENT_FORGE_BROWSER_TIMEOUT_MS`, and `AGENT_FORGE_BROWSER_PORT`.
