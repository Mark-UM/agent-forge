#!/usr/bin/env python3
"""arxiv_mcp.py 单元测试 — arXiv MCP Server.

覆盖维度：
  - 边界输入：空 query / 非 str / 超长 query / max_results 越界
  - URL 构造：含/不含 category / sort_by / sort_order / PII 脱敏
  - XML 解析：标准响应 / 无 entry / 单 entry 解析失败 / XML 格式错误
  - HTTP 失败：HTTPError / URLError / TimeoutError / 通用异常
  - MCP 协议：initialize / tools/list / tools/call / unknown method
  - CLI：search 子命令 / serve 子命令
  - 常量
"""
import sys
import os
import json
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from unittest.mock import patch, MagicMock
from io import StringIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import arxiv_mcp
from arxiv_mcp import (
    arxiv_search, _build_arxiv_url, _parse_arxiv_entry,
    _make_tool_list, _handle_request, _run_server, _cli,
    ARXIV_API_URL, DEFAULT_TIMEOUT, DEFAULT_MAX_RESULTS,
    MAX_RESULTS_CAP, MAX_QUERY_LEN, SORT_OPTIONS, SORT_ORDERS,
    PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION,
)


# ── 测试数据：Atom XML 响应 ──────────────────────────────
SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</opensearch:totalResults>
  <opensearch:startIndex xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:startIndex>
  <opensearch:itemsPerPage xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">2</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v5</id>
    <updated>2023-06-15T00:00:00Z</updated>
    <published>2017-06-12T00:00:00Z</published>
    <title>Attention Is All You Need</title>
    <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.</summary>
    <author>
      <name>Ashish Vaswani</name>
    </author>
    <author>
      <name>Noam Shazeer</name>
    </author>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL"/>
    <category term="cs.CL"/>
    <category term="cs.AI"/>
    <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">15 pages, 5 figures</arxiv:comment>
    <arxiv:doi xmlns:arxiv="http://arxiv.org/schemas/atom">10.48550/arXiv.1706.03762</arxiv:doi>
    <link href="http://arxiv.org/abs/1706.03762v5" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/1706.03762v5" rel="related" title="pdf" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2005.14165v4</id>
    <updated>2020-09-22T00:00:00Z</updated>
    <published>2020-05-28T00:00:00Z</published>
    <title>BERT: Pre-training of Deep Bidirectional Transformers</title>
    <summary>We introduce a new language representation model called BERT.</summary>
    <author>
      <name>Jacob Devlin</name>
    </author>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL"/>
    <category term="cs.CL"/>
  </entry>
</feed>"""

EMPTY_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:totalResults>
  <opensearch:startIndex xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:startIndex>
</feed>"""

MALFORMED_XML = """<?xml version="1.0"?>
<feed><entry><title>incomplete"""


def _make_urlopen_success(content):
    """构造模拟 urlopen 成功的 context manager。"""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.getcode = MagicMock(return_value=200)
    cm.read = MagicMock(return_value=content.encode('utf-8') if isinstance(content, str) else content)
    return cm


# ── 常量测试 ───────────────────────────────────────────────
class TestConstants(unittest.TestCase):
    def test_arxiv_api_url_is_http(self):
        # arXiv API 不强制 HTTPS（虽然 https 也可用，官方文档用 http）
        self.assertTrue(ARXIV_API_URL.startswith('http://') or
                       ARXIV_API_URL.startswith('https://'))

    def test_default_timeout_positive(self):
        self.assertGreater(DEFAULT_TIMEOUT, 0)

    def test_default_max_results_positive(self):
        self.assertGreater(DEFAULT_MAX_RESULTS, 0)

    def test_max_results_cap_at_least_50(self):
        self.assertGreaterEqual(MAX_RESULTS_CAP, 50)

    def test_max_query_len_at_least_100(self):
        self.assertGreaterEqual(MAX_QUERY_LEN, 100)

    def test_protocol_version_is_string(self):
        self.assertIsInstance(PROTOCOL_VERSION, str)

    def test_server_name_is_string(self):
        self.assertEqual(SERVER_NAME, 'arxiv-mcp')

    def test_sort_options_has_submittedDate(self):
        self.assertIn('submittedDate', SORT_OPTIONS)

    def test_sort_orders_has_ascending_and_descending(self):
        self.assertIn('ascending', SORT_ORDERS)
        self.assertIn('descending', SORT_ORDERS)


