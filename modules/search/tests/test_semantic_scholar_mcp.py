#!/usr/bin/env python3
"""semantic_scholar_mcp.py 单元测试 — Semantic Scholar MCP Server.

覆盖维度：
  - 5 个工具：search_papers / get_paper / get_citations / get_references / get_author
  - 边界输入：空 query / 空 paper_id / 空 author_id
  - HTTP 成功：标准响应 / 空结果 / 部分字段缺失
  - HTTP 失败：HTTPError / URLError / TimeoutError / JSONDecodeError
  - 限流（429）/ 服务器错误（500）
  - MCP 协议：initialize / tools/list（5 个工具）/ tools/call / unknown tool
  - PII 脱敏：query 出境前 redacted
  - CLI：5 个子命令 + serve
  - 常量
"""
import sys
import os
import json
import unittest
import urllib.error
from unittest.mock import patch, MagicMock
from io import StringIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import semantic_scholar_mcp as s2
from semantic_scholar_mcp import (
    search_papers, get_paper, get_citations, get_references, get_author,
    _make_s2_request, _make_tool_list, _handle_request, _run_server, _cli,
    _result_response, _error_response,
    S2_API_URL, DEFAULT_TIMEOUT, DEFAULT_LIMIT, MAX_LIMIT, MAX_QUERY_LEN,
    DEFAULT_PAPER_FIELDS, DEFAULT_CITATION_FIELDS,
    PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION,
)


# ── 测试数据 ──────────────────────────────────────────────
SAMPLE_SEARCH_RESPONSE = {
    'total': 2,
    'data': [
        {
            'paperId': 'abc123',
            'title': 'Attention Is All You Need',
            'authors': [{'name': 'Ashish Vaswani'}, {'name': 'Noam Shazeer'}],
            'abstract': 'The dominant sequence transduction models...',
            'year': 2017,
            'citationCount': 100000,
            'url': 'https://www.semanticscholar.org/paper/abc123',
            'venue': 'NeurIPS',
            'publicationDate': '2017-06-12',
            'externalIds': {'DOI': '10.48550/arXiv.1706.03762', 'ArXiv': '1706.03762'},
        },
        {
            'paperId': 'def456',
            'title': 'BERT',
            'authors': [{'name': 'Jacob Devlin'}],
            'abstract': 'We introduce BERT...',
            'year': 2019,
            'citationCount': 50000,
            'url': 'https://www.semanticscholar.org/paper/def456',
            'venue': 'NAACL',
        },
    ],
}

SAMPLE_PAPER_RESPONSE = {
    'paperId': 'abc123',
    'title': 'Attention Is All You Need',
    'authors': [{'name': 'Ashish Vaswani'}],
    'abstract': '...',
    'year': 2017,
    'citationCount': 100000,
    'url': 'https://www.semanticscholar.org/paper/abc123',
    'venue': 'NeurIPS',
}

SAMPLE_CITATIONS_RESPONSE = {
    'data': [
        {
            'citingPaper': {
                'paperId': 'cit1',
                'title': 'Citing paper 1',
                'year': 2020,
                'citationCount': 10,
                'url': 'https://www.semanticscholar.org/paper/cit1',
                'venue': 'ICML',
            },
            'contexts': ['some context'],
        },
        {
            'citingPaper': {
                'paperId': 'cit2',
                'title': 'Citing paper 2',
                'year': 2021,
                'citationCount': 5,
                'url': 'https://www.semanticscholar.org/paper/cit2',
            },
        },
    ],
    'next': 2,
    'offset': 0,
}

SAMPLE_REFERENCES_RESPONSE = {
    'data': [
        {
            'citedPaper': {
                'paperId': 'ref1',
                'title': 'Referenced paper 1',
                'year': 2014,
                'citationCount': 200,
                'url': 'https://www.semanticscholar.org/paper/ref1',
            },
            'intents': ['methodology'],
        },
    ],
    'next': 1,
}

SAMPLE_AUTHOR_RESPONSE = {
    'authorId': '12345',
    'name': 'Ashish Vaswani',
    'url': 'https://www.semanticscholar.org/author/12345',
    'affiliations': ['Google Brain'],
    'homepage': '',
    'paperCount': 50,
    'citationCount': 100000,
    'hIndex': 25,
}


