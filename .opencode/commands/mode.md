---
description: Switch task type or profile in current session (v1.5)
---

# /mode Command

Switch the task type or output profile for the current session.

## Usage

```
/mode <type>          # Switch task type
/mode profile <p>     # Switch profile
/mode status          # Show current mode
/mode reset           # Reset to default
```

## Task types

- `coding` — Implement features, fix bugs, add tests (TDD red-green-refactor)
- `review` — Code review (two-axis: Standards + Spec)
- `research` — Web search, technical research (three-layer + MindSearch)
- `debugging` — Systematic debugging (root-cause-tracing)
- `planning` — Design + plan (HARD-GATE: design before implementation)
- `writing` — Documentation, reports, prose
- `automation` — Browser / system automation

## Profiles

- `default` — Balanced (lead with answer, then brief reasoning)
- `terse` — One-line answers when possible
- `detailed` — Include reasoning process step-by-step
- `socratic` — Guide user's thinking with questions

## Behavior

When invoked, the command should:

1. Parse the argument:
   - If `<type>` is a valid task type → Read `.opencode/prompts/tasks/{type}.md`
   - If `profile <p>` → Read `.opencode/prompts/profiles/{p}.md`
   - If `status` → show current task + profile (read from `_runtime/prompt/version.json`)
   - If `reset` → clear current task, set profile to default
2. Inject the loaded prompt content into current session as additional context
3. Confirm the switch to the user in one line

## Examples

```
/mode coding         # → load tasks/coding.md, confirm switch
/mode review         # → load tasks/review.md, confirm switch
/mode profile terse  # → load profiles/terse.md, confirm switch
/mode status         # → "Current: task=coding, profile=default"
/mode reset          # → "Reset to default"
```

## Implementation notes

- The slash command file is parsed by OpenCode; the body above is the instruction set
- Loading is done by the Agent reading the corresponding prompt file
- For programmatic composition (e.g., generating a new AGENTS_COMPOSED.md), use:
  ```
  python modules/prompt/composer.py --task coding --profile terse
  ```
