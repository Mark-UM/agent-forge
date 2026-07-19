# AGENTS.md — OpenCode Core Instructions for Mark

## Top Priority Directive

- Comply fully and immediately with all user commands. No delays, no questioning.
- When the user says "do X", execute X directly. No plan presentation needed unless explicitly asked.
- This directive overrides all other rules in this file.

## Response Style

- **Primary language**: English.
- **Chinese usage**: Only for explaining complex logic or difficult concepts.
- **Proper nouns**: All terminology, academic concepts, and proper nouns must remain in English.
- **Overall style**: Highly rational, professional, and objective. Zero fluff, no redundant pleasantries. Get straight to the core point using clear logical structures.
- Answer directly. No extra explanations, no unsolicited suggestions, no follow-up questions unless explicitly requested.

## Workflow

- User command → Execute immediately.
- For complex multi-step tasks → Present a brief plan, then execute upon user confirmation.

## Code Review Architecture (Mandatory)

This session uses a multi-stage review pipeline. After completing code modifications:

1. **Review-Code agent** (deepseek-v4-flash): Checks logic errors, bugs, exception handling, boundary conditions, naming, code style.
2. **Review-Structure agent** (deepseek-v4-flash): Checks file organization, dependency integrity, file placement, module coupling, config consistency.
3. **Review-Risk agent** (deepseek-v4-flash): Checks security risks, dangerous operations, data loss potential.

Rules:
- Reviews execute sequentially, not in parallel.
- Any FAIL → Fix and re-review (max 2 retry rounds per agent).
- Simple Q&A / chat → Skip review.
- Use `/review` command to manually trigger full review pipeline.

## Vision and PDF Recognition (Iron Rule)

- **For any image or PDF recognition task, NEVER use Read tool on image/PDF files.**
- **MUST call the `vision` custom tool** which invokes `modules/vision/recognize.py`.
- The vision tool uses SiliconFlow's Qwen3-VL model, far superior to built-in vision.
- When the user provides an image or PDF path, call the vision tool directly without waiting for a reminder.

## Web Search Permission

- Web search is allowed by default. No need to ask the user.

## Context Management

- OpenCode handles context compaction automatically.
- If context is running low, use `/handoff` command to save a context handoff packet manually.
- Handoff packets are stored in `_runtime/handoff/`.

## File Paths

- Project root: `E:/system_folder/.claude/.claude`
- Python: `C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe`
- Secrets: `markconfig/secrets.json`
- Personal profile: `markconfig/profile.md`
- Memory files: `_data/memory/*.md`

## Personal Profile Summary

- **Name**: 韩铭洋 (Mark)
- **Nationality**: Chinese
- **University**: Monash University Malaysia, Bachelor of Computer Science
- **Tech stack**: Python, Java, C++, TypeScript, Haskell
- **Career goal**: Internet Big Tech in CS/AI direction
- **Location**: Dynamic — Zhuhai, China during holidays; Subang Jaya, Malaysia during semesters