def _make_urlopen_success(response_dict):
    """构造模拟 urlopen 成功的 context manager。"""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.getcode = MagicMock(return_value=200)
    cm.read = MagicMock(return_value=json.dumps(response_dict).encode('utf-8'))
    return cm


# ── 常量测试 ───────────────────────────────────────────────
class TestConstants(unittest.TestCase):
    def test_s2_api_url_is_https(self):
        self.assertTrue(S2_API_URL.startswith('https://'))

    def test_default_timeout_positive(self):
        self.assertGreater(DEFAULT_TIMEOUT, 0)

    def test_default_limit_positive(self):
        self.assertGreater(DEFAULT_LIMIT, 0)

    def test_max_limit_at_least_50(self):
        self.assertGreaterEqual(MAX_LIMIT, 50)

    def test_max_query_len_at_least_100(self):
        self.assertGreaterEqual(MAX_QUERY_LEN, 100)

    def test_default_paper_fields_has_title(self):
        self.assertIn('title', DEFAULT_PAPER_FIELDS)

    def test_default_paper_fields_has_authors(self):
        self.assertIn('authors', DEFAULT_PAPER_FIELDS)

    def test_protocol_version_is_string(self):
        self.assertIsInstance(PROTOCOL_VERSION, str)

    def test_server_name(self):
        self.assertEqual(SERVER_NAME, 'semantic-scholar-mcp')

    def test_server_version_format(self):
        parts = SERVER_VERSION.split('.')
        self.assertEqual(len(parts), 3)


# ── search_papers 边界测试 ─────────────────────────────────
class TestSearchPapersEdgeCases(unittest.TestCase):
    def test_empty_query_returns_failure(self):
        result = search_papers('')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_whitespace_query_returns_failure(self):
        result = search_papers('   ')
        self.assertFalse(result['success'])

    def test_non_string_query_returns_failure(self):
        result = search_papers(None)
        self.assertFalse(result['success'])
        result = search_papers(123)
        self.assertFalse(result['success'])


