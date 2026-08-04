#!/usr/bin/env python3
"""Fetch MCP Server — Web content fetching and HTML-to-markdown conversion.

Self-hosted replacement for the archived `@modelcontextprotocol/server-fetch` npm package
(removed from npm registry in 2025, see servers-archived repo).

Design principles:
- Zero external dependencies (Python stdlib only: urllib + html.parser + re)
- Single-file standalone module
- Core function `fetch_url()` usable as a library
- HTML → Markdown conversion (lightweight, no external libs)
- MCP server: JSON-RPC 2.0 over stdio (newline-delimited)
- Robust error handling: network errors, timeouts, encoding issues, non-HTML content

Protocol: MCP uses newline-delimited JSON-RPC 2.0 over stdin/stdout.

Usage as MCP server:
    python -m modules.mcp.fetch_mcp serve

Usage as library:
    from modules.mcp.fetch_mcp import fetch_url
    result = fetch_url("https://example.com", max_length=5000)

CLI direct call (no MCP):
    python -m modules.mcp.fetch_mcp fetch --url https://example.com
    python -m modules.mcp.fetch_mcp fetch --url https://example.com --max-length 10000 --json

Tools exposed:
    fetch_url(url, max_length=10000, start_index=0, raw=False)
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── Constants ────────────────────────────────────────────────
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'fetch-mcp'
SERVER_VERSION = '1.0.0'

DEFAULT_MAX_LENGTH = 10000  # Default max content length (chars)
MAX_CONTENT_CAP = 50000     # Hard cap to prevent memory exhaustion
DEFAULT_TIMEOUT = 30        # HTTP timeout (seconds)
DEFAULT_USER_AGENT = 'Mozilla/5.0 (compatible; fetch-mcp/1.0; +https://opencode.ai)'

# R2-6: start_index must not infinitely expand the read limit.
# MAX_START_INDEX caps pagination depth; MAX_NETWORK_BYTES caps total bytes
# read from the network regardless of start_index + max_length.
MAX_START_INDEX = 500000       # 500K chars max pagination depth
MAX_NETWORK_BYTES = 500000     # 500KB hard cap on network read

# Content types we process as HTML
HTML_CONTENT_TYPES = (
    'text/html',
    'application/xhtml+xml',
    'text/plain',  # treat plain text as raw content
)


# ── HTML to Markdown converter ───────────────────────────────
class _HTMLToMarkdown(HTMLParser):
    """Lightweight HTML-to-Markdown converter (stdlib only).

    Handles common HTML elements: headings, paragraphs, lists, links,
    code blocks, blockquotes, bold, italic, etc.
    """

    def __init__(self):
        super().__init__()
        self.output = []
        self._list_depth = 0
        self._list_type = []  # stack of 'ul' or 'ol'
        self._list_counter = []  # for ordered lists
        self._in_pre = False
        self._in_code = False
        self._in_anchor = False
        self._anchor_href = ''
        self._anchor_text = ''
        self._skip_tags = ('script', 'style', 'head', 'nav', 'footer', 'svg')
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return

        attrs_dict = dict(attrs)

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            level = int(tag[1])
            self.output.append('\n\n' + '#' * level + ' ')
        elif tag == 'p':
            self.output.append('\n\n')
        elif tag == 'br':
            self.output.append('  \n')
        elif tag == 'hr':
            self.output.append('\n\n---\n\n')
        elif tag == 'b' or tag == 'strong':
            self.output.append('**')
        elif tag == 'i' or tag == 'em':
            self.output.append('*')
        elif tag == 'code' and not self._in_pre:
            self._in_code = True
            self.output.append('`')
        elif tag == 'pre':
            self._in_pre = True
            self.output.append('\n\n```\n')
        elif tag == 'blockquote':
            self.output.append('\n\n> ')
        elif tag == 'ul':
            self._list_depth += 1
            self._list_type.append('ul')
            self.output.append('\n')
        elif tag == 'ol':
            self._list_depth += 1
            self._list_type.append('ol')
            self._list_counter.append(0)
            self.output.append('\n')
        elif tag == 'li':
            if self._list_type and self._list_type[-1] == 'ol':
                self._list_counter[-1] += 1
                indent = '  ' * (self._list_depth - 1)
                self.output.append(f'{indent}{self._list_counter[-1]}. ')
            else:
                indent = '  ' * (self._list_depth - 1)
                self.output.append(f'{indent}- ')
        elif tag == 'a':
            self._in_anchor = True
            self._anchor_href = attrs_dict.get('href', '')
            self._anchor_text = ''
        elif tag == 'img':
            alt = attrs_dict.get('alt', '')
            src = attrs_dict.get('src', '')
            if src:
                self.output.append(f'![{alt}]({src})')
        elif tag in ('table',):
            self.output.append('\n\n')

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p'):
            self.output.append('\n')
        elif tag == 'b' or tag == 'strong':
            self.output.append('**')
        elif tag == 'i' or tag == 'em':
            self.output.append('*')
        elif tag == 'code' and not self._in_pre:
            self._in_code = False
            self.output.append('`')
        elif tag == 'pre':
            self._in_pre = False
            self.output.append('\n```\n\n')
        elif tag == 'blockquote':
            self.output.append('\n')
        elif tag in ('ul', 'ol'):
            if self._list_type:
                self._list_type.pop()
            if tag == 'ol' and self._list_counter:
                self._list_counter.pop()
            self._list_depth = max(0, self._list_depth - 1)
            self.output.append('\n')
        elif tag == 'li':
            self.output.append('\n')
        elif tag == 'a' and self._in_anchor:
            self._in_anchor = False
            text = self._anchor_text.strip()
            href = self._anchor_href
            if href and text:
                if href.startswith(('http://', 'https://', '/', '#', 'mailto:')):
                    self.output.append(f'[{text}]({href})')
                else:
                    self.output.append(text)
            elif text:
                self.output.append(text)
            self._anchor_href = ''
            self._anchor_text = ''
        elif tag in ('table', 'tr', 'td', 'th'):
            self.output.append(' | ')

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self._in_anchor:
            self._anchor_text += data
        elif self._in_pre:
            self.output.append(data)
        else:
            # Collapse whitespace for non-pre content
            import re
            collapsed = re.sub(r'\s+', ' ', data)
            self.output.append(collapsed)

    def get_markdown(self) -> str:
        """Return the converted markdown text."""
        text = ''.join(self.output)
        # Clean up excessive blank lines
        import re
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown.

    Args:
        html: HTML content string

    Returns:
        str: Markdown content
    """
    parser = _HTMLToMarkdown()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # If parser fails, fall back to stripping tags
        import re
        text = re.sub(r'<[^>]+>', '', html)
        return text.strip()
    return parser.get_markdown()