# ── _build_arxiv_url 测试 ──────────────────────────────────
class TestBuildArxivURL(unittest.TestCase):
    def test_url_starts_with_arxiv_api(self):
        url = _build_arxiv_url('transformer')
        self.assertTrue(url.startswith(ARXIV_API_URL))

    def test_url_contains_search_query(self):
        url = _build_arxiv_url('transformer')
        self.assertIn('search_query=all%3Atransformer', url)

    def test_url_contains_max_results(self):
        url = _build_arxiv_url('test', max_results=20)
        self.assertIn('max_results=20', url)

    def test_url_with_category(self):
        url = _build_arxiv_url('transformer', category='cs.AI')
        self.assertIn('cat%3Acs.AI', url)
        self.assertIn('AND', url)

    def test_url_without_category(self):
        url = _build_arxiv_url('test')
        self.assertNotIn('cat%3A', url)

    def test_url_sort_by_submittedDate(self):
        url = _build_arxiv_url('test', sort_by='submittedDate')
        self.assertIn('sortBy=submittedDate', url)

    def test_url_sort_order_descending(self):
        url = _build_arxiv_url('test', sort_order='descending')
        self.assertIn('sortOrder=descending', url)


# ── _parse_arxiv_entry 测试 ────────────────────────────────
class TestParseArxivEntry(unittest.TestCase):
    def setUp(self):
        root = ET.fromstring(SAMPLE_ATOM_XML)
        self.entries = root.findall(
            'atom:entry',
            {'atom': 'http://www.w3.org/2005/Atom',
             'arxiv': 'http://arxiv.org/schemas/atom'}
        )

    def test_parse_first_entry(self):
        paper = _parse_arxiv_entry(self.entries[0])
        self.assertEqual(paper['title'], 'Attention Is All You Need')
        self.assertIn('Ashish Vaswani', paper['authors'])
        self.assertIn('Noam Shazeer', paper['authors'])
        self.assertEqual(len(paper['authors']), 2)
        self.assertEqual(paper['url'], 'http://arxiv.org/abs/1706.03762v5')
        self.assertEqual(paper['published'], '2017-06-12T00:00:00Z')
        self.assertEqual(paper['primary_category'], 'cs.CL')
        self.assertIn('cs.CL', paper['categories'])
        self.assertIn('cs.AI', paper['categories'])
        self.assertEqual(paper['comment'], '15 pages, 5 figures')
        self.assertEqual(paper['doi'], '10.48550/arXiv.1706.03762')
        self.assertEqual(paper['pdf_url'], 'http://arxiv.org/pdf/1706.03762v5')
        self.assertEqual(paper['source'], 'arxiv')

    def test_parse_second_entry(self):
        paper = _parse_arxiv_entry(self.entries[1])
        self.assertEqual(paper['title'], 'BERT: Pre-training of Deep Bidirectional Transformers')
        self.assertEqual(len(paper['authors']), 1)
        self.assertEqual(paper['authors'][0], 'Jacob Devlin')
        self.assertIsNone(paper['comment'])
        self.assertIsNone(paper['doi'])
        self.assertIsNone(paper['pdf_url'])  # No PDF link in second entry

    def test_whitespace_in_title_compressed(self):
        # arXiv titles often have line breaks / extra whitespace
        xml_with_extra_ws = """<entry xmlns="http://www.w3.org/2005/Atom">
            <title>Title with
            line break and   extra   spaces</title>
            <summary>abstract</summary>
            <id>http://arxiv.org/abs/1234</id>
        </entry>"""
        entry = ET.fromstring(xml_with_extra_ws)
        paper = _parse_arxiv_entry(entry)
        self.assertEqual(paper['title'], 'Title with line break and extra spaces')


