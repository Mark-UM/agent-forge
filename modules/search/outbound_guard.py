"""Shared fail-closed guard for query text sent to external search services."""
from __future__ import annotations

from typing import Callable


class OutboundRedactionError(RuntimeError):
    """Raised when an outbound query cannot be safely redacted."""


def require_redacted_query(
    query: str, redactor: Callable | None,
) -> tuple[str, dict]:
    """Return a safe query or stop the caller before any network request."""
    if redactor is None:
        raise OutboundRedactionError('Outbound query redaction unavailable')
    try:
        redacted, metadata = redactor(query)
    except Exception as exc:
        raise OutboundRedactionError(
            'Outbound query redaction failed'
        ) from exc
    if (not isinstance(metadata, dict) or metadata.get('error') or
            not isinstance(redacted, str) or not redacted.strip()):
        raise OutboundRedactionError('Outbound query redaction failed')
    return redacted, metadata
