---
description: Planning task with HARD-GATE (design before implementation)
task_type: planning
leading_words: [destination, fog-of-war, decision-ticket, hard-gate]
priority: 100
version: 1.5.0
---

# Planning Task

## Trigger
- User requests new feature / refactor / architecture change / large task
- Keywords: "设计" / "规划" / "plan" / "架构" / "refactor large"

## HARD-GATE (Iron Rule)

<HARD-GATE>
Do NOT invoke any implementation skill, write any code, scaffold any project, or take any implementation action until you have:
1. Presented a design
2. User has explicitly approved it

This applies to EVERY planning task regardless of perceived simplicity.
</HARD-GATE>

## Anti-pattern: "This Is Too Simple To Need A Design"

Every project goes through this process. "Simple" projects are where unexamined assumptions cause the most wasted work. The design can be short (a few sentences), but you MUST present it and get approval.

## Process

### 1. Explore project context
- Check files, docs, recent commits
- Read `_data/memory/MEMORY.md` for project state
- Identify scope: single feature vs multi-subsystem

### 2. Ask clarifying questions (one at a time)
- Purpose / constraints / success criteria
- Prefer multiple choice; open-ended when needed
- Only one question per message

### 3. Propose 2-3 approaches
- With trade-offs
- Lead with recommendation + reasoning

### 4. Present design (section by section)
- Architecture / components / data flow / error handling / testing
- Scale each section to its complexity
- Ask after each section: "Does this look right so far?"

### 5. Write design doc
- Save to `_runtime/plans/YYYY-MM-DD-<topic>-design.md`
- Commit to git

### 6. Spec self-review
- Check for: placeholders / contradictions / ambiguity / scope

### 7. User reviews spec
- Ask user to review the spec file
- Revise on feedback

### 8. Transition to implementation
- Only after user approval
- Invoke coding.md for implementation

## Large efforts (wayfinder mode)

For efforts too big for one session:
- Chart a **shared map** of **decision tickets**
- Resolve one ticket per session
- Produce **decisions, not deliverables**
- Hand off to coding.md when the way is clear

## Completion criteria
- Design doc committed
- User explicitly approved (not silent)
- Implementation path clear
- No implementation action taken before approval
