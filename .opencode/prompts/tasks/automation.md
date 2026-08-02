---
description: Automation task for browser / system automation
task_type: automation
leading_words: [playwright, daemon, headless, idempotent]
priority: 100
version: 1.5.0
---

# Automation Task

## Trigger
- User requests browser automation / system automation / batch processing
- Keywords: "浏览器" / "browser" / "自动化" / "automate" / "scrape"

## Process

### 1. Identify automation type
- **Browser**: use `modules/browser/daemon.py` (Playwright daemon) or browser MCP tools
- **System**: use RunCommand tool with explicit args (no shell injection)
- **Batch**: use Python script via `modules/` pattern

### 2. Design for idempotency
- Automation should be safe to re-run
- Track state in `_runtime/automation/` (e.g., last-run timestamp, processed items)
- Never assume "this will only run once"

### 3. Headless by default
- Browser automation: `headless=True` unless user requests UI
- System automation: no interactive prompts
- Log all actions to `_runtime/automation/<task>.log`

### 4. Error handling
- Network failures → retry with backoff (max 3 attempts)
- Page not found / element missing → screenshot + log, then fail
- Timeout → log + continue (don't hang the daemon)

### 5. Resource cleanup
- Close browser contexts after use
- Release file handles
- Kill stray processes

## Browser-specific rules

- Use the existing `modules/browser/daemon.py` (Playwright daemon) — do not spawn new Playwright instances
- For 9 browser tools (navigate/click/type/scroll/screenshot/etc.), use the TS extension tools
- For complex flows, write Python script using daemon API
- Persist cookies / sessions in `_runtime/browser/`

## System-specific rules

- Use RunCommand tool (NOT `os.system` in scripts)
- Quote file paths with spaces
- Set explicit `cwd` parameter
- Capture stdout + stderr separately

## Completion criteria
- Automation is idempotent (safe to re-run)
- Errors are logged with context (not silently swallowed)
- Resources cleaned up
- Headless mode verified (or user explicitly approved UI mode)
- `/review` passes

## Reference
- `modules/browser/daemon.py` for browser daemon API
- `.opencode/commands/browser.md` for browser slash command