# ── Core function ────────────────────────────────────────────
def fetch_url(url: str, max_length: int = DEFAULT_MAX_LENGTH,
              start_index: int = 0, raw: bool = False, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch a URL and return its content (optionally converted to markdown).

    Args:
        url: URL to fetch
        max_length: Max content length to return (chars). Capped at MAX_CONTENT_CAP.
        start_index: Start index for content slice (for pagination)
        raw: If True, return raw HTML/text instead of markdown
        timeout: HTTP timeout in seconds

    Returns:
        dict: {
            'url': str (final URL after redirects),
            'content_type': str,
            'status_code': int,
            'content': str (markdown or raw text),
            'truncated': bool,
            'start_index': int,
            'total_length': int,
            'max_start_index': int,
            'bytes_read': int,
            'network_truncated': bool,
            'total_length_known': bool,
        }

    Raises:
        ValueError: if URL is invalid or start_index exceeds MAX_START_INDEX
        urllib.error.URLError: if fetch fails
    """
    if not url:
        raise ValueError("url is required")

    # Validate URL
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")

    # R2-6: start_index must not exceed MAX_START_INDEX
    if start_index < 0:
        raise ValueError(f"start_index must be >= 0, got {start_index}")
    if start_index > MAX_START_INDEX:
        raise ValueError(
            f"start_index {start_index} exceeds MAX_START_INDEX {MAX_START_INDEX}"
        )

    # Cap max_length
    max_length = min(max_length, MAX_CONTENT_CAP)
    if max_length <= 0:
        max_length = DEFAULT_MAX_LENGTH

    # Build request
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': DEFAULT_USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
            'Accept-Encoding': 'identity',  # Avoid gzip/brotli (stdlib can't decode)
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        status_code = resp.status
        content_type = resp.headers.get('Content-Type', '').split(';')[0].strip().lower()

        # R2-6: Read Content-Length header to determine if total length is known.
        content_length_header = resp.headers.get('Content-Length')
        # Header may exist but be empty or non-numeric — guard against int('').
        if content_length_header and content_length_header.strip().isdigit():
            total_length_known = True
            declared_total_bytes = int(content_length_header)
        else:
            total_length_known = False
            declared_total_bytes = None

        # V2 fix: Read content in chunks with early stop
        # Instead of reading the entire response (resp.read()), we read in
        # chunks and stop when we have enough bytes to produce max_length chars.
        # This prevents memory exhaustion on very large responses.
        #
        # R2-6: byte_limit is capped by MAX_NETWORK_BYTES so that a large
        # start_index does not infinitely expand the read limit.
        # We read more than strictly needed to handle multi-byte chars correctly.
        byte_limit = (max_length + start_index) * 4 + 8192  # 4 bytes/char + buffer
        byte_limit = min(byte_limit, MAX_NETWORK_BYTES)  # R2-6: hard cap

        # Detect encoding from Content-Type header
        encoding = 'utf-8'
        content_type_full = resp.headers.get('Content-Type', '')
        if 'charset=' in content_type_full:
            encoding = content_type_full.split('charset=')[-1].split(';')[0].strip()

        # V2: Chunked read with early stop
        chunks = []
        total_bytes_read = 0
        network_truncated = False

        while True:
            chunk = resp.read(8192)  # Read 8KB at a time
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes_read += len(chunk)
            if total_bytes_read >= byte_limit:
                network_truncated = True
                break

        raw_bytes = b''.join(chunks)

        try:
            html_text = raw_bytes.decode(encoding, errors='replace')
        except (LookupError, UnicodeDecodeError):
            html_text = raw_bytes.decode('utf-8', errors='replace')

        # Convert to markdown if HTML and not raw mode
        if raw:
            content = html_text
        elif content_type in ('text/html', 'application/xhtml+xml'):
            content = _html_to_markdown(html_text)
        else:
            # Plain text or other: use as-is
            content = html_text

        total_length = len(content)
        # Apply pagination
        if start_index > 0 and start_index < total_length:
            content = content[start_index:]
        elif start_index >= total_length:
            content = ''

        # V2: truncated if either the network read was cut short OR
        # the decoded content exceeds max_length
        truncated = network_truncated or len(content) > max_length
        if len(content) > max_length:
            content = content[:max_length]

        return {
            'url': final_url,
            'content_type': content_type,
            'status_code': status_code,
            'content': content,
            'truncated': truncated,
            'start_index': start_index,
            'total_length': total_length,
            # R2-6: New fields for pagination safety and observability
            'max_start_index': MAX_START_INDEX,
            'bytes_read': total_bytes_read,
            'network_truncated': network_truncated,
            'total_length_known': total_length_known,
        }


# ── MCP protocol ─────────────────────────────────────────────
def _make_tool_list():
    return {
        'tools': [
            {
                'name': 'fetch_url',
                'description': (
                    'Fetch a URL and return its content as markdown (or raw text). '
                    'Handles HTML-to-markdown conversion, encoding detection, '
                    'and pagination via start_index. Useful for reading web pages, '
                    'documentation, and API responses.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'url': {
                            'type': 'string',
                            'description': 'URL to fetch (http:// or https://)',
                        },
                        'max_length': {
                            'type': 'integer',
                            'description': f'Max content length in chars (default {DEFAULT_MAX_LENGTH}, max {MAX_CONTENT_CAP})',
                            'default': DEFAULT_MAX_LENGTH,
                        },
                        'start_index': {
                            'type': 'integer',
                            'description': 'Start index for content slice (for pagination, default 0)',
                            'default': 0,
                        },
                        'raw': {
                            'type': 'boolean',
                            'description': 'Return raw content instead of markdown (default false)',
                            'default': False,
                        },
                    },
                    'required': ['url'],
                },
            },
        ]
    }


def _handle_request(request):
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

    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            },
        }

    if method == 'notifications/initialized':
        return None

    if method == 'tools/list':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': _make_tool_list(),
        }

    if method == 'tools/call':
        tool_name = params.get('name')
        args = params.get('arguments', {}) or {}

        if tool_name != 'fetch_url':
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'},
            }

        url = args.get('url', '')
        if not url:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({'success': False, 'error': 'url is required'},
                                           ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }

        max_length = args.get('max_length', DEFAULT_MAX_LENGTH)
        start_index = args.get('start_index', 0)
        raw = args.get('raw', False)

        try:
            result = fetch_url(url, max_length=max_length,
                               start_index=start_index, raw=raw)
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
        except urllib.error.URLError as e:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({
                            'success': False,
                            'error': f'Network error: {e.reason}',
                        }, ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }
        except Exception as e:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({
                            'success': False,
                            'error': f'Unexpected error: {type(e).__name__}: {e}',
                        }, ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }

    if is_notification:
        return None
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'error': {'code': -32601, 'message': f'Method not found: {method}'},
    }


def _run_server():
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
    parser = argparse.ArgumentParser(
        description='Fetch MCP Server — Web content fetching and HTML-to-markdown conversion'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_fetch = sub.add_parser('fetch', help='Fetch a URL')
    p_fetch.add_argument('--url', required=True, help='URL to fetch')
    p_fetch.add_argument('--max-length', type=int, default=DEFAULT_MAX_LENGTH,
                         help=f'Max content length (default {DEFAULT_MAX_LENGTH})')
    p_fetch.add_argument('--start-index', type=int, default=0,
                         help='Start index for content slice (default 0)')
    p_fetch.add_argument('--raw', action='store_true',
                         help='Return raw content instead of markdown')
    p_fetch.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                         help=f'HTTP timeout (default {DEFAULT_TIMEOUT})')
    p_fetch.add_argument('--json', action='store_true', help='JSON output')

    sub.add_parser('serve', help='Run as MCP server (JSON-RPC over stdio)')

    args = parser.parse_args()

    if args.command == 'fetch':
        try:
            result = fetch_url(args.url, max_length=args.max_length,
                               start_index=args.start_index, raw=args.raw,
                               timeout=args.timeout)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"URL: {result['url']}")
                print(f"Status: {result['status_code']}")
                print(f"Content-Type: {result['content_type']}")
                print(f"Total length: {result['total_length']}")
                if result['truncated']:
                    print(f"[Truncated to {args.max_length} chars]")
                print("---")
                print(result['content'])
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'serve':
        _run_server()


if __name__ == '__main__':
    if __package__ is None and __name__ == '__main__':
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _cli()
