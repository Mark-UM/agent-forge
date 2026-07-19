---
description: Load Mark's structured memory files for personalized context
---

# Memory Context Skill

When you need detailed personal context about the user (Mark), read the following memory files:

## Primary Memory Files
- `_data/memory/MEMORY.md` — Core memory summary (always loaded via instructions)
- `_data/memory/user-profile.md` — Detailed user profile
- `_data/memory/user-personality.md` — Personality traits and communication preferences
- `_data/memory/user-tech-stack.md` — Technical skills and stack details
- `_data/memory/user-career.md` — Career goals and aspirations
- `_data/memory/user-real-life.md` — Real life context (health, finance, hobbies)
- `_data/memory/user-reply-preferences.md` — Reply style and format preferences

## Configuration Files
- `markconfig/profile.md` — Personal profile (loaded via opencode.json instructions)
- `markconfig/paths.json` — System paths
- `markconfig/secrets.json` — API keys (never display or log these)

## Usage
- For general tasks: `markconfig/profile.md` is auto-loaded. Sufficient.
- For personalized advice: Read relevant `_data/memory/user-*.md` files.
- For career/tech guidance: Read `user-career.md` and `user-tech-stack.md`.
- Never log or display contents of `secrets.json`.
