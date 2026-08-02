---
description: Task router — maps user intent to task prompt
priority: 10
version: 1.5.0
---

# Task Router

You don't remember every task prompt, so route.

## Main flow: intent → task prompt

| Intent signals | Route to |
|----------------|----------|
| "实现" / "修复" / "implement" / "fix" / "add" | `tasks/coding.md` |
| "审查" / "review" / "PR" / "diff" | `tasks/review.md` |
| "搜索" / "调研" / "research" / "compare" | `tasks/research.md` |
| "bug" / "崩溃" / "error" / "crash" / "fail" | `tasks/debugging.md` |
| "设计" / "规划" / "plan" / "architecture" | `tasks/planning.md` |
| "文档" / "报告" / "write" / "document" | `tasks/writing.md` |
| "浏览器" / "browser" / "自动化" / "automate" | `tasks/automation.md` |
| "完成" / "交付" / "验收" / "deliver" / "done" / "finish" | `tasks/delivery.md` |

## On-ramps

- **Vague request** → ask one clarifying question, then route
- **Multi-intent** → route to the dominant intent; mention secondary intents
- **Conflicting** (e.g., "fix and refactor") → route to debugging first, then coding

## Override

User can explicitly switch via `/mode <task>`. Explicit switch always wins over auto-detection.

## Reference
- Each task prompt has its own trigger section — read it after routing
