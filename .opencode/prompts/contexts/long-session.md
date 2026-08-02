---
description: Long session context (≥50 turns) — context compaction awareness
session_length: long
priority: 70
version: 1.5.0
---

# Long Session Context

## When this loads
- Session has ≥ 50 turns
- Context window is filling up
- OpenCode may compact history soon

## Strategies

### 1. Prefer handoff over re-explanation
- If the user asks about something already discussed, check `_runtime/handoff/` first
- If no handoff exists, offer to create one with `/handoff` rather than re-explaining

### 2. Compress references
- Don't re-read files already read; cite from memory
- If a file was modified since last read, re-read only the changed sections

### 3. Summarize prior work
- When user asks "what did we do?", produce a summary from `_data/memory/MEMORY.md` or composition log
- Don't reconstruct from conversation history (it may be compacted)

### 4. Suggest handoff at natural boundaries
- After completing a task → suggest `/handoff` before starting a new topic
- After significant code changes → suggest committing before continuing

### 5. Avoid re-introducing context
- If the user references "the file we edited earlier", ask which one (don't guess)
- If unsure whether context is still available, ask user to restate the requirement

## Anti-patterns
- Re-reading files unnecessarily → wastes context budget
- Long summaries of prior work → use pointers to memory files instead
- Re-explaining concepts already covered → assume user remembers; only recap on explicit request

## Handoff content
When creating a handoff with `/handoff`, include:
- Current task and its status
- Files modified in this session
- Open questions or blockers
- Next steps
- Relevant memory entries
