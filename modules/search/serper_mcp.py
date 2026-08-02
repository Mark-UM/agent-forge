#!/usr/bin/env python3
"""Serper API MCP Server — Google Search API official partner.

Replaces g-search MCP (Playwright-based, ToS-violating) with official Serper API
(https://serper.dev). 2500 free queries/month, official Google partner, < 1s latency.

设计原则：
- 单文件独立模块，零外部依赖（仅 Python 标准库）
- 核心函数 `serper_search()` 可作为库直接调用
- MCP server：实现 JSON-RPC 2.0 over stdio 协议（initialize / tools/list / tools/call）
- Layer 0 PII 脱敏：出境前对所有 query 做正则脱敏
- 错误兜底：API 失败、超时、JSON 解析错误均返回结构化错误，绝不崩溃

Protocol: MCP uses newline-delimited JSON-RPC 2.0 over stdin/stdout.

Usage as MCP server:
    python -m modules.search.serper_mcp serve

Usage as library:
    from modules.search.serper_mcp import serper_search
    result = serper_search("React useEffect", api_key="...")

CLI direct call (no MCP):
    python modules/search/serper_mcp.py search --query "React useEffect"
    python modules/search/serper_mcp.py search --query "..." --json

Tool exposed:
    serper_search(query: str, num: int = 10, gl: str = "us", hl: str = "en")
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 导入 privacy 模块做出境 PII 脱敏
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(query):
        return query, {'redacted_count': 0}


# ── Constants ────────────────────────────────────────────────
SERPER_API_URL = 'https://google.serper.dev/search'
DEFAULT_TIMEOUT = 30
DEFAULT_NUM = 10
MAX_NUM = 100  # Serper API cap
MAX_QUERY_LEN = 500  # Safety limit

# MCP protocol constants
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'serper-mcp'
SERVER_VERSION = '1.0.0'


# ── Serper API client ────────────────────────────────────────
def serper_search(query, api_key=None, num=DEFAULT_NUM, gl='us', hl='en',
                  timeout=DEFAULT_TIMEOUT, redact=True):
    """调用 Serper API 获取 Google 搜索结果。

    Args:
        query: 搜索查询字符串
        api_key: Serper API key（默认从 SERPER_API_KEY env 读取）
        num: 结果数量（1-100）
        gl: 国家代码（us, cn, my 等）
        hl: 语言代码（en, zh 等）
        timeout: HTTP 超时秒数
        redact: 是否对 query 做出境 PII 脱敏（默认 True）

    Returns:
        dict: {
            'success': bool,
            'query': str（脱敏后）,
            'results': list of {'title', 'url', 'snippet', 'source', 'position'},
            'count': int,
            'error': str（仅失败时）,
            'error_body': str（仅 HTTP 错误时，已脱敏 API key）,
            'search_parameters': dict（成功时附 Serper 返回的搜索参数）,
        }
    """
    # 校验 query
    if not isinstance(query, str) or not query.strip():
        return {
            'success': False,
            'query': '',
            'results': [],
            'count': 0,
            'error': 'Empty query',
        }

    # 截断 query 避免超长请求
    query = query[:MAX_QUERY_LEN]

    # 校验 num
    try:
        num = int(num)
        if num < 1:
            num = 1
        elif num > MAX_NUM:
            num = MAX_NUM
    except (ValueError, TypeError):
        num = DEFAULT_NUM

    # API key
    if api_key is None:
        api_key = os.environ.get('SERPER_API_KEY', '')
    if not api_key:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'error': 'SERPER_API_KEY not set',
        }

    # Layer 0 PII 脱敏（出境前）
    if redact:
        redacted_query, meta = _redact_outbound(query)
        redacted_count = meta.get('redacted_count', 0)
        if redacted_count > 0:
            sys.stderr.write(
                f"[serper] Layer 0 redacted {redacted_count} PII pattern(s); "
                f"types: {meta.get('patterns_matched', [])}\n"
            )
        query = redacted_query

    # 构建请求 payload
    payload = {
        'q': query,
        'num': num,
        'gl': gl,
        'hl': hl,
    }

    req = urllib.request.Request(
        SERPER_API_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'X-API-KEY': api_key,
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            if status < 200 or status >= 300:
                return {
                    'success': False,
                    'query': query,
                    'results': [],
                    'count': 0,
                    'error': f'HTTP {status}',
                }
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        # 读取错误 body 用于调试，但绝不能泄露 API key
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            pass
        # 在错误 body 中替换可能的 API key 泄露
        if api_key and api_key in body:
            body = body.replace(api_key, '[REDACTED-KEY]')
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'error': f'Serper API HTTP {e.code}: {e.reason}',
            'error_body': body,
        }
    except urllib.error.URLError as e:
        # urlopen(timeout=...) 通常抛 URLError(reason=socket.timeout)，
        # 在此统一映射为 timeout 错误信息
        reason = e.reason
        if isinstance(reason, TimeoutError) or 'timeout' in str(reason).lower():
            return {
                'success': False,
                'query': query,
                'results': [],
                'count': 0,
                'error': f'Timeout after {timeout}s',
            }
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'error': f'URL error: {reason}',
        }
    except TimeoutError:
        # 兜底：直接抛出 TimeoutError 的场景（如理论上的 socket.timeout 未包装）
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'error': f'Timeout after {timeout}s',
        }
    except json.JSONDecodeError as e:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'error': f'Response parse failed: {e}',
        }
    except Exception as e:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'error': f'{type(e).__name__}: {e}',
        }

    # 解析 Serper 响应 — organic 数组
    organic = data.get('organic', [])
    if not isinstance(organic, list):
        organic = []

    results = []
    for item in organic[:num]:
        if not isinstance(item, dict):
            continue
        results.append({
            'title': str(item.get('title', '')),
            'url': str(item.get('link', '')),
            'snippet': str(item.get('snippet', '')),
            'source': 'serper',
            'position': item.get('position'),
        })

    return {
        'success': True,
        'query': query,
        'results': results,
        'count': len(results),
        'search_parameters': data.get('searchParameters', {}),
    }


# ── MCP Server (JSON-RPC 2.0 over stdio) ─────────────────────
def _make_tool_list():
    """构建 tools/list 响应。"""
    return {
        'tools': [
            {
                'name': 'serper_search',
                'description': (
                    'Search Google via Serper API (official Google partner). '
                    'Returns organic search results with title, url, snippet. '
                    'PII in query is automatically redacted before sending.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'Search query string',
                        },
                        'num': {
                            'type': 'integer',
                            'description': f'Number of results (1-{MAX_NUM}, default {DEFAULT_NUM})',
                            'default': DEFAULT_NUM,
                        },
                        'gl': {
                            'type': 'string',
                            'description': 'Country code (us, cn, my, etc.)',
                            'default': 'us',
                        },
                        'hl': {
                            'type': 'string',
                            'description': 'Language code (en, zh, etc.)',
                            'default': 'en',
                        },
                    },
                    'required': ['query'],
                },
            },
        ]
    }


def _handle_request(request):
    """处理单个 JSON-RPC 请求。返回响应 dict；notifications 返回 None。"""
    if not isinstance(request, dict):
        return {
            'jsonrpc': '2.0',
            'id': None,
            'error': {'code': -32600, 'message': 'Invalid request: not a JSON object'},
        }

    method = request.get('method')
    req_id = request.get('id')
    params = request.get('params', {}) or {}

    # notification（无 id）→ 无响应
    is_notification = req_id is None

    # initialize
    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {
                    'tools': {},
                },
                'serverInfo': {
                    'name': SERVER_NAME,
                    'version': SERVER_VERSION,
                },
            },
        }

    # notifications/initialized — no response
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

        if tool_name != 'serper_search':
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'},
            }

        query = args.get('query', '')
        if not query:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({
                            'success': False,
                            'error': 'query is required',
                        }, ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }

        num = args.get('num', DEFAULT_NUM)
        gl = args.get('gl', 'us')
        hl = args.get('hl', 'en')

        result = serper_search(query, num=num, gl=gl, hl=hl)

        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'content': [{
                    'type': 'text',
                    'text': json.dumps(result, ensure_ascii=False),
                }],
                'isError': not result.get('success', False),
            },
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
    """运行 MCP server — JSON-RPC over stdio。"""
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

        # 支持批量请求（JSON-RPC 2.0 spec）
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
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='Serper API MCP Server — Google Search API official partner'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # search 子命令 — 直接调用 API（不启动 MCP server）
    p_search = sub.add_parser('search', help='Call Serper API directly')
    p_search.add_argument('--query', required=True, help='Search query')
    p_search.add_argument('--num', type=int, default=DEFAULT_NUM,
                          help=f'Number of results (default {DEFAULT_NUM})')
    p_search.add_argument('--gl', default='us', help='Country code (default us)')
    p_search.add_argument('--hl', default='en', help='Language code (default en)')
    p_search.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                          help=f'HTTP timeout seconds (default {DEFAULT_TIMEOUT})')
    p_search.add_argument('--no-redact', action='store_true',
                          help='Disable PII redaction (NOT recommended)')
    p_search.add_argument('--json', action='store_true',
                          help='JSON output')

    # serve 子命令 — 运行 MCP server（stdio JSON-RPC）
    sub.add_parser('serve', help='Run as MCP server (stdio JSON-RPC)')

    args = parser.parse_args()

    if args.command == 'search':
        result = serper_search(
            args.query,
            num=args.num,
            gl=args.gl,
            hl=args.hl,
            timeout=args.timeout,
            redact=not args.no_redact,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                print(f"Found {result['count']} results for: {result['query']}")
                for i, r in enumerate(result['results'], 1):
                    print(f"\n{i}. {r['title']}")
                    print(f"   URL: {r['url']}")
                    print(f"   Snippet: {r['snippet']}")
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
        return 0

    if args.command == 'serve':
        _run_server()
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
