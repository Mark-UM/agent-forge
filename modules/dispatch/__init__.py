"""
Dispatch Guard Module — Flash role enforcement (Phase 2)

Public API:
    from modules.dispatch import guard
    guard.call_flash(task_type, prompt, ...)  # safe entry point
    guard.check_task_allowed(task_type)       # boolean check (no side effects)
    guard.FLASH_ALLOWED_TASKS                 # frozenset of allowed task types
    guard.FLASH_FORBIDDEN_TASKS               # frozenset of forbidden task types
"""
from modules.dispatch.guard import (
    call_flash,
    check_task_allowed,
    FlashRoleViolationError,
    FLASH_ALLOWED_TASKS,
    FLASH_FORBIDDEN_TASKS,
    FLASH_MODEL,
    PRO_MODEL,
)

__all__ = [
    "call_flash",
    "check_task_allowed",
    "FlashRoleViolationError",
    "FLASH_ALLOWED_TASKS",
    "FLASH_FORBIDDEN_TASKS",
    "FLASH_MODEL",
    "PRO_MODEL",
]
