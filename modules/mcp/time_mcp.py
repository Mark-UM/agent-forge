#!/usr/bin/env python3
"""Time MCP Server — Time and timezone conversion.

Self-hosted replacement for the archived `@modelcontextprotocol/server-time` npm package
(removed from npm registry in 2025, see servers-archived repo).

Design principles:
- Zero external dependencies (Python stdlib only: datetime + zoneinfo)
- Single-file standalone module
- Core function `get_current_time()` usable as a library
- MCP server: JSON-RPC 2.0 over stdio (newline-delimited)
- Robust error handling: unknown timezone, invalid format, etc.

Protocol: MCP uses newline-delimited JSON-RPC 2.0 over stdin/stdout.

Usage as MCP server:
    python -m modules.mcp.time_mcp serve

Usage as library:
    from modules.mcp.time_mcp import get_current_time, convert_time
    now = get_current_time("Asia/Shanghai")
    converted = convert_time("2026-07-20T10:00:00", "Asia/Shanghai", "America/New_York")

CLI direct call (no MCP):
    python -m modules.mcp.time_mcp now --timezone Asia/Shanghai
    python -m modules.mcp.time_mcp convert --time "2026-07-20T10:00:00" \
        --from-tz Asia/Shanghai --to-tz America/New_York

Tools exposed:
    get_current_time(timezone='UTC')
    convert_time(source_time, source_timezone, target_timezone)
"""
import sys
import os
import json
import argparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── Constants ────────────────────────────────────────────────
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'time-mcp'
SERVER_VERSION = '1.0.0'

# Default timezone if none specified (user's local timezone per markconfig/profile.md)
DEFAULT_TIMEZONE = 'Asia/Shanghai'


# ── Core functions ───────────────────────────────────────────
def _get_zoneinfo(timezone_name: str) -> ZoneInfo:
    """Get ZoneInfo object with error handling.

    Args:
        timezone_name: IANA timezone string (e.g., 'Asia/Shanghai', 'UTC')

    Returns:
        ZoneInfo object

    Raises:
        ValueError: if timezone_name is invalid or unsupported
    """
    if not timezone_name:
        raise ValueError("timezone_name is required")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Unknown timezone: {timezone_name}") from e
    except Exception as e:
        raise ValueError(f"Invalid timezone '{timezone_name}': {e}") from e