# ── search_papers limit 校验 ──────────────────────────────
class TestSearchPapersLimitValidation(unittest.TestCase):
    def test_limit_zero_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', limit=0)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('limit=1', url)

    def test_limit_negative_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', limit=-5)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('limit=1', url)

    def test_limit_above_cap_capped(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', limit=999)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn(f'limit={MAX_LIMIT}', url)

    def test_limit_string_converted(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', limit='5')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('limit=5', url)

    def test_limit_invalid_uses_default(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', limit='abc')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn(f'limit={DEFAULT_LIMIT}', url)


# ── search_papers 成功路径 ─────────────────────────────────
class TestSearchPapersSuccess(unittest.TestCase):
    def test_successful_search_returns_results(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            result = search_papers('transformer attention')

        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['results'][0]['title'], 'Attention Is All You Need')
        # source 应被标记
        self.assertEqual(result['results'][0]['source'], 'semantic_scholar')

    def test_query_redacted_in_result(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            result = search_papers('call 13800138000 for help')
        # 返回的 query 字段应是脱敏后的
        self.assertNotIn('13800138000', result['query'])
        self.assertIn('[REDACTED-PHONE]', result['query'])

    def test_pii_not_in_request_url(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('email me at john@example.com')
            url = mock_urlopen.call_args[0][0].full_url
            # PII 不应在 URL 中
            self.assertNotIn('john@example.com', url)

    def test_year_filter_added_to_url(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', year='2020-2023')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('year=2020-2023', url)

    def test_venue_filter_added_to_url(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test', venue='NeurIPS')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('venue=NeurIPS', url)

    def test_request_uses_get_method(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.get_method(), 'GET')

    def test_request_url_uses_s2_api(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('test')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertTrue(url.startswith(f'{S2_API_URL}/paper/search'))

    def test_no_redact_flag_preserves_pii(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            search_papers('13800138000', redact=False)
            url = mock_urlopen.call_args[0][0].full_url
            # 不脱敏时 PII 应保留在 URL
            self.assertIn('13800138000', url)

    def test_empty_data_returns_zero_results(self):
        empty_response = {'total': 0, 'data': []}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(empty_response)
            result = search_papers('nonexistent')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['total'], 0)

    def test_non_list_data_treated_as_empty(self):
        bad_response = {'total': 1, 'data': 'not a list'}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(bad_response)
            result = search_papers('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)


# ── HTTP 失败路径 ─────────────────────────────────────────
class TestSearchPapersHTTPErrors(unittest.TestCase):
    def test_http_error_returns_failure(self):
        error = urllib.error.HTTPError(
            url=S2_API_URL, code=429, msg='Too Many Requests',
            hdrs=None, fp=None,
        )
        error.read = MagicMock(return_value=b'{"error": "rate limited"}')
        with patch('urllib.request.urlopen', side_effect=error):
            result = search_papers('test')
        self.assertFalse(result['success'])
        self.assertIn('429', result['error'])

    def test_url_error_returns_failure(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('DNS failed')):
            result = search_papers('test')
        self.assertFalse(result['success'])
        self.assertIn('URL error', result['error'])

    def test_url_error_timeout_treated_as_timeout(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('timed out')):
            result = search_papers('test')
        self.assertFalse(result['success'])
        self.assertIn('Timeout', result['error'])

    def test_timeout_returns_failure(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError()):
            result = search_papers('test')
        self.assertFalse(result['success'])
        self.assertIn('Timeout', result['error'])

    def test_json_decode_error_returns_failure(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.getcode = MagicMock(return_value=200)
        cm.read = MagicMock(return_value=b'not valid json')
        with patch('urllib.request.urlopen', return_value=cm):
            result = search_papers('test')
        self.assertFalse(result['success'])

    def test_generic_exception_returns_failure(self):
        with patch('urllib.request.urlopen',
                   side_effect=RuntimeError('unexpected')):
            result = search_papers('test')
        self.assertFalse(result['success'])
        self.assertIn('RuntimeError', result['error'])

    def test_non_2xx_status_returns_failure(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.getcode = MagicMock(return_value=500)
        cm.read = MagicMock(return_value=b'{}')
        with patch('urllib.request.urlopen', return_value=cm):
            result = search_papers('test')
        self.assertFalse(result['success'])
        self.assertIn('HTTP 500', result['error'])


# ── get_paper 测试 ────────────────────────────────────────
class TestGetPaper(unittest.TestCase):
    def test_empty_paper_id_returns_failure(self):
        result = get_paper('')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_non_string_paper_id_returns_failure(self):
        result = get_paper(None)
        self.assertFalse(result['success'])

    def test_successful_get_paper(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_PAPER_RESPONSE)
            result = get_paper('abc123')
        self.assertTrue(result['success'])
        self.assertEqual(result['paper']['title'], 'Attention Is All You Need')
        self.assertEqual(result['paper']['source'], 'semantic_scholar')

    def test_url_contains_paper_id(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_PAPER_RESPONSE)
            get_paper('10.1145/3292500.3330701')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('paper/', url)

    def test_doi_with_slashes_url_encoded(self):
        # DOI 含 / 需要正确编码
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_PAPER_RESPONSE)
            get_paper('10.1145/3292500.3330701')
            url = mock_urlopen.call_args[0][0].full_url
            # / 应被编码为 %2F
            self.assertIn('%2F', url)

    def test_http_error_returns_failure(self):
        error = urllib.error.HTTPError(
            url=S2_API_URL, code=404, msg='Not Found',
            hdrs=None, fp=None,
        )
        error.read = MagicMock(return_value=b'{"error": "not found"}')
        with patch('urllib.request.urlopen', side_effect=error):
            result = get_paper('nonexistent')
        self.assertFalse(result['success'])
        self.assertIn('404', result['error'])


# ── get_citations 测试 ─────────────────────────────────────
class TestGetCitations(unittest.TestCase):
    def test_empty_paper_id_returns_failure(self):
        result = get_citations('')
        self.assertFalse(result['success'])

    def test_successful_get_citations(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            result = get_citations('abc123')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['citations'][0]['title'], 'Citing paper 1')
        self.assertEqual(result['citations'][0]['source'], 'semantic_scholar')
        self.assertEqual(result['next_offset'], 2)

    def test_limit_zero_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            get_citations('test', limit=0)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('limit=1', url)

    def test_limit_above_cap_capped(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            get_citations('test', limit=999)
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn(f'limit={MAX_LIMIT}', url)

    def test_url_endpoint_is_citations(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            get_citations('abc123')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('/citations', url)

    def test_non_dict_citing_paper_skipped(self):
        bad_response = {'data': ['not a dict', {'citingPaper': {'title': 'valid'}}]}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(bad_response)
            result = get_citations('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 1)

    def test_missing_citing_paper_field_skipped(self):
        bad_response = {'data': [{'notCitingPaper': {}}]}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(bad_response)
            result = get_citations('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)


# ── get_references 测试 ────────────────────────────────────
class TestGetReferences(unittest.TestCase):
    def test_empty_paper_id_returns_failure(self):
        result = get_references('')
        self.assertFalse(result['success'])

    def test_successful_get_references(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_REFERENCES_RESPONSE)
            result = get_references('abc123')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['references'][0]['title'], 'Referenced paper 1')
        self.assertEqual(result['references'][0]['source'], 'semantic_scholar')

    def test_url_endpoint_is_references(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_REFERENCES_RESPONSE)
            get_references('abc123')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('/references', url)

    def test_non_dict_cited_paper_skipped(self):
        bad_response = {'data': [{'citedPaper': 'not a dict'}]}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(bad_response)
            result = get_references('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)


# ── get_author 测试 ───────────────────────────────────────
class TestGetAuthor(unittest.TestCase):
    def test_empty_author_id_returns_failure(self):
        result = get_author('')
        self.assertFalse(result['success'])

    def test_successful_get_author(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_AUTHOR_RESPONSE)
            result = get_author('12345')
        self.assertTrue(result['success'])
        self.assertEqual(result['author']['name'], 'Ashish Vaswani')
        self.assertEqual(result['author']['hIndex'], 25)

    def test_url_endpoint_is_author(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_AUTHOR_RESPONSE)
            get_author('12345')
            url = mock_urlopen.call_args[0][0].full_url
            self.assertIn('/author/', url)

    def test_http_error_returns_failure(self):
        error = urllib.error.HTTPError(
            url=S2_API_URL, code=404, msg='Not Found',
            hdrs=None, fp=None,
        )
        error.read = MagicMock(return_value=b'{}')
        with patch('urllib.request.urlopen', side_effect=error):
            result = get_author('nonexistent')
        self.assertFalse(result['success'])
        self.assertIn('404', result['error'])


# ── MCP 协议测试 ──────────────────────────────────────────
class TestMCPProtocol(unittest.TestCase):
    def test_make_tool_list_returns_5_tools(self):
        result = _make_tool_list()
        self.assertEqual(len(result['tools']), 5)
        tool_names = [t['name'] for t in result['tools']]
        self.assertIn('search_papers', tool_names)
        self.assertIn('get_paper', tool_names)
        self.assertIn('get_citations', tool_names)
        self.assertIn('get_references', tool_names)
        self.assertIn('get_author', tool_names)

    def test_search_papers_tool_has_input_schema(self):
        result = _make_tool_list()
        tool = next(t for t in result['tools'] if t['name'] == 'search_papers')
        schema = tool['inputSchema']
        self.assertEqual(schema['type'], 'object')
        self.assertIn('query', schema['properties'])
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

    def test_tools_call_search_papers_empty_query(self):
        req = {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
               'params': {'name': 'search_papers', 'arguments': {'query': ''}}}
        resp = _handle_request(req)
        self.assertTrue(resp['result']['isError'])

    def test_tools_call_search_papers_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                   'params': {'name': 'search_papers',
                              'arguments': {'query': 'transformer'}}}
            resp = _handle_request(req)
        self.assertFalse(resp['result']['isError'])
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 2)

    def test_tools_call_get_paper_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_PAPER_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
                   'params': {'name': 'get_paper',
                              'arguments': {'paper_id': 'abc123'}}}
            resp = _handle_request(req)
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['paper']['title'], 'Attention Is All You Need')

    def test_tools_call_get_paper_empty_id(self):
        req = {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
               'params': {'name': 'get_paper', 'arguments': {'paper_id': ''}}}
        resp = _handle_request(req)
        self.assertTrue(resp['result']['isError'])

    def test_tools_call_get_citations_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 8, 'method': 'tools/call',
                   'params': {'name': 'get_citations',
                              'arguments': {'paper_id': 'abc123'}}}
            resp = _handle_request(req)
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 2)

    def test_tools_call_get_references_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_REFERENCES_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call',
                   'params': {'name': 'get_references',
                              'arguments': {'paper_id': 'abc123'}}}
            resp = _handle_request(req)
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 1)

    def test_tools_call_get_author_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_AUTHOR_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 10, 'method': 'tools/call',
                   'params': {'name': 'get_author',
                              'arguments': {'author_id': '12345'}}}
            resp = _handle_request(req)
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['author']['name'], 'Ashish Vaswani')

    def test_unknown_method_returns_error(self):
        req = {'jsonrpc': '2.0', 'id': 11, 'method': 'nonexistent'}
        resp = _handle_request(req)
        self.assertEqual(resp['error']['code'], -32601)

    def test_invalid_request_not_dict(self):
        resp = _handle_request('not a dict')
        self.assertEqual(resp['error']['code'], -32600)

    def test_notification_unknown_method_returns_none(self):
        req = {'jsonrpc': '2.0', 'method': 'unknown'}
        self.assertIsNone(_handle_request(req))


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
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            with patch('sys.argv', ['s2.py', 'search', '--query', 'transformer', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])

    def test_search_command_human_readable(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            with patch('sys.argv', ['s2.py', 'search', '--query', 'transformer']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        output = mock_out.getvalue()
        self.assertIn('Attention Is All You Need', output)

    def test_search_command_failure_returns_1(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('fail')):
            with patch('sys.argv', ['s2.py', 'search', '--query', 'test']):
                with patch('sys.stderr', StringIO()):
                    ret = _cli()
        self.assertEqual(ret, 1)

    def test_paper_command_json(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_PAPER_RESPONSE)
            with patch('sys.argv', ['s2.py', 'paper', '--id', 'abc123', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])

    def test_citations_command_json(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            with patch('sys.argv', ['s2.py', 'citations', '--paper-id', 'abc', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])

    def test_references_command_json(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_REFERENCES_RESPONSE)
            with patch('sys.argv', ['s2.py', 'references', '--paper-id', 'abc', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])

    def test_author_command_json(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_AUTHOR_RESPONSE)
            with patch('sys.argv', ['s2.py', 'author', '--author-id', '12345', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])

    def test_serve_command_runs_server(self):
        stdin = StringIO(json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'
        }) + '\n')
        stdout = StringIO()
        with patch('sys.argv', ['s2.py', 'serve']):
            with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
                ret = _cli()
        self.assertEqual(ret, 0)
        response = json.loads(stdout.getvalue().strip())
        self.assertEqual(response['id'], 1)


# ── 集成测试 ──────────────────────────────────────────────
class TestIntegration(unittest.TestCase):
    def test_mcp_search_papers_end_to_end(self):
        """MCP tools/call → search_papers → HTTP → JSON 解析 → 返回。"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 100, 'method': 'tools/call',
                   'params': {'name': 'search_papers',
                              'arguments': {'query': 'transformer',
                                            'limit': 5}}}
            resp = _handle_request(req)
        self.assertEqual(resp['id'], 100)
        self.assertFalse(resp['result']['isError'])
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 2)
        self.assertEqual(content['results'][0]['title'], 'Attention Is All You Need')

    def test_pii_redacted_in_mcp_response(self):
        """PII 不应在 MCP 返回中泄露。"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_SEARCH_RESPONSE)
            req = {'jsonrpc': '2.0', 'id': 200, 'method': 'tools/call',
                   'params': {'name': 'search_papers',
                              'arguments': {'query': 'call 13800138000'}}}
            resp = _handle_request(req)
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertNotIn('13800138000', content['query'])
        self.assertIn('[REDACTED-PHONE]', content['query'])

    def test_citation_network_traversal(self):
        """citation 网络：paper → citations → references。"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            # 第一次调用 get_paper
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_PAPER_RESPONSE)
            paper_result = get_paper('abc123')
            self.assertTrue(paper_result['success'])
            self.assertEqual(paper_result['paper']['title'], 'Attention Is All You Need')

            # 第二次调用 get_citations
            mock_urlopen.return_value = _make_urlopen_success(SAMPLE_CITATIONS_RESPONSE)
            cit_result = get_citations('abc123')
            self.assertTrue(cit_result['success'])
            self.assertEqual(cit_result['count'], 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
