#!/usr/bin/env python3
"""Semantic Scholar MCP Server — Academic paper search + citation network.

Semantic Scholar API is free, no API key required (rate-limited).
Covers published papers (post-arXiv) + citation graph.

设计原则：
- 单文件独立模块，零外部依赖（仅 Python 标准库）
- 多工具暴露：search_papers / get_paper / get_citations / get_references / get_author
- MCP server：JSON-RPC 2.0 over stdio
- Layer 0 PII 脱敏：出境前对所有 query 做正则脱敏
- 错误兜底：API 失败、超时、JSON 解析错误均返回结构化错误，绝不崩溃

Usage as MCP server:
    python -m modules.search.semantic_scholar_mcp serve

Usage as library:
    from modules.search.semantic_scholar_mcp import search_papers, get_paper
    result = search_papers("transformer attention")

CLI:
    python semantic_scholar_mcp.py search --query "..."
    python semantic_scholar_mcp.py paper --id "10.1145/3292500.3330701"
    python semantic_scholar_mcp.py citations --paper-id "..."
    python semantic_scholar_mcp.py references --paper-id "..."
    python semantic_scholar_mcp.py author --author-id "..."

Tools exposed:
    search_papers(query, limit=10, fields=..., year=None, venue=None,
                  fields_of_study=None)
    get_paper(paper_id, fields=...)
    get_citations(paper_id, limit=20, fields=...)
    get_references(paper_id, limit=20, fields=...)
    get_author(author_id)
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import urllib.parse

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 导入 privacy 模块做出境 PII 脱敏
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(text):
        return text, {'redacted_count': 0}


# ── Constants ────────────────────────────────────────────────
S2_API_URL = 'https://api.semanticscholar.org/graph/v1'
DEFAULT_TIMEOUT = 30
DEFAULT_LIMIT = 10
MAX_LIMIT = 100  # Semantic Scholar API cap per request
MAX_QUERY_LEN = 500

# 默认返回字段（避免请求过多字段触发限流）
DEFAULT_PAPER_FIELDS = 'title,authors,abstract,year,citationCount,url,venue,publicationDate,externalIds'
DEFAULT_CITATION_FIELDS = 'title,authors,year,citationCount,url,venue'

# MCP protocol constants
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'semantic-scholar-mcp'
SERVER_VERSION = '1.0.0'


# ── HTTP helper ────────────────────────────────────────────
def _make_s2_request(endpoint, params, timeout=DEFAULT_TIMEOUT):
    """调用 Semantic Scholar API 的通用方法。

    Args:
        endpoint: API 路径（如 '/paper/search' 或 '/paper/{id}'）
        params: dict，URL 参数
        timeout: HTTP 超时秒数

    Returns:
        tuple: (success: bool, data: dict, error: str or None)
    """
    url = f"{S2_API_URL}{endpoint}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'OpenCodeSearchBot/4.3 (semantic-scholar-mcp)',
            'Accept': 'application/json',
        },
        method='GET',
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            if status < 200 or status >= 300:
                return False, {}, f'HTTP {status}'
            data = json.loads(resp.read().decode('utf-8'))
            return True, data, None
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            pass
        return False, {}, f'S2 API HTTP {e.code}: {e.reason} (body: {body})'
    except urllib.error.URLError as e:
        reason = e.reason
        reason_str = str(reason).lower()
        if 'timeout' in reason_str or 'timed out' in reason_str:
            return False, {}, f'Timeout after {timeout}s'
        return False, {}, f'URL error: {reason}'
    except TimeoutError:
        return False, {}, f'Timeout after {timeout}s'
    except json.JSONDecodeError as e:
        return False, {}, f'Response parse failed: {e}'
    except Exception as e:
        return False, {}, f'{type(e).__name__}: {e}'


# ── Tool implementations ───────────────────────────────────
def search_papers(query, limit=DEFAULT_LIMIT, fields=None,
                  year=None, venue=None, fields_of_study=None,
                  timeout=DEFAULT_TIMEOUT, redact=True):
    """搜索 Semantic Scholar 论文。

    Args:
        query: 搜索查询字符串
        limit: 返回数（1-100）
        fields: 返回字段（逗号分隔，默认 DEFAULT_PAPER_FIELDS）
        year: 年份过滤（如 '2020' 或 '2018-2023'）
        venue: 期刊/会议过滤
        fields_of_study: 学科过滤（如 'Computer Science'）
        timeout: HTTP 超时秒数
        redact: 是否对 query 做出境 PII 脱敏（默认 True）

    Returns:
        dict: {
            'success': bool,
            'query': str（脱敏后）,
            'results': list of paper dicts,
            'count': int,
            'total': int,  # S2 报告的总匹配数
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
            'total': 0,
            'error': 'Empty query',
        }

    # 校验 limit
    try:
        limit = int(limit)
        if limit < 1:
            limit = 1
        elif limit > MAX_LIMIT:
            limit = MAX_LIMIT
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT

    # 默认字段
    if not fields:
        fields = DEFAULT_PAPER_FIELDS

    # Layer 0 PII 脱敏
    if redact:
        redacted_query, meta = _redact_outbound(query)
        redacted_count = meta.get('redacted_count', 0)
        if redacted_count > 0:
            sys.stderr.write(
                f"[s2] Layer 0 redacted {redacted_count} PII pattern(s); "
                f"types: {meta.get('patterns_matched', [])}\n"
            )
        query = redacted_query

    # 截断 query（脱敏后再截断，避免 [REDACTED-EMAIL] 等标记被截半）
    query = query[:MAX_QUERY_LEN]

    params = {
        'query': query,
        'limit': limit,
        'fields': fields,
    }
    if year:
        params['year'] = year
    if venue:
        params['venue'] = venue
    if fields_of_study:
        params['fieldsOfStudy'] = fields_of_study

    success, data, error = _make_s2_request('/paper/search', params, timeout)
    if not success:
        return {
            'success': False,
            'query': query,
            'results': [],
            'count': 0,
            'total': 0,
            'error': error,
        }

    papers = data.get('data', [])
    if not isinstance(papers, list):
        papers = []

    # 标记 source
    for paper in papers:
        if isinstance(paper, dict):
            paper['source'] = 'semantic_scholar'

    return {
        'success': True,
        'query': query,
        'results': papers,
        'count': len(papers),
        'total': data.get('total', len(papers)),
    }


