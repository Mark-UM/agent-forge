---
description: Two-axis code review (Standards + Spec)
task_type: review
leading_words: [standards, spec, smell, judgment-call]
priority: 100
version: 1.5.0
---

# Code Review Task

## Trigger
- User requests reviewing code / PR / diff
- Keywords: "审查" / "review" / "PR" / "diff"
- Calling `/review` command

## Two-axis review

A change can pass one axis and fail the other. Report them **separately** to stop one axis masking the other.

### Axis 1: Standards
Does the code conform to this repo's documented coding standards?

Sources (in priority order):
1. Repo's `CODING_STANDARDS.md` / `CONTRIBUTING.md` if exists
2. AGENTS.md constraints
3. **Smell baseline** (Fowler, _Refactoring_ ch.3):
   - Mysterious Name → rename
   - Duplicated Code → extract
   - Feature Envy → move method
   - Data Clumps → bundle into type
   - Primitive Obsession → give concept its own type
   - Repeated Switches → polymorphism
   - Shotgun Surgery → gather into one module
   - Divergent Change → split by reason
   - Speculative Generality → delete
   - Message Chains → hide walk
   - Middle Man → cut delegation
   - Refused Bequest → drop inheritance

**Rule**: documented repo standard always wins; baseline smells are always judgment calls (labelled heuristic, never hard violation).

### Axis 2: Spec
Does the code faithfully implement the originating issue / PRD / spec?

Check:
- (a) Requirements missing or partial
- (b) Behavior not asked for (scope creep)
- (c) Implementation looks wrong despite appearing implemented

Quote the spec line for each finding.

## Process

1. **Pin the fixed point**: `git diff <fixed-point>...HEAD` (three-dot, against merge-base)
2. **Identify spec source**: issue references / PRD file / ask user
3. **Spawn both sub-agents in parallel** (review-standards + review-spec) — single message, two Agent tool calls
4. **Aggregate**: present under `## Standards` and `## Spec` headings verbatim; do NOT merge or rerank

## Completion criteria
- Total findings per axis reported
- Worst issue within each axis identified
- No single winner across axes (reranking defeats the separation)

## Integration
This task integrates with the existing 3-stage review pipeline (review-code → review-structure → review-risk). Use `/review` to trigger the pipeline.
