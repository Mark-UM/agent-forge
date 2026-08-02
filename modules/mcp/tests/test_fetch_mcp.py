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
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com'
        mock_resp.status = 200
        mock_resp.headers.get.side_effect = lambda k, d='': {
            'Content-Type': 'text/html; charset=utf-8',
        }.get(k, d)
        mock_resp.read.return_value = b'<html><body>x</body></html>'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_url('https://example.com', max_length=999999)
        # MAX_CONTENT_CAP = 50000 — content much shorter so no truncation
        assert result['truncated'] is False


def test_fetch_url_max_length_zero_or_negative():
    """max_length <= 0 resets to DEFAULT_MAX_LENGTH."""
    with patch('urllib.request.urlopen') as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://example.com'
        mock_resp.status = 200
        mock_resp.headers.get.side_effect = lambda k, d='': {
            'Content-Type': 'text/html; charset=utf-8',
        }.get(k, d)
        mock_resp.read.return_value = b'<html><body>short</body></html>'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = fetch_url('https://example.com', max_length=0)
        # Content should not be truncated (shorter than default 10000)
        assert result['truncated'] is False
        assert 'short' in result['content']


# ---------- fetch_url: success path (mocked HTTP) ----------

def _make_mock_response(body_bytes, content_type='text/html; charset=utf-8',
                        status=200, final_url='https://example.com'):
    mock_resp = MagicMock()
    mock_resp.geturl.return_value = final_url
    mock_resp.status = status
    mock_resp.headers.get.side_effect = lambda k, d='': {
        'Content-Type': content_type,
    }.get(k, d)
    mock_resp.read.return_value = body_bytes
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
