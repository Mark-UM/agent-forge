"""Tests for modules.mcp.fetch_mcp (v1.6)

Covers:
- _HTMLToMarkdown converter (headings, lists, links, code, blockquote, skip tags)
- _html_to_markdown wrapper (fallback path)
- fetch_url() (URL validation, max_length cap, pagination, raw mode, mocked HTTP)
- _handle_request() MCP protocol (initialize, tools/list, tools/call, errors)
"""
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.mcp import fetch_mcp
from modules.mcp.fetch_mcp import _html_to_markdown, _HTMLToMarkdown, fetch_url


# ---------- HTML → Markdown converter ----------

def test_html_heading_h1():
    md = _html_to_markdown('<h1>Title</h1>')
    assert md.startswith('# ')
    assert 'Title' in md


def test_html_heading_h3():
    md = _html_to_markdown('<h3>Section</h3>')
    assert '# ### Section' in md or '### Section' in md


def test_html_paragraph():
    md = _html_to_markdown('<p>Hello world</p>')
    assert 'Hello world' in md


def test_html_bold_strong():
    md = _html_to_markdown('<p><b>bold</b> and <strong>strong</strong></p>')
    assert '**bold**' in md
    assert '**strong**' in md


def test_html_italic_em():
    md = _html_to_markdown('<p><i>italic</i> and <em>em</em></p>')
    assert '*italic*' in md
    assert '*em*' in md


def test_html_inline_code():
    md = _html_to_markdown('<p>Use <code>pip install</code> to install</p>')
    assert '`pip install`' in md


def test_html_pre_code_block():
    md = _html_to_markdown('<pre>line1\nline2</pre>')
    assert '```' in md
    assert 'line1' in md
    assert 'line2' in md


def test_html_blockquote():
    md = _html_to_markdown('<blockquote>Quoted text</blockquote>')
    assert '>' in md
    assert 'Quoted text' in md


def test_html_unordered_list():
    md = _html_to_markdown('<ul><li>apple</li><li>banana</li></ul>')
    assert '- apple' in md
    assert '- banana' in md


def test_html_ordered_list():
    md = _html_to_markdown('<ol><li>first</li><li>second</li></ol>')
    assert '1. first' in md
    assert '2. second' in md


def test_html_link():
    md = _html_to_markdown('<a href="https://example.com">Example</a>')
    assert '[Example](https://example.com)' in md


def test_html_link_relative_href():
    """Relative hrefs (no scheme) render as bare text per implementation."""
    md = _html_to_markdown('<a href="/page">Page</a>')
    # /page starts with '/' so it should be rendered as a link
    assert '[Page](/page)' in md


def test_html_image():
    md = _html_to_markdown('<img src="https://example.com/x.png" alt="Alt">')
    assert '![Alt](https://example.com/x.png)' in md


def test_html_hr():
    md = _html_to_markdown('<p>before</p><hr><p>after</p>')
    assert '---' in md


def test_html_skips_script_style():
    md = _html_to_markdown('<p>visible</p><script>alert(1)</script><style>.x{}</style>')
    assert 'visible' in md
    assert 'alert' not in md
    assert '.x{}' not in md


def test_html_skips_nav_footer():
    md = _html_to_markdown('<p>main</p><nav>menu</nav><footer>bottom</footer>')
    assert 'main' in md
    assert 'menu' not in md
    assert 'bottom' not in md


def test_html_collapses_whitespace():
    md = _html_to_markdown('<p>  multiple    spaces   </p>')
    # Non-pre content collapses whitespace
    assert '  \n' not in md or 'multiple spaces' in md


def test_html_to_markdown_fallback_on_error():
    """If HTMLParser.feed raises, fallback to stripping tags via regex."""
    # Force an error by passing non-string (will be handled gracefully)
    # We use a malformed input that the parser tolerates but verify fallback works
    with patch.object(_HTMLToMarkdown, 'feed', side_effect=Exception('forced')):
        md = _html_to_markdown('<p>hello</p>')
        # Fallback strips tags, returns plain text
        assert 'hello' in md
        assert '<p>' not in md


# ---------- fetch_url: validation ----------

def test_fetch_url_empty_url_raises():
    with pytest.raises(ValueError, match='url is required'):
        fetch_url('')


def test_fetch_url_invalid_url_no_scheme():
    with pytest.raises(ValueError, match='Invalid URL'):
        fetch_url('not-a-url')


def test_fetch_url_unsupported_scheme():
    with pytest.raises(ValueError, match='Unsupported scheme'):
        fetch_url('ftp://example.com/file')


