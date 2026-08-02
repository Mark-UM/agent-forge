---
description: Haskell functional programming conventions
language: haskell
priority: 80
version: 1.5.0
---

# Haskell Context

## Style
- Follow Haskell Programmers' Hangout style guide
- camelCase for functions/variables, PascalCase for types/modules
- 2-space indentation
- Maximum line length: 80 chars (soft), 100 chars (hard)
- Use `where` for top-level helpers, `let..in` for local bindings

## Project Conventions
- GHC 9.2+ with Cabal or Stack
- Extensions: enable per-module via `{-# LANGUAGE ... #-}`, not globally
- Common safe extensions: `LambdaCase`, `TupleSections`, `OverloadedStrings`, `BangPatterns`
- Avoid `OverloadedStrings` for low-level byte operations
- Imports: explicit lists (`import Data.Map (Map, fromList, lookup)`)

## Type System
- Annotate top-level functions always
- Use `newtype` for single-field wrappers (zero-cost)
- Use `data` for multi-field records
- Prefer `Maybe` / `Either` over partial functions (`head`, `tail`, `fromJust`)
- Use typeclasses for ad-hoc polymorphism; avoid orphan instances

## Common Patterns
- Recursion schemes (foldr/foldl') over explicit recursion
- `traverse` / `sequenceA` for effectful iteration
- `fmap` / `<$>` / `<*>` for functor/applicative composition
- Monads: `IO`, `Maybe`, `Either e`, `Reader`, `State` — use `do` notation for chains
- Lenses (lens package) for deep record updates

## Anti-patterns
- Partial functions (`head`, `tail`, `init`, `last`) → use `NonEmpty` or pattern matching
- `undefined` / `error` as placeholders → use `todo` from `tasty` or `undefined` only in drafts
- Excessive `IO` → push to edges, keep core pure
- Lazy I/O (`readFile` for huge files) → use `conduit` / `pipes` for streaming
- `unsafePerformIO` → almost never; if needed, document thoroughly

## Testing
- Framework: `tasty` (preferred) or `HUnit`
- Property testing: `QuickCheck` or `hedgehog`
- Test files: `test/Spec.hs` or `test/**/*.hs`
- Property: invariants + round-trip properties + edge cases
- Use `tasty-hunit` for unit tests, `tasty-quickcheck` for properties
