---
description: Context handoff — save current session state for resumption
argument-hint: "[save|list|clean]"
agent: build
---

# /handoff — Context Handoff Command

## Subcommands

### `/handoff save` (default)
Generate a context handoff packet containing:
1. **User Original Goal**: What the user asked for
2. **Current State**: What has been completed
3. **Modified Files**: List of all files changed
4. **Commands Run**: Key commands executed and their results
5. **Next Actions**: Immediate next steps
6. **Prohibitions**: Safety rules to carry forward

Save the packet to `_runtime/handoff/context_handoff_YYYYMMDD_HHMMSS.md` and update `_runtime/handoff/LATEST.md`.

### `/handoff list`
List all historical handoff packets in `_runtime/handoff/`.

### `/handoff clean`
Remove handoff packets older than 7 days.

## Implementation

Generate the handoff packet directly by writing a markdown file. Use the `write` tool to create `_runtime/handoff/context_handoff_YYYYMMDD_HHMMSS.md` (use current date/time) and overwrite `_runtime/handoff/LATEST.md` with the same content.

For `/handoff list`, use `ls _runtime/handoff/` via the bash tool to enumerate files.

For `/handoff clean`, delete files in `_runtime/handoff/` older than 7 days (exclude `LATEST.md`).

### Handoff Packet Template

```markdown
# Context Handoff — YYYY-MM-DD HH:MM

## User Original Goal
<original user request>

## Current State
<what has been completed>

## Modified Files
- <file path 1>
- <file path 2>

## Commands Run
- <command> → <result>

## Next Actions
1. <next step 1>
2. <next step 2>

## Prohibitions
- Do not delete markconfig/
- Do not expose secrets.json contents
- Follow AGENTS.md response style
```
