"""modules.common.time_utils — UTC normalization helpers (Round 2 Phase 1).

All persisted timestamps MUST go through `to_utc_iso()`. The original
timezone and original due_at are preserved alongside the normalized UTC
value so callers can render local times without re-deriving them.

Handles:
    - naive datetime → assume source_timezone (default from config)
    - aware datetime → convert to UTC via astimezone()
    - ISO 8601 strings with offset → parse → convert
    - DST gap / fold → raise or require explicit fold policy
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from backports.zoneinfo import ZoneInfo as _BackportZoneInfo  # type: ignore
except ImportError:  # pragma: no cover
    _BackportZoneInfo = None  # type: ignore

# Default user timezone. Read from env or config; never hardcode in domain modules.
# Resolved lazily so tests can monkey-patch DEFAULT_USER_TIMEZONE.
DEFAULT_USER_TIMEZONE = 'Asia/Shanghai'


def _resolve_zoneinfo(name: str) -> tzinfo:
    """Resolve a ZoneInfo from stdlib or backport. Raises if unknown."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if _BackportZoneInfo is not None:
            return _BackportZoneInfo(name)
        raise


def parse_iso_with_tz(s: str,
                      default_timezone: Optional[str] = None) -> datetime:
    """Parse an ISO 8601 string into an aware datetime.

    If the string has no offset, `default_timezone` (or DEFAULT_USER_TIMEZONE)
    is attached. Raises ValueError on malformed input.
    """
    if not s:
        raise ValueError('empty timestamp string')
    s = s.strip()
    # Python 3.11 fromisoformat accepts 'Z' suffix and most ISO variants.
    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'invalid ISO 8601 timestamp: {s!r}') from exc
    if dt.tzinfo is None:
        tz_name = default_timezone or DEFAULT_USER_TIMEZONE
        dt = dt.replace(tzinfo=_resolve_zoneinfo(tz_name))
    return dt


def to_utc_iso(dt: Optional[datetime] = None,
               *,
               source_timezone: Optional[str] = None,
               original: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str]]:
    """Normalize a datetime to UTC ISO 8601.

    Args:
        dt: datetime to normalize. If naive, `source_timezone` is attached.
            If None, uses current UTC time.
        source_timezone: timezone name to attach when dt is naive
            (defaults to DEFAULT_USER_TIMEZONE).
        original: original due_at string to preserve verbatim.

    Returns:
        (utc_iso, source_timezone_name, original_due_at)
        - utc_iso: normalized UTC ISO 8601 with explicit +00:00 offset
        - source_timezone_name: timezone that was attached (None if dt was aware)
        - original_due_at: the `original` arg, preserved verbatim
    """
    if dt is None:
        return datetime.now(timezone.utc).isoformat(), None, original

    attached_tz: Optional[str] = None
    if dt.tzinfo is None:
        tz_name = source_timezone or DEFAULT_USER_TIMEZONE
        dt = dt.replace(tzinfo=_resolve_zoneinfo(tz_name))
        attached_tz = tz_name
    else:
        # Aware datetime: preserve original offset, do NOT overwrite.
        # astimezone() converts while preserving the instant.
        pass

    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.isoformat(), attached_tz, original


def now_utc_iso() -> str:
    """Current time in UTC ISO 8601 with explicit +00:00 offset."""
    return datetime.now(timezone.utc).isoformat()


def format_offset(minutes: int) -> str:
    """Format a UTC offset in minutes as ±HH:MM.

    Handles negative and non-hour offsets correctly (e.g. +05:30, -03:30,
    +05:45, -09:30). Used by Time MCP and Action Extractor.
    """
    if minutes == 0:
        return '+00:00'
    sign = '+' if minutes > 0 else '-'
    abs_min = abs(minutes)
    hours, rem = divmod(abs_min, 60)
    return f'{sign}{hours:02d}:{rem:02d}'


def handle_dst_gap(dt: datetime, tz: tzinfo,
                   policy: str = 'reject') -> datetime:
    """Handle a DST gap (e.g. spring-forward 02:00→03:00).

    Args:
        dt: naive datetime to localize.
        tz: target timezone.
        policy: 'reject' (raise ValueError), 'shift_forward' (use later time),
            'shift_backward' (use earlier time).

    Raises:
        ValueError: if policy='reject' and dt falls in a DST gap.
    """
    # Try to detect a gap by constructing both fold values and comparing.
    dt_forward = dt.replace(tzinfo=tz, fold=0)
    dt_backward = dt.replace(tzinfo=tz, fold=1)
    # If both folds give the same UTC instant, it's not a gap.
    if dt_forward.utcoffset() == dt_backward.utcoffset():
        return dt_forward
    if policy == 'reject':
        raise ValueError(
            f'timestamp {dt.isoformat()} falls in a DST gap in {tz}; '
            f'explicit policy required (shift_forward / shift_backward)'
        )
    if policy == 'shift_forward':
        return dt_forward
    if policy == 'shift_backward':
        return dt_backward
    raise ValueError(f'unknown DST policy: {policy!r}')


def handle_dst_fold(dt: datetime, tz: tzinfo,
                    fold: Optional[int] = None) -> datetime:
    """Handle a DST fold (autumn overlap, e.g. 02:30 occurs twice).

    Args:
        dt: naive datetime to localize.
        tz: target timezone.
        fold: 0 (first occurrence, "summer time") or 1 (second, "winter time").
            If None, raises ValueError to force explicit disambiguation.

    Raises:
        ValueError: if fold is None and the time is ambiguous.
    """
    if fold is None:
        # Detect ambiguity: utcoffset differs between fold=0 and fold=1
        dt0 = dt.replace(tzinfo=tz, fold=0)
        dt1 = dt.replace(tzinfo=tz, fold=1)
        if dt0.utcoffset() != dt1.utcoffset():
            raise ValueError(
                f'timestamp {dt.isoformat()} is ambiguous in {tz} '
                f'(DST fold); specify fold=0 or fold=1'
            )
        return dt0
    if fold not in (0, 1):
        raise ValueError(f'fold must be 0 or 1, got {fold!r}')
    return dt.replace(tzinfo=tz, fold=fold)
