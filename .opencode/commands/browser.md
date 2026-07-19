---
description: Start the browser daemon (Playwright + Firefox) for browser automation
agent: build
---

# /browser — Start Browser Daemon

Start the browser daemon in the background. The daemon runs on port 9223 and provides HTTP API for browser automation.

Run the following command in background:
```
start /B "" "C:\Users\mingy\AppData\Local\Programs\Python\Python311\python.exe" modules/browser/daemon.py
```

Wait 2 seconds, then verify the daemon is running by checking `http://127.0.0.1:9223/ping`.

Report the daemon status to the user. If already running, report "Browser daemon already running".