def get_paper(paper_id, fields=None, timeout=DEFAULT_TIMEOUT):
    """获取单篇论文详情。

    Args:
        paper_id: 论文 ID（如 '10.1145/3292500.3330701' 或 'arXiv:1706.03762' 或 S2 paperId）
        fields: 返回字段
        timeout: HTTP 超时

    Returns:
        dict: {success, paper: dict, error}
    """
    if not isinstance(paper_id, str) or not paper_id.strip():
        return {
            'success': False,
            'paper': None,
            'error': 'Empty paper_id',
        }

    if not fields:
        fields = DEFAULT_PAPER_FIELDS

    # URL 编码 paper_id（DOI 含 / 需编码）
    encoded_id = urllib.parse.quote(paper_id, safe=':')
    endpoint = f'/paper/{encoded_id}'
    params = {'fields': fields}

    success, data, error = _make_s2_request(endpoint, params, timeout)
    if not success:
        return {
            'success': False,
            'paper': None,
            'error': error,
        }

    if isinstance(data, dict):
        data['source'] = 'semantic_scholar'

    return {
        'success': True,
        'paper': data,
    }


def get_citations(paper_id, limit=DEFAULT_LIMIT, fields=None,
                  timeout=DEFAULT_TIMEOUT):
    """获取引用该论文的论文列表（citing papers）。

    Args:
        paper_id: 论文 ID
        limit: 返回数（1-100）
        fields: 返回字段
        timeout: HTTP 超时

    Returns:
        dict: {success, citations: list, count, next_offset, error}
    """
    if not isinstance(paper_id, str) or not paper_id.strip():
        return {
            'success': False,
            'citations': [],
            'count': 0,
            'error': 'Empty paper_id',
        }

    try:
        limit = int(limit)
        if limit < 1:
            limit = 1
        elif limit > MAX_LIMIT:
            limit = MAX_LIMIT
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT

    if not fields:
        fields = DEFAULT_CITATION_FIELDS

    encoded_id = urllib.parse.quote(paper_id, safe=':')
    endpoint = f'/paper/{encoded_id}/citations'
    params = {
        'limit': limit,
        'fields': fields,
    }

    success, data, error = _make_s2_request(endpoint, params, timeout)
    if not success:
        return {
            'success': False,
            'citations': [],
            'count': 0,
            'error': error,
        }

    # S2 citations 返回格式：{'data': [{'citingPaper': {...}, 'contexts': [...]}]}
    raw_citations = data.get('data', [])
    if not isinstance(raw_citations, list):
        raw_citations = []

    citations = []
    for item in raw_citations:
        if not isinstance(item, dict):
            continue
        citing_paper = item.get('citingPaper')
        if isinstance(citing_paper, dict) and citing_paper:
            citing_paper['source'] = 'semantic_scholar'
            citations.append(citing_paper)

    return {
        'success': True,
        'citations': citations,
        'count': len(citations),
        'next_offset': data.get('next'),
    }