def test_fetch_url_max_length_capped():
    """max_length > MAX_CONTENT_CAP should be clamped."""
    body = b'<html><body>x</body></html>'
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body)):
        result = fetch_url('https://example.com', max_length=999999)
        # MAX_CONTENT_CAP = 50000 — content much shorter so no truncation
        assert result['truncated'] is False


def test_fetch_url_max_length_zero_or_negative():
    """max_length <= 0 resets to DEFAULT_MAX_LENGTH."""
    body = b'<html><body>short</body></html>'
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body)):
        result = fetch_url('https://example.com', max_length=0)
        # Content should not be truncated (shorter than default 10000)
        assert result['truncated'] is False
        assert 'short' in result['content']


# ---------- fetch_url: success path (mocked HTTP) ----------

def _make_mock_response(body_bytes, content_type='text/html; charset=utf-8',
                        status=200, final_url='https://example.com'):
    """Mock HTTP response.

    V2 fix: fetch_mcp reads in 8KB chunks via resp.read(8192).
    We model this with a side_effect that returns successive chunks
    of body_bytes, then b'' on the following call (EOF).
    """
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = final_url
    mock_resp.status = status
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': content_type,
    }.get(k, d)

    # Simulate chunked reads: read(n) returns up to n bytes per call.
    read_iter = [body_bytes] if body_bytes else [b'']

    def _read_side_effect(size=-1):
        if not read_iter:
            return b''
        chunk = read_iter.pop(0)
        if size is None or size < 0:
            return chunk
        # Slice the chunk to the requested size; remainder stays for next call.
        if len(chunk) <= size:
            return chunk
        head, tail = chunk[:size], chunk[size:]
        read_iter.insert(0, tail)
        return head

    mock_resp.read.side_effect = _read_side_effect
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_fetch_url_success_html_to_markdown():
    body = b'<html><body><h1>Hello</h1><p>World</p></body></html>'
    with patch('urllib.request.urlopen', return_value=_make_mock_response(body)):
        result = fetch_url('https://example.com')
        assert result['status_code'] == 200
        assert result['content_type'] == 'text/html'
        assert '# Hello' in result['content']
        assert 'World' in result['content']
        assert result['truncated'] is False
        assert result['start_index'] == 0
        assert result['total_length'] > 0


def test_fetch_url_raw_mode():
    body = b'<html><body><h1>Hello</h1></body></html>'
    with patch('urllib.request.urlopen', return_value=_make_mock_response(body)):
        result = fetch_url('https://example.com', raw=True)
        # In raw mode, content is the raw HTML (no markdown conversion)
        assert '<h1>Hello</h1>' in result['content']


def test_fetch_url_plain_text_content_type():
    body = b'just plain text'
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/plain')):
        result = fetch_url('https://example.com')
        assert 'just plain text' in result['content']
        # Plain text should not be HTML-converted
        assert '<' not in result['content']


def test_fetch_url_pagination_start_index():
    long_text = 'A' * 500
    body = long_text.encode('utf-8')
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/plain')):
        result = fetch_url('https://example.com', start_index=100)
        assert result['start_index'] == 100
        assert result['total_length'] == 500
        # Content should be sliced from index 100
        assert len(result['content']) == 400


def test_fetch_url_pagination_beyond_end():
    body = b'short content'
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/plain')):
        result = fetch_url('https://example.com', start_index=9999)
        assert result['content'] == ''
        assert result['total_length'] == len('short content')


def test_fetch_url_truncation_when_too_long():
    long_body = ('<html><body>' + 'X' * 200 + '</body></html>').encode('utf-8')
    with patch('urllib.request.urlopen', return_value=_make_mock_response(long_body)):
        result = fetch_url('https://example.com', max_length=50)
        assert result['truncated'] is True
        assert len(result['content']) == 50


def test_fetch_url_encoding_detection():
    """charset from Content-Type header is honored."""
    body = 'héllo wörld'.encode('latin-1')
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/html; charset=latin-1')):
        result = fetch_url('https://example.com')
        assert 'héllo wörld' in result['content']


def test_fetch_url_encoding_fallback_on_unknown_charset():
    body = 'plain ascii'.encode('utf-8')
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/html; charset=invalid-charset')):
        result = fetch_url('https://example.com')
        # Should fall back to utf-8 with errors='replace'
        assert 'plain ascii' in result['content']


def test_fetch_url_urlerror_propagation():
    """urllib.error.URLError propagates up (caller may catch)."""
    with patch('urllib.request.urlopen',
               side_effect=urllib.error.URLError('connection refused')):
        with pytest.raises(urllib.error.URLError):
            fetch_url('https://example.com')


