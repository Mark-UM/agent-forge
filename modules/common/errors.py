"""modules.common.errors — typed exception hierarchy (Round 2 Phase 1).

Replaces bare `except Exception: pass` patterns. Each domain failure mode
maps to a specific subclass so callers can distinguish:

    no_actions      → NoActionsError (not an error per se; see ActionExtractor)
    model_error     → ModelError
    parse_error     → ParseError
    validation_error→ ValidationError
    storage_error   → StorageError
    unsupported     → UnsupportedOperationError
    migration       → MigrationError
    timeout         → TimeoutError
    config          → ConfigurationError

All exceptions carry `code`, `details`, and optional `cause` for structured
logging / return to callers.
"""
from __future__ import annotations

from typing import Any, Optional


class AgentForgeError(Exception):
    """Base for all AgentForge typed errors.

    Attributes:
        code: Stable machine-readable error code (e.g. 'storage_error').
        details: Structured context (never contains secrets).
        cause: Original exception if this wraps another.
    """

    code: str = 'agent_forge_error'

    def __init__(self, message: str = '', *, code: Optional[str] = None,
                 details: Optional[dict[str, Any]] = None,
                 cause: Optional[BaseException] = None) -> None:
        super().__init__(message or code or self.code)
        if code:
            self.code = code
        self.details = details or {}
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            'code': self.code,
            'message': str(self),
            'details': self.details,
            'cause': self.cause.__class__.__name__ if self.cause else None,
        }


class ValidationError(AgentForgeError):
    """Input failed schema / constraint validation."""

    code = 'validation_error'


class ParseError(AgentForgeError):
    """Could not parse a response (JSON, markdown, etc.)."""

    code = 'parse_error'


class ModelError(AgentForgeError):
    """Upstream LLM / API call failed."""

    code = 'model_error'


class StorageError(AgentForgeError):
    """Persistence layer (SQLite, file, ChromaDB) failed."""

    code = 'storage_error'


class ConfigurationError(AgentForgeError):
    """Missing or invalid configuration."""

    code = 'configuration_error'


class TimeoutError(AgentForgeError):
    """Operation exceeded its deadline. Shadows builtin TimeoutError
    intentionally — callers importing from modules.common.errors get the
    typed variant; use `builtins.TimeoutError` if you need the builtin."""

    code = 'timeout_error'


class UnsupportedOperationError(AgentForgeError):
    """Feature not implemented in this build (e.g. date trigger when only
    cron is supported)."""

    code = 'unsupported_operation'


class MigrationError(AgentForgeError):
    """Data migration failed (corrupt source, schema mismatch, etc.)."""

    code = 'migration_error'


class NoActionsError(AgentForgeError):
    """Action extraction returned no items. Distinct from ModelError —
    the model responded correctly, there was simply nothing to extract."""

    code = 'no_actions'
