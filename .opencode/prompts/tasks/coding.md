---
description: Coding task prompt with TDD red-green-refactor emphasis
task_type: coding
leading_words: [red, green, seam, tracer-bullet]
priority: 100
version: 1.5.0
---

# Coding Task

## Trigger
- User requests implementing a feature / fixing a bug / adding tests / refactoring
- Keywords: "实现" / "修复" / "添加" / "refactor" / "implement" / "fix" / "add"

## Process

### 1. Identify the seam
Before writing code, identify the **seam** — the public boundary where behavior is observed. Tests live at seams, never against internals.

Ask: "What's the public interface, and which seams should we test?"

### 2. Red before green (TDD)
- Write the failing test first
- Only enough code to pass it
- Don't anticipate future tests or add speculative features
- One slice at a time: one seam, one test, one minimal implementation per cycle

### 3. Vertical slices, not horizontal
Work in **vertical slices** — one test → one implementation → repeat. Each test is a **tracer bullet** that responds to what the last cycle taught you.

Never write all tests first, then all implementation (horizontal slicing tests imagined behavior).

### 4. Refactoring is NOT part of the loop
Refactoring belongs to the review stage (see `review.md`), not the red → green implementation cycle.

## Anti-patterns to avoid
- **Implementation-coupled tests**: mocks internal collaborators, tests private methods → breaks on refactor
- **Tautological tests**: assertion recomputes expected value the way code does → passes by construction
- **Speculative generality**: abstraction/hooks for needs the spec doesn't have → delete it

## Completion criteria
- Every modified model accounted for (not "produce a change list")
- Each new behavior has a corresponding test at an agreed seam
- No speculative features added beyond spec
- Code review (`/review`) passes with Score ≥ 7/10

## Reference
- See `examples/python/test-driven.md` for TDD patterns (P1)
- See `examples/python/refactor-extract-method.md` for refactoring patterns (P1)