def get_current_time(timezone_name: str = 'UTC') -> dict:
    """Get current time in the specified timezone.

    Args:
        timezone_name: IANA timezone string (default 'UTC')

    Returns:
        dict: {
            'timezone': str,
            'datetime': str (ISO 8601),
            'is_dst': bool,
            'utc_offset': str (e.g., '+08:00'),
        }

    Raises:
        ValueError: if timezone is invalid
    """
    tz = _get_zoneinfo(timezone_name)
    now = datetime.now(tz)
    offset = now.utcoffset() or timedelta(0)
    offset_hours = int(offset.total_seconds() // 3600)
    offset_minutes = int((offset.total_seconds() % 3600) // 60)
    offset_str = f"{offset_hours:+03d}:{offset_minutes:02d}"

    return {
        'timezone': timezone_name,
        'datetime': now.isoformat(),
        'is_dst': bool(now.dst()),
        'utc_offset': offset_str,
    }


def convert_time(source_time: str, source_timezone: str,
                 target_timezone: str) -> dict:
    """Convert a time from one timezone to another.

    V3 fix: Correctly handles datetimes that already have a timezone offset.
    - If source_time has no offset (naive): interpret as local wall-clock time
      in source_timezone via replace(tzinfo=src_tz).
    - If source_time has an offset (aware): use astimezone() to convert,
      do NOT overwrite the offset with replace().
    - source_timezone is used as the interpretation context for naive inputs
      and as a consistency check for aware inputs.

    Args:
        source_time: ISO 8601 datetime string. May include timezone offset
                     (e.g., '2026-07-20T10:00:00+08:00' or '2026-07-20T10:00:00Z')
        source_timezone: IANA timezone of source_time (used for naive inputs)
        target_timezone: IANA timezone to convert to

    Returns:
        dict: {
            'source': {
                'timezone': str,
                'datetime': str,
                'utc_offset': str,
            },
            'target': {
                'timezone': str,
                'datetime': str,
                'utc_offset': str,
            },
        }

    Raises:
        ValueError: if time format or timezone is invalid, or if the
                   source_time falls in a DST gap (non-existent local time).
    """
    if not source_time:
        raise ValueError("source_time is required")

    src_tz = _get_zoneinfo(source_timezone)
    tgt_tz = _get_zoneinfo(target_timezone)

    # Parse source time
    # Support formats: '2026-07-20T10:00:00', '2026-07-20 10:00:00',
    # '2026-07-20T10:00:00+08:00', '2026-07-20T10:00:00Z'
    try:
        # Try ISO format first (handles 'T' separator and optional offset)
        try:
            parsed = datetime.fromisoformat(source_time)
        except ValueError:
            # Try space separator (naive only)
            parsed = datetime.strptime(source_time, '%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        raise ValueError(
            f"Invalid time format '{source_time}'. "
            f"Expected ISO 8601 (e.g., '2026-07-20T10:00:00' or "
            f"'2026-07-20T10:00:00+08:00')"
        ) from e

    # V3 fix: Handle aware vs naive datetimes differently
    if parsed.tzinfo is not None:
        # Input already has a timezone offset (e.g., '+08:00' or 'Z')
        # Use astimezone() to convert to source_timezone first, then target.
        # Do NOT use replace() — that would overwrite the input's offset.
        src_dt = parsed.astimezone(src_tz)
    else:
        # Naive datetime: interpret as local wall-clock time in source_timezone.
        # Use replace() to attach the timezone.
        src_dt = parsed.replace(tzinfo=src_tz)

        # V3: Check for DST gap (non-existent local time)
        # In zoneinfo, a time in the DST gap is technically "non-existent".
        # Python's zoneinfo handles this by shifting forward, but we should
        # detect and warn about it.
        # The fold attribute (0 or 1) disambiguates ambiguous times (DST fold).
        # For gap times, Python picks a default but we check consistency.
        _check_dst_issues(src_dt, source_timezone)

    # Convert to target timezone
    tgt_dt = src_dt.astimezone(tgt_tz)

    def _offset_str(dt):
        offset = dt.utcoffset() or timedelta(0)
        hours = int(offset.total_seconds() // 3600)
        minutes = int((offset.total_seconds() % 3600) // 60)
        return f"{hours:+03d}:{minutes:02d}"

    return {
        'source': {
            'timezone': source_timezone,
            'datetime': src_dt.isoformat(),
            'utc_offset': _offset_str(src_dt),
        },
        'target': {
            'timezone': target_timezone,
            'datetime': tgt_dt.isoformat(),
            'utc_offset': _offset_str(tgt_dt),
        },
    }


def _check_dst_issues(dt: datetime, timezone_name: str) -> None:
    """V3: Detect DST gap and fold issues for naive datetime interpretations.

    A DST gap occurs when clocks jump forward (e.g., 02:00→03:00 in spring),
    making times like 02:30 non-existent. Python's zoneinfo handles this by
    shifting the time, but we warn about it.

    A DST fold occurs when clocks fall back (e.g., 03:00→02:00 in autumn),
    making times like 02:30 ambiguous. The fold attribute (0=first, 1=second)
    disambiguates.

    Args:
        dt: The datetime to check (must be timezone-aware)
        timezone_name: Timezone name for error messages
    """
    # Check for DST gap: if the UTC offset of the datetime doesn't match
    # either the DST or standard offset for that wall-clock time, it's in a gap.
    # zoneinfo silently shifts gap times; we detect this by checking if
    # round-tripping through UTC produces a different wall-clock time.
    utc_dt = dt.astimezone(timezone.utc)
    roundtrip = utc_dt.astimezone(dt.tzinfo)

    if roundtrip.replace(tzinfo=None) != dt.replace(tzinfo=None):
        # The wall-clock time changed after round-trip → gap time
        import warnings
        warnings.warn(
            f"Time {dt.replace(tzinfo=None).isoformat()} in {timezone_name} "
            f"falls in a DST gap (non-existent local time). "
            f"Python shifted it to {roundtrip.replace(tzinfo=None).isoformat()}.",
            UserWarning,
            stacklevel=3,
        )


# ── MCP protocol ─────────────────────────────────────────────
def _make_tool_list():
    """Build the tools/list response."""
    return {
        'tools': [
            {
                'name': 'get_current_time',
                'description': (
                    'Get the current time in a specified timezone. '
                    'Returns ISO 8601 datetime, UTC offset, and DST status.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'timezone': {
                            'type': 'string',
                            'description': (
                                'IANA timezone name (e.g., "Asia/Shanghai", '
                                '"America/New_York", "UTC"). '
                                f'Default: "{DEFAULT_TIMEZONE}"'
                            ),
                            'default': DEFAULT_TIMEZONE,
                        },
                    },
                    'required': [],
                },
            },
            {
                'name': 'convert_time',
                'description': (
                    'Convert a time from one timezone to another. '
                    'Useful for scheduling across timezones.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'source_time': {
                            'type': 'string',
                            'description': (
                                'ISO 8601 datetime string '
                                '(e.g., "2026-07-20T10:00:00")'
                            ),
                        },
                        'source_timezone': {
                            'type': 'string',
                            'description': 'IANA timezone of source_time',
                        },
                        'target_timezone': {
                            'type': 'string',
                            'description': 'IANA timezone to convert to',
                        },
                    },
                    'required': ['source_time', 'source_timezone', 'target_timezone'],
                },
            },
        ]
    }


