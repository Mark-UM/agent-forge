---
description: Naming contract enforcement — spec doc names are non-negotiable (Phase 1 Direction 2)
priority: 92
version: 2.0.0
trigger: when spec/design doc (.md) is in working set OR .ts/.tsx/.py files touched
---

# Naming Contract (enforced)

> **Source**: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 2
> **Root cause addressed**: deepseek's "naming drift" — inventing synonyms
> ("GamePhase" for "GameState", "addNextBlock" for "generateBlock") instead of
> using the canonical names from the spec doc.

## Core Principle

Names in spec/design docs are **CONTRACTS**, not suggestions. Before implementing
any class / method / interface / event / enum, search the spec doc for the
canonical name. If the doc names it, you MUST use that exact name. No "I prefer
my convention."

## Doc-Aware Implementation Rule

1. **Before** writing any new class/method, grep the spec doc for candidate names
2. If doc has an explicit name → use it **verbatim** (case-sensitive)
3. If doc is silent → follow language convention:
   - TypeScript: PascalCase for classes/interfaces/types, camelCase for methods/vars, UPPER_SNAKE for constants
   - Python: PascalCase for classes, snake_case for functions/vars, UPPER_SNAKE for constants
4. **NEVER** invent a synonym because "it reads better" — doc wins

## Common Drift Patterns (banned)

| Doc Name | Banned Variant | Why Drift Happens (banned excuse) |
|----------|----------------|-----------------------------------|
| `GameState` | `GamePhase` | "phase sounds more accurate" — no, doc wins |
| `ScoreState` | `ScoreData` | "data is more common" — no, doc wins |
| `generateBlock` | `addNextBlock` | "add is simpler" — no, doc wins |
| `updateBlockPosition` | `update` | "shorter is cleaner" — no, doc wins |
| `calculateCut` | `calculateCutX` / `calculateCutZ` | "more specific" — no, doc wins |
| `game:stateChange` | `state:changed` | "shorter is cleaner" — no, doc wins |
| `SoundManager` | `AudioManager` | "audio is more general" — no, doc wins |
| `GameResult` | `GameOutcome` | "outcome is clearer" — no, doc wins |

## Event Naming Convention

Events are part of the public contract. Their names must be stable across the codebase.

| Category | Format | Example |
|----------|--------|---------|
| State change | `<domain>:stateChange` | `game:stateChange` |
| Score update | `<domain>:update` | `score:update` |
| Lifecycle | `<domain>:<phase>` | `game:start`, `game:over` |
| Error | `<domain>:error` | `audio:error` |

Banned: `state:changed`, `scoreChanged`, `onGameOver` (mixing event name with handler name).

## Self-Check Before Committing

- [ ] Every class name in my code appears verbatim in the spec doc OR follows
      convention for unnamed concepts
- [ ] Every public method name matches doc OR is clearly an internal helper
      (prefixed with `_` in Python, `private`/`#` in TS)
- [ ] Every event name matches doc's event section verbatim
- [ ] Every enum value matches doc's enum section verbatim
- [ ] No "I prefer X" deviations — if doc names it, doc wins

## Anti-patterns

- ❌ Inventing a synonym because the doc name "feels wrong" — that's not your call
- ❌ Renaming an existing doc-named entity during refactor without updating the doc first
- ❌ Mixing event names with handler names (`onGameOver` as event name)
- ❌ Adding suffix/prefix to doc name (`GameStateData`, `GameResultPayload`)
  unless the doc itself uses that suffix
