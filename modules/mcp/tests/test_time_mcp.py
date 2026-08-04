"""Tests for modules.mcp.time_mcp (v1.6)"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.mcp import time_mcp


# ---------- get_current_time ----------

def test_get_current_time_utc():
    result = time_mcp.get_current_time('UTC')
    assert result['timezone'] == 'UTC'
    assert '+00:00' in result['datetime']
    assert result['utc_offset'] == '+00:00'
    assert isinstance(result['is_dst'], bool)


def test_get_current_time_shanghai():
    result = time_mcp.get_current_time('Asia/Shanghai')
    assert result['timezone'] == 'Asia/Shanghai'
    assert '+08:00' in result['datetime']
    assert result['utc_offset'] == '+08:00'


def test_get_current_time_new_york():
    result = time_mcp.get_current_time('America/New_York')
    assert result['timezone'] == 'America/New_York'
    # New York is either -05:00 (EST) or -04:00 (EDT)
    assert result['utc_offset'] in ('-05:00', '-04:00')


def test_get_current_time_invalid_timezone():
    with pytest.raises(ValueError, match="Unknown timezone"):
        time_mcp.get_current_time('Invalid/Timezone')


def test_get_current_time_empty_timezone():
    with pytest.raises(ValueError, match="timezone_name is required"):
        time_mcp.get_current_time('')


# ---------- convert_time ----------

def test_convert_time_same_timezone():
    result = time_mcp.convert_time('2026-07-20T10:00:00', 'Asia/Shanghai', 'Asia/Shanghai')
    assert result['source']['datetime'] == result['target']['datetime']
    assert result['source']['utc_offset'] == result['target']['utc_offset']


def test_convert_time_shanghai_to_new_york():
    result = time_mcp.convert_time('2026-07-20T10:00:00', 'Asia/Shanghai', 'America/New_York')
    # Shanghai is UTC+8, New York is UTC-4 (EDT in July) or UTC-5 (EST)
    # Difference: 12 or 13 hours
    src_hour = int(result['source']['datetime'][11:13])
    tgt_hour = int(result['target']['datetime'][11:13])
    diff = (src_hour - tgt_hour) % 24
    assert diff in (12, 13)


def test_convert_time_utc_to_shanghai():
    result = time_mcp.convert_time('2026-07-20T10:00:00', 'UTC', 'Asia/Shanghai')
    # UTC 10:00 -> Shanghai 18:00 (+8)
    assert 'T18:00:00' in result['target']['datetime']


def test_convert_time_space_separator():
    """Support '2026-07-20 10:00:00' (space separator)."""
    result = time_mcp.convert_time('2026-07-20 10:00:00', 'UTC', 'Asia/Shanghai')
    assert 'T18:00:00' in result['target']['datetime']


def test_convert_time_invalid_format():
    with pytest.raises(ValueError, match="Invalid time format"):
        time_mcp.convert_time('not-a-date', 'UTC', 'Asia/Shanghai')


def test_convert_time_invalid_timezone():
    with pytest.raises(ValueError, match="Unknown timezone"):
        time_mcp.convert_time('2026-07-20T10:00:00', 'UTC', 'Invalid/Zone')


def test_convert_time_empty_source_time():
    with pytest.raises(ValueError, match="source_time is required"):
        time_mcp.convert_time('', 'UTC', 'Asia/Shanghai')


# ---------- MCP protocol: _handle_request ----------

def test_handle_initialize():
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = time_mcp._handle_request(req)
    assert resp['jsonrpc'] == '2.0'
    assert resp['id'] == 1
    assert resp['result']['protocolVersion'] == '2024-11-05'
    assert resp['result']['serverInfo']['name'] == 'time-mcp'


def test_handle_tools_list():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = time_mcp._handle_request(req)
    tool_names = [t['name'] for t in resp['result']['tools']]
    assert 'get_current_time' in tool_names
    assert 'convert_time' in tool_names


def test_handle_call_get_current_time():
    req = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_current_time", "arguments": {"timezone": "UTC"}}
    }
    resp = time_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    assert data['timezone'] == 'UTC'


def test_handle_call_convert_time():
    req = {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {
            "name": "convert_time",
            "arguments": {
                "source_time": "2026-07-20T10:00:00",
                "source_timezone": "UTC",
                "target_timezone": "Asia/Shanghai",
            }
        }
    }
    resp = time_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'T18:00:00' in data['target']['datetime']


def test_handle_call_invalid_timezone():
    req = {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "get_current_time", "arguments": {"timezone": "Invalid/Zone"}}
    }
    resp = time_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'Unknown timezone' in data['error']


def test_handle_call_unknown_tool():
    req = {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}}
    }
    resp = time_mcp._handle_request(req)
    assert 'error' in resp
    assert resp['error']['code'] == -32601


def test_handle_call_convert_time_missing_args():
    req = {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "convert_time", "arguments": {"source_time": "2026-07-20T10:00:00"}}
    }
    resp = time_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'required' in data['error']


def test_handle_notification_initialized():
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = time_mcp._handle_request(req)
    assert resp is None  # notifications return None


def test_handle_unknown_method():
    req = {"jsonrpc": "2.0", "id": 8, "method": "unknown/method"}
    resp = time_mcp._handle_request(req)
    assert 'error' in resp
    assert resp['error']['code'] == -32601


def test_handle_invalid_request_not_dict():
    resp = time_mcp._handle_request("not a dict")
    assert resp['error']['code'] == -32600


# ---------- V3 contract: aware datetime handling ----------

def test_v3_aware_input_offset_not_overwritten():
    """V3: Input with +08:00 must not be overwritten by replace(tzinfo=...).

    '2026-07-20T10:00:00+08:00' with source_timezone='UTC' must preserve
    the time point (10:00+08:00 = 02:00 UTC = 10:00 Shanghai). The source
    field is represented in source_timezone (UTC → 02:00+00:00), but the
    time point itself is not shifted. The target in Shanghai should be
    10:00+08:00, proving no information was lost.
    """
    result = time_mcp.convert_time(
        '2026-07-20T10:00:00+08:00', 'UTC', 'Asia/Shanghai'
    )
    # Source is represented in source_timezone (UTC): 10:00+08:00 → 02:00+00:00
    assert result['source']['utc_offset'] == '+00:00'
    assert 'T02:00:00' in result['source']['datetime']
    # Target in Shanghai: 02:00 UTC → 10:00+08:00 (time point preserved)
    assert 'T10:00:00+08:00' in result['target']['datetime']


def test_v3_aware_input_z_suffix():
    """V3: 'Z' suffix is treated as UTC offset, not naive."""
    result = time_mcp.convert_time(
        '2026-07-20T10:00:00Z', 'Asia/Shanghai', 'Asia/Shanghai'
    )
    # Z = UTC, so source is 10:00 UTC = 18:00 Shanghai
    assert 'T18:00:00+08:00' in result['target']['datetime']


def test_v3_naive_input_uses_source_timezone():
    """V3: Naive input is interpreted in source_timezone (existing behavior)."""
    result = time_mcp.convert_time(
        '2026-07-20T10:00:00', 'Asia/Shanghai', 'UTC'
    )
    # 10:00 Shanghai = 02:00 UTC
    assert 'T02:00:00+00:00' in result['target']['datetime']


def test_v3_aware_input_cross_timezone():
    """V3: Aware input converts correctly across timezones."""
    result = time_mcp.convert_time(
        '2026-07-20T10:00:00+08:00', 'UTC', 'America/New_York'
    )
    # 10:00+08:00 = 02:00 UTC = 22:00-04:00 (EDT, July) previous day in NY
    tgt = result['target']['datetime']
    assert '-04:00' in tgt or '-05:00' in tgt
    # Verify the conversion is correct: 10:00+08:00 → 02:00Z → 22:00-04:00
    assert 'T22:00:00' in tgt or 'T21:00:00' in tgt


def test_v3_dst_gap_emits_warning():
    """V3: A naive time in the DST gap (spring forward) emits a warning.

    In America/New_York, 2026-03-08 02:30 is in the DST gap
    (clocks jump 02:00→03:00). Python shifts it, and we warn.
    """
    import warnings
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        time_mcp.convert_time(
            '2026-03-08T02:30:00', 'America/New_York', 'UTC'
        )
    # At least one UserWarning about DST gap
    gap_warnings = [w for w in caught if 'DST gap' in str(w.message)]
    assert len(gap_warnings) >= 1


def test_v3_invalid_iana_timezone_rejected():
    """V3: Invalid IANA timezone raises ValueError."""
    with pytest.raises(ValueError, match='Unknown timezone'):
        time_mcp.convert_time(
            '2026-07-20T10:00:00', 'NotAReal/Zone', 'UTC'
        )