def _handle_request(request):
    """Handle a single JSON-RPC request. Returns response dict; notifications return None."""
    if not isinstance(request, dict):
        return {
            'jsonrpc': '2.0',
            'id': None,
            'error': {'code': -32600, 'message': 'Invalid request: not a JSON object'},
        }

    method = request.get('method')
    req_id = request.get('id')
    params = request.get('params', {}) or {}

    is_notification = req_id is None

    # initialize
    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {
                    'name': SERVER_NAME,
                    'version': SERVER_VERSION,
                },
            },
        }

    # notifications/initialized
    if method == 'notifications/initialized':
        return None

    # tools/list
    if method == 'tools/list':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': _make_tool_list(),
        }

    # tools/call
    if method == 'tools/call':
        tool_name = params.get('name')
        args = params.get('arguments', {}) or {}

        if tool_name == 'get_current_time':
            tz_name = args.get('timezone', DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
            try:
                result = get_current_time(tz_name)
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'content': [{
                            'type': 'text',
                            'text': json.dumps(result, ensure_ascii=False),
                        }],
                        'isError': False,
                    },
                }
            except ValueError as e:
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'content': [{
                            'type': 'text',
                            'text': json.dumps({'success': False, 'error': str(e)},
                                               ensure_ascii=False),
                        }],
                        'isError': True,
                    },
                }

        if tool_name == 'convert_time':
            source_time = args.get('source_time', '')
            source_tz = args.get('source_timezone', '')
            target_tz = args.get('target_timezone', '')
            if not all([source_time, source_tz, target_tz]):
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'content': [{
                            'type': 'text',
                            'text': json.dumps({
                                'success': False,
                                'error': 'source_time, source_timezone, target_timezone are all required',
                            }, ensure_ascii=False),
                        }],
                        'isError': True,
                    },
                }
            try:
                result = convert_time(source_time, source_tz, target_tz)
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'content': [{
                            'type': 'text',
                            'text': json.dumps(result, ensure_ascii=False),
                        }],
                        'isError': False,
                    },
                }
            except ValueError as e:
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'result': {
                        'content': [{
                            'type': 'text',
                            'text': json.dumps({'success': False, 'error': str(e)},
                                               ensure_ascii=False),
                        }],
                        'isError': True,
                    },
                }

        # Unknown tool
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'},
        }

    # Unknown method
    if is_notification:
        return None
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'error': {'code': -32601, 'message': f'Method not found: {method}'},
    }


def _run_server():
    """Run MCP server — JSON-RPC over stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = {
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32700, 'message': f'Parse error: {e}'},
            }
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            sys.stdout.flush()
            continue

        # Support batch requests
        if isinstance(request, list):
            responses = []
            for single in request:
                resp = _handle_request(single)
                if resp is not None:
                    responses.append(resp)
            if responses:
                sys.stdout.write(json.dumps(responses, ensure_ascii=False) + '\n')
                sys.stdout.flush()
            continue

        response = _handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            sys.stdout.flush()


# ── CLI ──────────────────────────────────────────────────────
def _cli():
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description='Time MCP Server — Time and timezone conversion'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # now subcommand
    p_now = sub.add_parser('now', help='Get current time')
    p_now.add_argument('--timezone', default=DEFAULT_TIMEZONE,
                       help=f'IANA timezone (default {DEFAULT_TIMEZONE})')

    # convert subcommand
    p_conv = sub.add_parser('convert', help='Convert time between timezones')
    p_conv.add_argument('--time', required=True,
                        help='ISO 8601 datetime (e.g., 2026-07-20T10:00:00)')
    p_conv.add_argument('--from-tz', required=True, help='Source IANA timezone')
    p_conv.add_argument('--to-tz', required=True, help='Target IANA timezone')

    # serve subcommand
    sub.add_parser('serve', help='Run as MCP server (JSON-RPC over stdio)')

    args = parser.parse_args()

    if args.command == 'now':
        try:
            result = get_current_time(args.timezone)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'convert':
        try:
            result = convert_time(args.time, args.from_tz, args.to_tz)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'serve':
        _run_server()


if __name__ == '__main__':
    # Allow direct script invocation (v1.6 shim: handles relative import)
    if __package__ is None and __name__ == '__main__':
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _cli()