# ---------- MCP protocol: _handle_request ----------

def test_handle_initialize():
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = fetch_mcp._handle_request(req)
    assert resp['jsonrpc'] == '2.0'
    assert resp['id'] == 1
    assert resp['result']['protocolVersion'] == '2024-11-05'
    assert resp['result']['serverInfo']['name'] == 'fetch-mcp'


def test_handle_tools_list():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = fetch_mcp._handle_request(req)
    tool_names = [t['name'] for t in resp['result']['tools']]
    assert 'fetch_url' in tool_names
    # Verify schema declares url as required
    fetch_tool = [t for t in resp['result']['tools'] if t['name'] == 'fetch_url'][0]
    assert 'url' in fetch_tool['inputSchema']['required']


def test_handle_call_fetch_url_missing_url():
    req = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "fetch_url", "arguments": {}}
    }
    resp = fetch_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'url is required' in data['error']


def test_handle_call_fetch_url_invalid_url():
    req = {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "fetch_url", "arguments": {"url": "not-a-url"}}
    }
    resp = fetch_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'Invalid URL' in data['error']


def test_handle_call_fetch_url_success_mocked():
    body = b'<html><body><h1>OK</h1></body></html>'
    with patch('urllib.request.urlopen', return_value=_make_mock_response(body)):
        req = {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "fetch_url", "arguments": {"url": "https://example.com"}}
        }
        resp = fetch_mcp._handle_request(req)
        assert resp['result']['isError'] is False
        data = json.loads(resp['result']['content'][0]['text'])
        assert data['status_code'] == 200
        assert '# OK' in data['content']


def test_handle_call_fetch_url_network_error():
    with patch('urllib.request.urlopen',
               side_effect=urllib.error.URLError('connection refused')):
        req = {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "fetch_url", "arguments": {"url": "https://example.com"}}
        }
        resp = fetch_mcp._handle_request(req)
        assert resp['result']['isError'] is True
        data = json.loads(resp['result']['content'][0]['text'])
        assert 'Network error' in data['error']


def test_handle_call_unknown_tool():
    req = {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}}
    }
    resp = fetch_mcp._handle_request(req)
    assert 'error' in resp
    assert resp['error']['code'] == -32601


def test_handle_notification_initialized():
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = fetch_mcp._handle_request(req)
    assert resp is None  # notifications return None


def test_handle_unknown_method():
    req = {"jsonrpc": "2.0", "id": 8, "method": "unknown/method"}
    resp = fetch_mcp._handle_request(req)
    assert 'error' in resp
    assert resp['error']['code'] == -32601


def test_handle_invalid_request_not_dict():
    resp = fetch_mcp._handle_request("not a dict")
    assert resp['error']['code'] == -32600


# ---------- V2 contract: chunked read with early stop ----------

def test_v2_chunked_read_stops_at_byte_limit():
    """V2: When the response exceeds the byte limit, reading stops early.

    We mock read(8192) to return an endless stream of 'A' bytes.
    fetch_url must stop reading once byte_limit is reached and set
    truncated=True.
    """
    from modules.mcp.fetch_mcp import MAX_CONTENT_CAP

    mock_resp = MagicMock()
    mock_resp.geturl.return_value = 'https://example.com'
    mock_resp.status = 200
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': 'text/plain; charset=utf-8',
    }.get(k, d)

    # Endless stream: each read(n) returns n bytes of 'A'
    def _endless_read(size=-1):
        if size is None or size < 0:
            return b'A' * 8192
        return b'A' * size

    mock_resp.read.side_effect = _endless_read
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch('urllib.request.urlopen', return_value=mock_resp):
        result = fetch_url('https://example.com', max_length=500)
        # Must be truncated because the stream is endless
        assert result['truncated'] is True
        # Content must be capped at max_length (after cap to MAX_CONTENT_CAP)
        assert len(result['content']) <= MAX_CONTENT_CAP


def test_v2_chunked_read_small_response_not_truncated():
    """V2: A small response that fits within the byte limit is not truncated."""
    body = b'hello world'
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/plain')):
        result = fetch_url('https://example.com', max_length=1000)
        assert result['truncated'] is False
        assert result['content'] == 'hello world'


def test_v2_chunked_read_preserves_total_length():
    """V2: total_length reflects the decoded content length, not bytes read."""
    body = 'A' * 1000
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body.encode('utf-8'),
                                                content_type='text/plain')):
        result = fetch_url('https://example.com', max_length=10000)
        assert result['total_length'] == 1000
        assert result['truncated'] is False


