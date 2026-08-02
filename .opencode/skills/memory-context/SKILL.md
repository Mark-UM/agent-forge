---
description: Load ignored local structured memory only when personalized context is required
---

# Memory Context Skill

When a task requires detailed personal context, read only the relevant ignored
local memory files. They may not exist in a clean clone.

## Primary Memory Files
- `_data/memory/MEMORY.md` — optional core memory summary
- `_data/memory/user-profile.md` — Detailed user profile
- `_data/memory/user-personality.md` — Personality traits and communication preferences
- `_data/memory/user-tech-stack.md` — Technical skills and stack details
- `_data/memory/user-career.md` — Career goals and aspirations
- `_data/memory/user-real-life.md` — Real life context (health, finance, hobbies)
- `_data/memory/user-reply-preferences.md` — Reply style and format preferences

## Configuration Files
- `markconfig/profile.md` — Personal profile (loaded via opencode.json instructions)
- `markconfig/secrets.json` — API keys (never display or log these)

## Usage
- For general tasks: `markconfig/profile.md` is loaded by `opencode.json` when present.
- For personalized advice: Read relevant `_data/memory/user-*.md` files.
- For career/tech guidance: Read `user-career.md` and `user-tech-stack.md`.
- Never log or display contents of `secrets.json`.
- Never assume any private memory file exists or is safe to commit.
- `python -m modules.memory.hook health` reports structure/counts without
  returning private content.
- `python -m modules.memory.hook review` creates an ignored, read-only report of
  explicit unchecked tasks/TODO lines. It does not modify source memory.
- Lesson/decision appends occur only through explicit calls. There is no
  automatic conversation capture or hidden event registration.
