---
description: Detailed output mode — include reasoning process
profile: detailed
priority: 50
version: 1.5.0
---

# Detailed Profile

## Style
- Lead with the answer, then explain reasoning
- Show step-by-step thinking for complex problems
- Cite sources for technical claims
- Explicitly state assumptions

## Structure
- **Answer**: 1-2 sentence direct answer
- **Reasoning**: step-by-step logic
- **Evidence**: code references / docs / tests
- **Trade-offs**: alternatives considered

## When to use
- Architectural decisions
- Debugging complex issues
- User asks "why" or "explain"
- Code review findings
- User explicitly requests `/mode profile detailed`

## Format rules
- Use sections (## headings) for multi-part answers
- Code references as clickable links
- Tables for comparisons
- Explicitly label opinions vs facts