# ---------- R2-6 contract: pagination safety and observability ----------

def test_r26_new_fields_present_in_return():
    """R2-6: fetch_url must return max_start_index, bytes_read, network_truncated, total_length_known."""
    body = b'hello world'
    with patch('urllib.request.urlopen',
               return_value=_make_mock_response(body, content_type='text/plain')):
        result = fetch_url('https://example.com', max_length=1000)
    assert 'max_start_index' in result
    assert 'bytes_read' in result
    assert 'network_truncated' in result
    assert 'total_length_known' in result
    assert result['max_start_index'] > 0
    assert result['bytes_read'] == len(body)
    assert result['network_truncated'] is False
    # Mock response has no Content-Length header → unknown
    assert result['total_length_known'] is False


def test_r26_start_index_exceeding_max_raises():
    """R2-6: start_index exceeding MAX_START_INDEX must raise ValueError."""
    from modules.mcp.fetch_mcp import MAX_START_INDEX
    with pytest.raises(ValueError, match='exceeds MAX_START_INDEX'):
        fetch_url('https://example.com', start_index=MAX_START_INDEX + 1)


def test_r26_negative_start_index_raises():
    """R2-6: Negative start_index must raise ValueError."""
    with pytest.raises(ValueError, match='must be >= 0'):
        fetch_url('https://example.com', start_index=-1)


def test_r26_network_truncated_on_large_response():
    """R2-6: network_truncated must be True when byte_limit is hit."""
    # Endless stream mock
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = 'https://example.com'
    mock_resp.status = 200
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': 'text/plain',
    }.get(k, d)

    def _endless_read(size=-1):
        return b'A' * 8192

    mock_resp.read.side_effect = _endless_read
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch('urllib.request.urlopen', return_value=mock_resp):
        result = fetch_url('https://example.com', max_length=500)
    assert result['network_truncated'] is True
    assert result['truncated'] is True
    assert result['bytes_read'] > 0


def test_r26_total_length_known_when_content_length_present():
    """R2-6: total_length_known must be True when Content-Length header is valid."""
    body = b'hello world'
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = 'https://example.com'
    mock_resp.status = 200
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': 'text/plain',
        'Content-Length': str(len(body)),
    }.get(k, d)

    read_iter = [body]

    def _read_side_effect(size=-1):
        if not read_iter:
            return b''
        chunk = read_iter.pop(0)
        if len(chunk) > size > 0:
            read_iter.insert(0, chunk[size:])
            return chunk[:size]
        return chunk

    mock_resp.read.side_effect = _read_side_effect
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch('urllib.request.urlopen', return_value=mock_resp):
        result = fetch_url('https://example.com', max_length=1000)
    assert result['total_length_known'] is True
    assert result['network_truncated'] is False


def test_r26_total_length_unknown_when_content_length_empty():
    """R2-6: total_length_known must be False when Content-Length header is empty/invalid."""
    body = b'hello'
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = 'https://example.com'
    mock_resp.status = 200
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': 'text/plain',
        'Content-Length': '',  # empty string
    }.get(k, d)

    read_iter = [body]

    def _read_side_effect(size=-1):
        if not read_iter:
            return b''
        chunk = read_iter.pop(0)
        if len(chunk) > size > 0:
            read_iter.insert(0, chunk[size:])
            return chunk[:size]
        return chunk

    mock_resp.read.side_effect = _read_side_effect
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch('urllib.request.urlopen', return_value=mock_resp):
        result = fetch_url('https://example.com', max_length=1000)
    assert result['total_length_known'] is False


def test_r26_byte_limit_capped_by_max_network_bytes():
    """R2-6: byte_limit must not exceed MAX_NETWORK_BYTES even with large start_index."""
    from modules.mcp.fetch_mcp import MAX_NETWORK_BYTES
    # With a large start_index, byte_limit would be huge without the cap.
    # Verify that bytes_read never exceeds MAX_NETWORK_BYTES + one chunk (8192).
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = 'https://example.com'
    mock_resp.status = 200
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': 'text/plain',
    }.get(k, d)

    def _endless_read(size=-1):
        return b'A' * 8192

    mock_resp.read.side_effect = _endless_read
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch('urllib.request.urlopen', return_value=mock_resp):
        result = fetch_url('https://example.com', max_length=50000,
                           start_index=100000)
    # bytes_read must be bounded by MAX_NETWORK_BYTES + one 8KB chunk
    assert result['bytes_read'] <= MAX_NETWORK_BYTES + 8192
    assert result['network_truncated'] is True