# ── arxiv_search 边界测试 ──────────────────────────────────
class TestArxivSearchEdgeCases(unittest.TestCase):
    def test_empty_query_returns_failure(self):
        result = arxiv_search('')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_whitespace_query_returns_failure(self):
        result = arxiv_search('   ')
        self.assertFalse(result['success'])

    def test_non_string_query_returns_failure(self):
        result = arxiv_search(None)
        self.assertFalse(result['success'])
        result = arxiv_search(12345)
        self.assertFalse(result['success'])


# ── max_results 校验 ──────────────────────────────────────
class TestMaxResultsValidation(unittest.TestCase):
    def test_max_results_zero_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', max_results=0)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('max_results=1', url)

    def test_max_results_negative_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', max_results=-5)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('max_results=1', url)

    def test_max_results_above_cap_capped(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', max_results=999)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn(f'max_results={MAX_RESULTS_CAP}', url)

    def test_max_results_string_converted(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', max_results='5')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('max_results=5', url)

    def test_max_results_invalid_uses_default(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', max_results='abc')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn(f'max_results={DEFAULT_MAX_RESULTS}', url)


# ── query 截断 ─────────────────────────────────────────────
class TestQueryTruncation(unittest.TestCase):
    def test_long_query_truncated(self):
        long_query = 'a' * (MAX_QUERY_LEN + 100)
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search(long_query)
            url = mock_urlopen.call_args[0][0].full_url
            # 检查 query 在 URL 中是否被截断
            # URL 编码后，原 query 长 + 4 (all:)
            # 这里只验证 query 不超过 MAX_QUERY_LEN
            # 解析 URL 验证
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            search_q = qs['search_query'][0]
            # all: 前缀 4 个字符
            self.assertLessEqual(len(search_q), 4 + MAX_QUERY_LEN)


# ── sort_by / sort_order 校验 ─────────────────────────────
class TestSortValidation(unittest.TestCase):
    def test_invalid_sort_by_defaults_to_submittedDate(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', sort_by='invalid_sort')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('sortBy=submittedDate', url)

    def test_invalid_sort_order_defaults_to_descending(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            arxiv_search('test', sort_order='invalid')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('sortOrder=descending', url)


# ── HTTP 成功路径 ─────────────────────────────────────────
class TestArxivSearchSuccess(unittest.TestCase):
    def test_successful_search_returns_results(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            result = arxiv_search('transformer attention')

        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['total_results'], 2)
        self.assertEqual(result['start_index'], 0)
        self.assertEqual(result['results'][0]['title'], 'Attention Is All You Need')
        self.assertIn('Ashish Vaswani', result['results'][0]['authors'])

    def test_empty_results_returns_zero_count(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(EMPTY_ATOM_XML)
            result = arxiv_search('nonexistent topic')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['total_results'], 0)

    def test_results_truncated_to_max_results(self):
        # 即使 XML 含多个 entry，max_results 也应限制返回数
        # 但实际 arXiv API 在 max_results 处即截断 XML
        # 这里测试 max_results 传递正确
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            arxiv_search('test', max_results=5)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('max_results=5', url)

    def test_request_uses_get_method(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            arxiv_search('test')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.get_method(), 'GET')

    def test_request_has_user_agent(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            arxiv_search('test')
            req = mock_urlopen.call_args[0][0]
            self.assertIn('User-agent', req.headers)

    def test_query_redacted_in_result(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            result = arxiv_search('contact 13800138000 for help')
        # 返回的 query 字段应是脱敏后的
        self.assertNotIn('13800138000', result['query'])
        self.assertIn('[REDACTED-PHONE]', result['query'])

    def test_no_redact_flag_preserves_pii(self):
        """Regression: --no-redact flag must actually preserve PII.

        Previously _build_arxiv_url() unconditionally called _redact_outbound,
        making the `redact=False` parameter a silent no-op. This test guards
        against regression.
        """
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            arxiv_search('contact 13800138000 for help', redact=False)
            url = mock_urlopen.call_args[0][0].full_url
            # PII must be preserved when redact=False
            self.assertIn('13800138000', url)

    def test_pii_not_in_url(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            arxiv_search('email me at john@example.com')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertNotIn('john@example.com', url)
            # [REDACTED-EMAIL] 在 URL 中会被编码为 %5BREDACTED-EMAIL%5D
            from urllib.parse import urlparse, parse_qs, unquote
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            search_q = qs['search_query'][0]
            decoded = unquote(search_q)
            self.assertIn('[REDACTED-EMAIL]', decoded)


# ── HTTP 失败路径 ─────────────────────────────────────────
class TestArxivSearchHTTPErrors(unittest.TestCase):
    def test_http_error_returns_failure(self):
        error = urllib.error.HTTPError(
            url=ARXIV_API_URL, code=403, msg='Forbidden',
            hdrs=None, fp=None,
        )
        error.read = MagicMock(return_value=b'Forbidden')
        with patch('urllib.request.urlopen', side_effect=error):
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('403', result['error'])
        self.assertIn('error_body', result)

    def test_url_error_returns_failure(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('DNS failed')):
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('URL error', result['error'])

    def test_url_error_timeout_treated_as_timeout(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('timed out')):
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('Timeout', result['error'])

    def test_timeout_returns_failure(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError()):
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('Timeout', result['error'])

    def test_xml_parse_error_returns_failure(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(MALFORMED_XML)
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('XML parse', result['error'])

    def test_generic_exception_returns_failure(self):
        with patch('urllib.request.urlopen',
                   side_effect=RuntimeError('unexpected')):
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('RuntimeError', result['error'])

    def test_non_2xx_status_returns_failure(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.getcode = MagicMock(return_value=500)
        cm.read = MagicMock(return_value=b'{}')
        with patch('urllib.request.urlopen', return_value=cm):
            result = arxiv_search('test')
        self.assertFalse(result['success'])
        self.assertIn('HTTP 500', result['error'])


# ── MCP 协议测试 ──────────────────────────────────────────
class TestMCPProtocol(unittest.TestCase):
    def test_make_tool_list_returns_dict_with_tools(self):
        result = _make_tool_list()
        self.assertIn('tools', result)
        self.assertEqual(result['tools'][0]['name'], 'arxiv_search')

    def test_make_tool_list_has_input_schema(self):
        result = _make_tool_list()
        tool = result['tools'][0]
        self.assertIn('inputSchema', tool)
        schema = tool['inputSchema']
        self.assertEqual(schema['type'], 'object')
        self.assertIn('query', schema['properties'])
        self.assertIn('max_results', schema['properties'])
        self.assertIn('category', schema['properties'])
        self.assertIn('sort_by', schema['properties'])
        self.assertIn('sort_order', schema['properties'])
        self.assertIn('query', schema['required'])

    def test_initialize_returns_protocol_version(self):
        req = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}
        resp = _handle_request(req)
        self.assertEqual(resp['result']['protocolVersion'], PROTOCOL_VERSION)
        self.assertEqual(resp['result']['serverInfo']['name'], SERVER_NAME)

    def test_notifications_initialized_returns_none(self):
        req = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
        self.assertIsNone(_handle_request(req))

    def test_tools_list_returns_tools(self):
        req = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}
        resp = _handle_request(req)
        self.assertIn('tools', resp['result'])

    def test_tools_call_unknown_tool_returns_error(self):
        req = {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
               'params': {'name': 'unknown', 'arguments': {}}}
        resp = _handle_request(req)
        self.assertEqual(resp['error']['code'], -32601)

    def test_tools_call_empty_query_returns_error_content(self):
        req = {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
               'params': {'name': 'arxiv_search', 'arguments': {'query': ''}}}
        resp = _handle_request(req)
        self.assertTrue(resp['result']['isError'])

    def test_tools_call_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            req = {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                   'params': {'name': 'arxiv_search',
                              'arguments': {'query': 'transformer'}}}
            resp = _handle_request(req)
        self.assertFalse(resp['result']['isError'])
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 2)

    def test_unknown_method_returns_error(self):
        req = {'jsonrpc': '2.0', 'id': 6, 'method': 'nonexistent'}
        resp = _handle_request(req)
        self.assertEqual(resp['error']['code'], -32601)

    def test_invalid_request_not_dict(self):
        resp = _handle_request('not a dict')
        self.assertEqual(resp['error']['code'], -32600)

    def test_notification_unknown_method_returns_none(self):
        req = {'jsonrpc': '2.0', 'method': 'unknown'}
        self.assertIsNone(_handle_request(req))

    def test_tools_call_with_all_params(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            req = {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                   'params': {'name': 'arxiv_search',
                              'arguments': {'query': 'test', 'max_results': 5,
                                            'category': 'cs.AI',
                                            'sort_by': 'relevance',
                                            'sort_order': 'ascending'}}}
            resp = _handle_request(req)
        url = mock_urlopen.call_args[0][0].full_url
        self.assertIn('max_results=5', url)
        self.assertIn('cs.AI', url)
        self.assertIn('sortBy=relevance', url)
        self.assertIn('sortOrder=ascending', url)


# ── _run_server 测试 ───────────────────────────────────────
class TestRunServer(unittest.TestCase):
    def test_server_handles_initialize(self):
        request = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'
        })
        stdin = StringIO(request + '\n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        response = json.loads(stdout.getvalue().strip())
        self.assertEqual(response['id'], 1)
        self.assertEqual(response['result']['serverInfo']['name'], SERVER_NAME)

    def test_server_handles_parse_error(self):
        stdin = StringIO('not valid json{{{\n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        response = json.loads(stdout.getvalue().strip())
        self.assertEqual(response['error']['code'], -32700)

    def test_server_skips_empty_lines(self):
        stdin = StringIO('\n\n   \n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        self.assertEqual(stdout.getvalue(), '')


# ── CLI 测试 ──────────────────────────────────────────────
class TestCLI(unittest.TestCase):
    def test_search_command_json_output(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            with patch('sys.argv', ['arxiv_mcp.py', 'search', '--query', 'transformer', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 2)

    def test_search_command_human_readable(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            with patch('sys.argv', ['arxiv_mcp.py', 'search', '--query', 'transformer']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        output = mock_out.getvalue()
        self.assertIn('Attention Is All You Need', output)
        self.assertIn('arxiv.org', output)

    def test_search_command_failure_returns_1(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('fail')):
            with patch('sys.argv', ['arxiv_mcp.py', 'search', '--query', 'test']):
                with patch('sys.stderr', StringIO()):
                    ret = _cli()
        self.assertEqual(ret, 1)

    def test_search_command_with_category(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            with patch('sys.argv', ['arxiv_mcp.py', 'search', '--query', 'test',
                                     '--category', 'cs.AI', '--json']):
                with patch('sys.stdout', StringIO()):
                    ret = _cli()
        self.assertEqual(ret, 0)
        url = mock_urlopen.call_args[0][0].full_url
        self.assertIn('cs.AI', url)

    def test_serve_command_runs_server(self):
        stdin = StringIO(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'
        }) + '\n')
        stdout = StringIO()
        with patch('sys.argv', ['arxiv_mcp.py', 'serve']):
            with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
                ret = _cli()
        self.assertEqual(ret, 0)
        response = json.loads(stdout.getvalue().strip())
        self.assertEqual(response['id'], 1)


# ── 集成测试 ──────────────────────────────────────────────
class TestIntegration(unittest.TestCase):
    def test_mcp_call_to_arxiv_search_end_to_end(self):
        """MCP tools/call → arxiv_search → HTTP → XML 解析 → 返回。"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            req = {'jsonrpc': '2.0', 'id': 100, 'method': 'tools/call',
                   'params': {'name': 'arxiv_search',
                              'arguments': {'query': 'attention transformer',
                                            'max_results': 5}}}
            resp = _handle_request(req)
        self.assertEqual(resp['id'], 100)
        self.assertFalse(resp['result']['isError'])
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 2)
        self.assertEqual(content['results'][0]['title'], 'Attention Is All You Need')

    def test_pii_redacted_in_mcp_response(self):
        """MCP 调用返回的 query 字段不应包含原始 PII。"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_ATOM_XML)
            req = {'jsonrpc': '2.0', 'id': 200, 'method': 'tools/call',
                   'params': {'name': 'arxiv_search',
                              'arguments': {'query': 'call 13800138000 for help'}}}
            resp = _handle_request(req)
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertNotIn('13800138000', content['query'])
        self.assertIn('[REDACTED-PHONE]', content['query'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
