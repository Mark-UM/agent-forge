---
description: Systematic debugging with root-cause-tracing
task_type: debugging
leading_words: [red, root-cause, tight-loop, defense-in-depth]
priority: 100
version: 1.5.0
---

# Debugging Task

## Trigger
- User reports a bug / exception / abnormal behavior
- Keywords: "bug" / "崩溃" / "异常" / "不工作" / "error" / "crash" / "fail"

## Iron Rule: Refuse to theorize until you have a tight feedback loop

A **tight loop** is one command that already goes **red** on *this* bug. Before any hypothesis:
1. Reproduce the bug with a single command
2. Confirm the reproduction is reliable (≥ 2 consecutive runs)

## Process

### 1. Reproduce (red)
- Write the smallest test that reproduces the bug
- The test must fail (red) on the current code
- If you can't reproduce → you can't fix; ask user for repro steps

### 2. Root-cause tracing
- **Never fix symptoms.** A symptom fix moves the bug, doesn't kill it.
- Use binary search / `git bisect` to locate the introducing commit
- Read the diff of the introducing commit
- Identify the **root cause** at the deepest layer

### 3. Fix at the root
- Fix the root cause, not the symptom
- The reproduction test must now pass (green)
- Add a regression test that would catch this bug class in the future

### 4. Defense in depth
- Ask: "What other code paths could trigger the same root cause?"
- Add assertions / guards at upstream boundaries
- Document the bug class in `_data/memory/lessons.md` (if exists)

## Anti-patterns
- **Shotgun fix**: changing 5 places hoping one works → indicates root cause not found
- **Symptom suppression**: try/except around the error → hides the bug
- **Fix without test**: "I'm sure it's fixed" → regression guaranteed

## Completion criteria
- Single reproduction test committed
- Root cause identified and documented (one sentence)
- Fix is minimal (touches only the root cause layer)
- Regression test added
- `/review` passes

## Reference
- obra `systematic-debugging` skill for full root-cause-tracing methodology
