---
description: Memory file update rules (_data/memory/)
directory: memory
priority: 90
version: 1.5.0
---

# Memory Context

## File Structure
- `MEMORY.md` — main memory index
- `user-profile.md` — user background and preferences
- `lessons.md` — lessons learned from failures
- `decisions.md` — architectural decisions records (ADR)

## Update Triggers
- User says "remember this" → append to relevant file
- Code review FAIL → add lesson to `lessons.md`
- Architectural decision made → add ADR to `decisions.md`
- User profile changes → update `user-profile.md`

## Update Rules
1. **Append, don't overwrite** — preserve prior content
2. **Date-stamp** every entry: `## YYYY-MM-DD — <topic>`
3. **Cite source** — link to commit/PR/file that triggered the entry
4. **Keep it terse** — one paragraph per entry, not a novel
5. **Categorize** — use existing sections; create new only if needed

## Format
```markdown
## YYYY-MM-DD — <topic>

**Trigger**: <what caused this entry>
**Decision**: <what was decided>
**Rationale**: <why>
**Source**: <commit hash / file:line / PR>
```

## Anti-patterns
- Deleting prior entries without explicit user request
- Vague entries ("fixed bug") — be specific
- Future predictions as facts — label them as predictions
- Personal opinions as universal rules — label as opinions
- Mass reorganization without user approval

## Privacy
- Memory files may contain personal info (location, contacts, schedule)
- Never log memory file contents to stdout
- Never share memory file contents in web requests
- Use `modules/search/privacy.py` for PII redaction when needed
