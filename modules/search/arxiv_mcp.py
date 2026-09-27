#!/usr/bin/env python3
"""arXiv MCP Server — Academic preprint search.

arXiv API is free, no API key required. Returns Atom XML feed.

设计原则：
- 可独立启动的 CLI/MCP 入口；部署时需保留同目录的 privacy.py 与 outbound_guard.py（仅 Python 标准库）
- 核心函数 `arxiv_search()` 可作为库直接调用
- MCP server：JSON-RPC 2.0 over stdio
- Layer 0 PII 脱敏：出境前对所有 query 做正则脱敏
- 错误兜底：API 失败、超时、XML 解析错误均返回结构化错误，绝不崩溃

Protocol: MCP uses newline-delimited JSON-RPC 2.0 over stdin/stdout.

Usage as MCP server:
    python -m modules.search.arxiv_mcp serve

Usage as library:
    from modules.search.arxiv_mcp import arxiv_search
    result = arxiv_search("transformer attention", max_results=10)

CLI direct call (no MCP):
    python arxiv_mcp.py search --query "transformer attention"
    python arxiv_mcp.py search --query "..." --category cs.AI --max 20 --json

Tool exposed:
    arxiv_search(query, max_results=10, category=None, sort_by='submittedDate',
                 sort_order='descending')
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 导入 privacy 模块做出境 PII 脱敏
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    _redact_outbound = None

from outbound_guard import OutboundRedactionError, require_redacted_query


# ── Constants ────────────────────────────────────────────────
ARXIV_API_URL = 'https://export.arxiv.org/api/query'
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RESULTS = 10
MAX_RESULTS_CAP = 100  # arXiv API limit
MAX_QUERY_LEN = 500  # Safety limit

# Atom XML namespaces
_NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom',
    'opensearch': 'http://a9.com/-/spec/opensearch/1.1/',
}

# Sort options supported by arXiv API
SORT_OPTIONS = {
    'relevance': 'relevance',
    'lastUpdatedDate': 'lastUpdatedDate',
    'submittedDate': 'submittedDate',
}

SORT_ORDERS = {'ascending', 'descending'}

# MCP protocol constants
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'arxiv-mcp'
SERVER_VERSION = '1.0.0'


# ── arXiv API client ────────────────────────────────────────
def _build_arxiv_url(query, max_results=DEFAULT_MAX_RESULTS, category=None,
                     sort_by='submittedDate', sort_order='descending'):
    """构建 arXiv API URL。

    Args:
        query: 搜索查询
        max_results: 最大结果数
        category: arXiv 分类（如 cs.AI, cs.CL），可选
        sort_by: 排序字段
        sort_order: 排序方向

    Returns:
        str: 完整的 arXiv API URL

    Note:
        Caller (arxiv_search) is responsible for Layer 0 PII redaction
        before invoking this function. Redaction is not duplicated here
        to preserve the `redact=False` escape hatch (CLI --no-redact).
    """
    redacted_query = query.strip()

    # 构建搜索查询
    if category:
        # 分类 + 关键词：cat:cs.AI AND all:transformer
        search_query = f'cat:{category} AND all:{redacted_query}'
    else:
        search_query = f'all:{redacted_query}'

    params = {
        'search_query': search_query,
        'start': 0,
        'max_results': max_results,
        'sortBy': sort_by,
        'sortOrder': sort_order,
    }

    return f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"


def _parse_arxiv_entry(entry):
    """解析单个 Atom entry 为 dict。

    Args:
        entry: ElementTree Element

    Returns:
        dict: {
            'title': str,
            'authors': [str],
            'abstract': str,
            'url': str,
            'published': str,
            'updated': str,
            'categories': [str],
            'primary_category': str or None,
            'comment': str or None,
            'doi': str or None,
            'pdf_url': str or None,
        }
    """
    ns = _NAMESPACES

    def _get_text(elem, tag, namespace='atom'):
        """安全获取子元素文本。"""
        if elem is None:
            return None
        full_tag = f'{namespace}:{tag}' if namespace else tag
        child = elem.find(full_tag, ns)
        if child is not None and child.text:
            return child.text.strip()
        return None

    def _get_attr(elem, tag, attr, namespace='atom'):
        """安全获取子元素属性。"""
        if elem is None:
            return None
        full_tag = f'{namespace}:{tag}' if namespace else tag
        child = elem.find(full_tag, ns)
        if child is not None:
            return child.get(attr)
        return None

    # 标题（arXiv 标题可能含多余空白）
    title = _get_text(entry, 'title') or ''
    # 压缩多余空白
    title = ' '.join(title.split())

    # 作者列表
    authors = []
    for author_elem in entry.findall('atom:author', ns):
        name_elem = author_elem.find('atom:name', ns)
        if name_elem is not None and name_elem.text:
            authors.append(name_elem.text.strip())

    # 摘要
    abstract = _get_text(entry, 'summary') or ''
    abstract = ' '.join(abstract.split())  # 压缩空白

    # URL（arXiv abs 页面）
    url = _get_text(entry, 'id') or ''

    # 发布/更新时间
    published = _get_text(entry, 'published')
    updated = _get_text(entry, 'updated')

    # 分类
    categories = []
    for cat_elem in entry.findall('atom:category', ns):
        term = cat_elem.get('term')
        if term:
            categories.append(term)

    # 主分类
    primary_category = _get_attr(entry, 'primary_category', 'term', namespace='arxiv')

    # 其他可选字段
    comment = _get_text(entry, 'comment', namespace='arxiv')
    doi = _get_text(entry, 'doi', namespace='arxiv')

    # PDF 链接
    pdf_url = None
    for link in entry.findall('atom:link', ns):
        if link.get('title') == 'pdf':
            pdf_url = link.get('href')
            break

    return {
        'title': title,
        'authors': authors,
        'abstract': abstract,
        'url': url,
        'published': published,
        'updated': updated,
        'categories': categories,
        'primary_category': primary_category,
        'comment': comment,
        'doi': doi,
        'pdf_url': pdf_url,
        'source': 'arxiv',
    }


def arxiv_search(query, max_results=DEFAULT_MAX_RESULTS, category=None,
                 sort_by='submittedDate', sort_order='descending',
                 timeout=DEFAULT_TIMEOUT, redact=True):
    """搜索 arXiv 论文。

    Args:
        query: 搜索查询字符串
        max_results: 最大结果数（1-100）
        category: arXiv 分类（如 'cs.AI'），可选
        sort_by: 排序字段（'relevance' | 'lastUpdatedDate' | 'submittedDate'）
        sort_order: 排序方向（'ascending' | 'descending'）
        timeout: HTTP 超时秒数
        redact: 是否对 query 做出境 PII 脱敏（默认 True）

    Returns:
        dict: {
            'success': bool,
            'query': str（脱敏后）,
            'results': list of paper dicts,
            'count': int,
            'total_results': int,  # arXiv 报告的总匹配数
            'start_index': int,
            'error': str（仅失败时）,
        }
    """
    # 校验 query
    if not isinstance(query, str) or not query.strip():
        return {
            'success': False,
            'query': '',
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': 'Empty query',
        }

    # 校验 max_results
    try:
        max_results = int(max_results)
        if max_results < 1:
            max_results = 1
        elif max_results > MAX_RESULTS_CAP:
            max_results = MAX_RESULTS_CAP
    except (ValueError, TypeError):
        max_results = DEFAULT_MAX_RESULTS

    # 校验 sort_by
    if sort_by not in SORT_OPTIONS:
        sort_by = 'submittedDate'

    # 校验 sort_order
    if sort_order not in SORT_ORDERS:
        sort_order = 'descending'

    # Layer 0 PII 脱敏
    if redact:
        try:
            redacted_query, meta = require_redacted_query(query, _redact_outbound)
        except OutboundRedactionError as exc:
            return {
            'success': False,
            'query': '',
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': str(exc),
            }
        redacted_count = meta.get('redacted_count', 0)
        if redacted_count > 0:
            sys.stderr.write(
                f"[arxiv] Layer 0 redacted {redacted_count} PII pattern(s); "
                f"types: {meta.get('patterns_matched', [])}\n"
            )
        query = redacted_query

    # 截断 query（脱敏后再截断，避免 [REDACTED-PHONE] 标记被截半）
    query = query[:MAX_QUERY_LEN]

    # 构建 URL
    url = _build_arxiv_url(
        query, max_results=max_results, category=category,
        sort_by=sort_by, sort_order=sort_order
    )

    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'OpenCodeSearchBot/4.3 (arxiv-mcp)'},
        method='GET',
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
                    'total_results': 0,
                    'start_index': 0,
                    'error': f'HTTP {status}',
                }
            xml_data = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            pass
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': f'arXiv API HTTP {e.code}: {e.reason}',
            'error_body': body,
        }
    except urllib.error.URLError as e:
        reason = e.reason
        reason_str = str(reason).lower()
        # urlopen(timeout=...) 通常抛 URLError(reason=socket.timeout)
        # reason 可能是字符串 'timed out' 或 socket.timeout 对象
        if 'timeout' in reason_str or 'timed out' in reason_str:
            return {
                'success': False,
                'query': query,
                'results': [],
                'count': 0,
                'total_results': 0,
                'start_index': 0,
                'error': f'Timeout after {timeout}s',
            }
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': f'URL error: {reason}',
        }
    except TimeoutError:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': f'Timeout after {timeout}s',
        }
    except Exception as e:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': f'{type(e).__name__}: {e}',
        }

    # 解析 Atom XML
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'total_results': 0,
            'start_index': 0,
            'error': f'XML parse error: {e}',
        }

    # 解析 opensearch 元数据
    ns = _NAMESPACES
    total_results = 0
    start_index = 0

    total_elem = root.find('opensearch:totalResults', ns)
    if total_elem is not None and total_elem.text:
        try:
            total_results = int(total_elem.text)
        except ValueError:
            pass

    start_elem = root.find('opensearch:startIndex', ns)
    if start_elem is not None and start_elem.text:
        try:
            start_index = int(start_elem.text)
        except ValueError:
            pass

    # 解析 entries
    results = []
    for entry in root.findall('atom:entry', ns):
        try:
            paper = _parse_arxiv_entry(entry)
            results.append(paper)
        except Exception as e:
            # 单个 entry 解析失败不应阻塞整个结果
            sys.stderr.write(f"[arxiv] entry parse failed: {e}\n")
            continue

    return {
        'success': True,
        'query': query,
        'results': results,
        'count': len(results),
        'total_results': total_results,
        'start_index': start_index,
    }


# ── MCP Server (JSON-RPC 2.0 over stdio) ─────────────────────
def _make_tool_list():
    """构建 tools/list 响应。"""
    return {
        'tools': [
            {
                'name': 'arxiv_search',
                'description': (
                    'Search arXiv preprint server for academic papers. '
                    'Returns papers with title, authors, abstract, URL, '
                    'published date, categories. No API key required. '
                    'PII in query is automatically redacted before sending.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'Search query string',
                        },
                        'max_results': {
                            'type': 'integer',
                            'description': f'Max results (1-{MAX_RESULTS_CAP}, default {DEFAULT_MAX_RESULTS})',
                            'default': DEFAULT_MAX_RESULTS,
                        },
                        'category': {
                            'type': 'string',
                            'description': 'arXiv category filter (e.g., cs.AI, cs.CL, stat.ML)',
                        },
                        'sort_by': {
                            'type': 'string',
                            'enum': list(SORT_OPTIONS.keys()),
                            'description': 'Sort field (default: submittedDate)',
                            'default': 'submittedDate',
                        },
                        'sort_order': {
                            'type': 'string',
                            'enum': list(SORT_ORDERS),
                            'description': 'Sort order (default: descending)',
                            'default': 'descending',
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

        if tool_name != 'arxiv_search':
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

        result = arxiv_search(
            query,
            max_results=args.get('max_results', DEFAULT_MAX_RESULTS),
            category=args.get('category'),
            sort_by=args.get('sort_by', 'submittedDate'),
            sort_order=args.get('sort_order', 'descending'),
        )

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

        # 支持批量请求
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
        description='arXiv MCP Server — Academic preprint search'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # search 子命令
    p_search = sub.add_parser('search', help='Search arXiv directly')
    p_search.add_argument('--query', required=True, help='Search query')
    p_search.add_argument('--max', type=int, default=DEFAULT_MAX_RESULTS,
                          help=f'Max results (default {DEFAULT_MAX_RESULTS})')
    p_search.add_argument('--category', default=None,
                          help='arXiv category (e.g., cs.AI)')
    p_search.add_argument('--sort-by', default='submittedDate',
                          choices=list(SORT_OPTIONS.keys()),
                          help='Sort field (default submittedDate)')
    p_search.add_argument('--sort-order', default='descending',
                          choices=list(SORT_ORDERS),
                          help='Sort order (default descending)')
    p_search.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                          help=f'HTTP timeout (default {DEFAULT_TIMEOUT})')
    p_search.add_argument('--no-redact', action='store_true',
                          help='Disable PII redaction (NOT recommended)')
    p_search.add_argument('--json', action='store_true', help='JSON output')

    # serve 子命令
    sub.add_parser('serve', help='Run as MCP server (stdio JSON-RPC)')

    args = parser.parse_args()

    if args.command == 'search':
        result = arxiv_search(
            args.query,
            max_results=args.max,
            category=args.category,
            sort_by=args.sort_by,
            sort_order=args.sort_order,
            timeout=args.timeout,
            redact=not args.no_redact,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                print(f"Found {result['count']} arXiv papers "
                      f"(total matches: {result['total_results']}) for: {result['query']}")
                for i, paper in enumerate(result['results'], 1):
                    print(f"\n{i}. {paper['title']}")
                    print(f"   Authors: {', '.join(paper['authors'][:5])}"
                          + (f" (+{len(paper['authors']) - 5} more)" if len(paper['authors']) > 5 else ''))
                    print(f"   URL: {paper['url']}")
                    if paper['pdf_url']:
                        print(f"   PDF: {paper['pdf_url']}")
                    print(f"   Published: {paper['published']}")
                    if paper['primary_category']:
                        print(f"   Primary category: {paper['primary_category']}")
                    if paper['categories']:
                        print(f"   Categories: {', '.join(paper['categories'])}")
                    # 摘要截断到 200 字
                    abstract = paper['abstract']
                    if len(abstract) > 200:
                        abstract = abstract[:200] + '...'
                    print(f"   Abstract: {abstract}")
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
