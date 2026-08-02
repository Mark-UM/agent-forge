"""
Prompt Composer Module (v1.5 P0)

Dynamic prompt assembly engine for OpenCode.

Modules:
- composer: core assembly engine
- context: context signal detection
- classify: task type classification (heuristic + flash)
- log: composition logging + version snapshot

CLI:
    python -m modules.prompt.composer --pre-session
    python -m modules.prompt.composer --task coding --profile terse
    python -m modules.prompt.composer --list-tasks
"""
__version__ = "1.5.0"