def get_references(paper_id, limit=DEFAULT_LIMIT, fields=None,
                   timeout=DEFAULT_TIMEOUT):
    """获取该论文引用的论文列表（referenced papers）。

    Args:
        paper_id: 论文 ID
        limit: 返回数（1-100）
        fields: 返回字段
        timeout: HTTP 超时

    Returns:
        dict: {success, references: list, count, next_offset, error}
    """
    if not isinstance(paper_id, str) or not paper_id.strip():
        return {
            'success': False,
            'references': [],
            'count': 0,
            'error': 'Empty paper_id',
        }

    try:
        limit = int(limit)
        if limit < 1:
            limit = 1
        elif limit > MAX_LIMIT:
            limit = MAX_LIMIT
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT

    if not fields:
        fields = DEFAULT_CITATION_FIELDS

    encoded_id = urllib.parse.quote(paper_id, safe=':')
    endpoint = f'/paper/{encoded_id}/references'
    params = {
        'limit': limit,
        'fields': fields,
    }

    success, data, error = _make_s2_request(endpoint, params, timeout)
    if not success:
        return {
            'success': False,
            'references': [],
            'count': 0,
            'error': error,
        }

    # S2 references 返回格式：{'data': [{'citedPaper': {...}, 'contexts': [...], 'intents': [...]}]}
    raw_refs = data.get('data', [])
    if not isinstance(raw_refs, list):
        raw_refs = []

    references = []
    for item in raw_refs:
        if not isinstance(item, dict):
            continue
        cited_paper = item.get('citedPaper')
        if isinstance(cited_paper, dict) and cited_paper:
            cited_paper['source'] = 'semantic_scholar'
            references.append(cited_paper)

    return {
        'success': True,
        'references': references,
        'count': len(references),
        'next_offset': data.get('next'),
    }


def get_author(author_id, timeout=DEFAULT_TIMEOUT):
    """获取作者详情。

    Args:
        author_id: 作者 ID（Semantic Scholar authorId）

    Returns:
        dict: {success, author: dict, error}
    """
    if not isinstance(author_id, str) or not author_id.strip():
        return {
            'success': False,
            'author': None,
            'error': 'Empty author_id',
        }

    encoded_id = urllib.parse.quote(author_id, safe=':')
    endpoint = f'/author/{encoded_id}'
    params = {'fields': 'name,url,affiliations,homepage,paperCount,citationCount,hIndex'}

    success, data, error = _make_s2_request(endpoint, params, timeout)
    if not success:
        return {
            'success': False,
            'author': None,
            'error': error,
        }

    return {
        'success': True,
        'author': data,
    }


# ── MCP Server (JSON-RPC 2.0 over stdio) ─────────────────────
def _make_tool_list():
    """构建 tools/list 响应，5 个工具。"""
    return {
        'tools': [
            {
                'name': 'search_papers',
                'description': (
                    'Search Semantic Scholar for academic papers. '
                    'Free, no API key required. Covers published papers + '
                    'citation network. PII in query is auto-redacted.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {'type': 'string', 'description': 'Search query'},
                        'limit': {
                            'type': 'integer',
                            'description': f'Max results (1-{MAX_LIMIT}, default {DEFAULT_LIMIT})',
                            'default': DEFAULT_LIMIT,
                        },
                        'year': {
                            'type': 'string',
                            'description': 'Year filter (e.g., "2020" or "2018-2023")',
                        },
                        'venue': {
                            'type': 'string',
                            'description': 'Venue filter (e.g., "NeurIPS")',
                        },
                        'fields_of_study': {
                            'type': 'string',
                            'description': 'Field of study (e.g., "Computer Science")',
                        },
                    },
                    'required': ['query'],
                },
            },
            {
                'name': 'get_paper',
                'description': (
                    'Get paper details by ID. '
                    'Supports DOI (e.g., "10.1145/3292500.3330701"), '
                    'arXiv ID (e.g., "arXiv:1706.03762"), '
                    'or Semantic Scholar paperId.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'paper_id': {'type': 'string', 'description': 'Paper ID'},
                    },
                    'required': ['paper_id'],
                },
            },
            {
                'name': 'get_citations',
                'description': (
                    'Get papers that cite the given paper (citation network - incoming).'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'paper_id': {'type': 'string', 'description': 'Paper ID'},
                        'limit': {
                            'type': 'integer',
                            'description': f'Max results (default {DEFAULT_LIMIT})',
                            'default': DEFAULT_LIMIT,
                        },
                    },
                    'required': ['paper_id'],
                },
            },
            {
                'name': 'get_references',
                'description': (
                    'Get papers cited by the given paper (citation network - outgoing).'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'paper_id': {'type': 'string', 'description': 'Paper ID'},
                        'limit': {
                            'type': 'integer',
                            'description': f'Max results (default {DEFAULT_LIMIT})',
                            'default': DEFAULT_LIMIT,
                        },
                    },
                    'required': ['paper_id'],
                },
            },
            {
                'name': 'get_author',
                'description': 'Get author details by Semantic Scholar authorId.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'author_id': {'type': 'string', 'description': 'Author ID'},
                    },
                    'required': ['author_id'],
                },
            },
        ]
    }


def _handle_request(request):
    """处理单个 JSON-RPC 请求。"""
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

        if tool_name == 'search_papers':
            query = args.get('query', '')
            if not query:
                return _error_response(req_id, 'query is required')
            result = search_papers(
                query,
                limit=args.get('limit', DEFAULT_LIMIT),
                year=args.get('year'),
                venue=args.get('venue'),
                fields_of_study=args.get('fields_of_study'),
            )
            return _result_response(req_id, result)

        elif tool_name == 'get_paper':
            paper_id = args.get('paper_id', '')
            if not paper_id:
                return _error_response(req_id, 'paper_id is required')
            result = get_paper(paper_id)
            return _result_response(req_id, result)

        elif tool_name == 'get_citations':
            paper_id = args.get('paper_id', '')
            if not paper_id:
                return _error_response(req_id, 'paper_id is required')
            result = get_citations(
                paper_id,
                limit=args.get('limit', DEFAULT_LIMIT),
            )
            return _result_response(req_id, result)

        elif tool_name == 'get_references':
            paper_id = args.get('paper_id', '')
            if not paper_id:
                return _error_response(req_id, 'paper_id is required')
            result = get_references(
                paper_id,
                limit=args.get('limit', DEFAULT_LIMIT),
            )
            return _result_response(req_id, result)

        elif tool_name == 'get_author':
            author_id = args.get('author_id', '')
            if not author_id:
                return _error_response(req_id, 'author_id is required')
            result = get_author(author_id)
            return _result_response(req_id, result)

        else:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'},
            }

    if is_notification:
        return None
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'error': {'code': -32601, 'message': f'Method not found: {method}'},
    }


def _result_response(req_id, result):
    """构造 tools/call 成功响应。"""
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


def _error_response(req_id, message):
    """构造 tools/call 错误响应（参数缺失）。"""
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'result': {
            'content': [{
                'type': 'text',
                'text': json.dumps({
                    'success': False,
                    'error': message,
                }, ensure_ascii=False),
            }],
            'isError': True,
        },
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
        description='Semantic Scholar MCP Server — Academic paper search + citation network'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # search 子命令
    p_search = sub.add_parser('search', help='Search papers')
    p_search.add_argument('--query', required=True)
    p_search.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    p_search.add_argument('--year', default=None)
    p_search.add_argument('--venue', default=None)
    p_search.add_argument('--fields-of-study', default=None)
    p_search.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_search.add_argument('--no-redact', action='store_true')
    p_search.add_argument('--json', action='store_true')

    # paper 子命令
    p_paper = sub.add_parser('paper', help='Get paper details by ID')
    p_paper.add_argument('--id', required=True, dest='paper_id')
    p_paper.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_paper.add_argument('--json', action='store_true')

    # citations 子命令
    p_cit = sub.add_parser('citations', help='Get citing papers')
    p_cit.add_argument('--paper-id', required=True)
    p_cit.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    p_cit.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_cit.add_argument('--json', action='store_true')

    # references 子命令
    p_ref = sub.add_parser('references', help='Get referenced papers')
    p_ref.add_argument('--paper-id', required=True)
    p_ref.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    p_ref.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_ref.add_argument('--json', action='store_true')

    # author 子命令
    p_author = sub.add_parser('author', help='Get author details')
    p_author.add_argument('--author-id', required=True)
    p_author.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_author.add_argument('--json', action='store_true')

    # serve 子命令
    sub.add_parser('serve', help='Run as MCP server (stdio JSON-RPC)')

    args = parser.parse_args()

    if args.command == 'search':
        result = search_papers(
            args.query,
            limit=args.limit,
            year=args.year,
            venue=args.venue,
            fields_of_study=args.fields_of_study,
            timeout=args.timeout,
            redact=not args.no_redact,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                print(f"Found {result['count']} papers (total: {result['total']})")
                for i, p in enumerate(result['results'], 1):
                    print(f"\n{i}. {p.get('title', 'N/A')} ({p.get('year', 'N/A')})")
                    authors = p.get('authors', [])
                    if authors:
                        names = [a.get('name', '') for a in authors[:3]]
                        print(f"   Authors: {', '.join(names)}" +
                              (f" (+{len(authors) - 3})" if len(authors) > 3 else ''))
                    print(f"   Citations: {p.get('citationCount', 0)}")
                    if p.get('venue'):
                        print(f"   Venue: {p['venue']}")
                    if p.get('url'):
                        print(f"   URL: {p['url']}")
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
        return 0

    if args.command == 'paper':
        result = get_paper(args.paper_id, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                p = result['paper']
                print(f"Title: {p.get('title', 'N/A')}")
                print(f"Year: {p.get('year', 'N/A')}")
                authors = p.get('authors', [])
                if authors:
                    print(f"Authors: {', '.join(a.get('name', '') for a in authors[:5])}")
                print(f"Citations: {p.get('citationCount', 0)}")
                if p.get('venue'):
                    print(f"Venue: {p['venue']}")
                if p.get('url'):
                    print(f"URL: {p['url']}")
                abstract = p.get('abstract', '') or ''
                if abstract:
                    print(f"Abstract: {abstract[:300]}...")
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
        return 0

    if args.command == 'citations':
        result = get_citations(args.paper_id, limit=args.limit, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                print(f"Found {result['count']} citing papers")
                for i, p in enumerate(result['citations'], 1):
                    print(f"\n{i}. {p.get('title', 'N/A')} ({p.get('year', 'N/A')})")
                    print(f"   Citations: {p.get('citationCount', 0)}")
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
        return 0

    if args.command == 'references':
        result = get_references(args.paper_id, limit=args.limit, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                print(f"Found {result['count']} references")
                for i, p in enumerate(result['references'], 1):
                    print(f"\n{i}. {p.get('title', 'N/A')} ({p.get('year', 'N/A')})")
            else:
                print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
        return 0

    if args.command == 'author':
        result = get_author(args.author_id, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                a = result['author']
                print(f"Name: {a.get('name', 'N/A')}")
                print(f"Paper count: {a.get('paperCount', 0)}")
                print(f"Citation count: {a.get('citationCount', 0)}")
                print(f"h-index: {a.get('hIndex', 0)}")
                if a.get('affiliations'):
                    print(f"Affiliations: {', '.join(a['affiliations'])}")
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
