#!/usr/bin/env python3
"""serper_mcp.py 单元测试 — Serper API MCP Server。

覆盖维度：
  - 边界输入：空 query / 非 str / 超长 query / num 越界
  - API key 缺失：env 未设置 + 显式 None/空
  - HTTP 成功：标准响应解析 / 无 organic 字段 / 截断到 num
  - HTTP 失败：HTTPError / URLError / TimeoutError / JSONDecodeError / 通用异常
  - API key 泄露防护：错误 body 中 key 被替换
  - PII 脱敏：默认启用 / 显式关闭
  - MCP 协议：initialize / tools/list / tools/call / unknown method
  - MCP 错误处理：parse error / invalid request / notification
  - 批量请求：JSON-RPC 2.0 batch
  - CLI：search 子命令 / serve 子命令 / 错误处理
  - 常量：URL / timeout / num 上限
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

import serper_mcp
from serper_mcp import (
    serper_search, _make_tool_list, _handle_request, _run_server, _cli,
    SERPER_API_URL, DEFAULT_TIMEOUT, DEFAULT_NUM, MAX_NUM, MAX_QUERY_LEN,
    PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION,
)


# ── 辅助函数 ──────────────────────────────────────────────
def _make_response(organic=None, search_params=None):
    """构造 Serper API 成功响应体。"""
    return {
        'organic': organic or [],
        'searchParameters': search_params or {'q': 'test', 'num': 10},
    }


def _make_urlopen_success(response_dict):
    """构造模拟 urlopen 成功的 context manager。"""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.getcode = MagicMock(return_value=200)
    cm.read = MagicMock(return_value=json.dumps(response_dict).encode('utf-8'))
    return cm


# ── Constants 测试 ─────────────────────────────────────────
class TestConstants(unittest.TestCase):
    def test_serper_api_url_is_https(self):
        self.assertTrue(serper_mcp.SERPER_API_URL.startswith('https://'))

    def test_default_timeout_positive(self):
        self.assertGreater(DEFAULT_TIMEOUT, 0)

    def test_default_num_positive(self):
        self.assertGreater(DEFAULT_NUM, 0)

    def test_max_num_at_least_50(self):
        self.assertGreaterEqual(MAX_NUM, 50)

    def test_max_query_len_at_least_100(self):
        self.assertGreaterEqual(MAX_QUERY_LEN, 100)

    def test_protocol_version_is_string(self):
        self.assertIsInstance(PROTOCOL_VERSION, str)
        self.assertGreater(len(PROTOCOL_VERSION), 0)

    def test_server_name_is_string(self):
        self.assertEqual(SERVER_NAME, 'serper-mcp')

    def test_server_version_format(self):
        # Format: major.minor.patch
        parts = SERVER_VERSION.split('.')
        self.assertEqual(len(parts), 3)


# ── serper_search 边界输入测试 ─────────────────────────────
class TestSerperSearchEdgeCases(unittest.TestCase):
    def setUp(self):
        # 确保所有测试默认有 API key
        os.environ['SERPER_API_KEY'] = 'test-key-12345'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_empty_query_returns_failure(self):
        result = serper_search('')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_whitespace_query_returns_failure(self):
        result = serper_search('   ')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_non_string_query_returns_failure(self):
        result = serper_search(None)
        self.assertFalse(result['success'])
        result = serper_search(123)
        self.assertFalse(result['success'])

    def test_no_api_key_returns_failure(self):
        os.environ.pop('SERPER_API_KEY', None)
        result = serper_search('test query', api_key='')
        self.assertFalse(result['success'])
        self.assertIn('SERPER_API_KEY', result['error'])

    def test_no_env_api_key_returns_failure(self):
        os.environ.pop('SERPER_API_KEY', None)
        result = serper_search('test query')
        self.assertFalse(result['success'])
        self.assertIn('SERPER_API_KEY', result['error'])


# ── num 参数校验 ──────────────────────────────────────────
class TestSerperSearchNumValidation(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_num_zero_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', num=0)
            call_args = mock_urlopen.call_args[0][0]
            body = json.loads(call_args.data.decode('utf-8'))
            self.assertEqual(body['num'], 1)

    def test_num_negative_becomes_one(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', num=-5)
            call_args = mock_urlopen.call_args[0][0]
            body = json.loads(call_args.data.decode('utf-8'))
            self.assertEqual(body['num'], 1)

    def test_num_above_max_capped(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', num=999)
            call_args = mock_urlopen.call_args[0][0]
            body = json.loads(call_args.data.decode('utf-8'))
            self.assertEqual(body['num'], MAX_NUM)

    def test_num_string_converted(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', num='5')
            call_args = mock_urlopen.call_args[0][0]
            body = json.loads(call_args.data.decode('utf-8'))
            self.assertEqual(body['num'], 5)

    def test_num_invalid_string_uses_default(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', num='abc')
            call_args = mock_urlopen.call_args[0][0]
            body = json.loads(call_args.data.decode('utf-8'))
            self.assertEqual(body['num'], DEFAULT_NUM)


# ── query 截断 ─────────────────────────────────────────────
class TestQueryTruncation(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_long_query_truncated(self):
        long_query = 'a' * (MAX_QUERY_LEN + 100)
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search(long_query)
            call_args = mock_urlopen.call_args[0][0]
            body = json.loads(call_args.data.decode('utf-8'))
            self.assertEqual(len(body['q']), MAX_QUERY_LEN)

    def test_sensitive_number_crossing_limit_is_redacted_before_truncation(self):
        query = 'x' * (MAX_QUERY_LEN - 6) + ' 13800138000'
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search(query)
            request = mock_urlopen.call_args[0][0]
            sent = json.loads(request.data.decode('utf-8'))['q']
        self.assertLessEqual(len(sent), MAX_QUERY_LEN)
        self.assertNotIn('13800', sent)
        self.assertNotIn('13800138000', sent)


# ── HTTP 成功路径 ─────────────────────────────────────────
class TestSerperSearchSuccess(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key-12345'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_successful_search_returns_results(self):
        organic = [
            {'title': 'Result 1', 'link': 'https://example.com/1',
             'snippet': 'Snippet 1', 'position': 1},
            {'title': 'Result 2', 'link': 'https://example.com/2',
             'snippet': 'Snippet 2', 'position': 2},
        ]
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response(organic=organic))
            result = serper_search('test query')

        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['results'][0]['title'], 'Result 1')
        self.assertEqual(result['results'][0]['url'], 'https://example.com/1')
        self.assertEqual(result['results'][0]['source'], 'serper')
        self.assertEqual(result['results'][0]['position'], 1)

    def test_results_truncated_to_num(self):
        organic = [{'title': f'R{i}', 'link': f'https://e.com/{i}',
                    'snippet': f'S{i}', 'position': i} for i in range(1, 21)]
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response(organic=organic))
            result = serper_search('test', num=5)
        self.assertEqual(result['count'], 5)
        self.assertEqual(len(result['results']), 5)

    def test_empty_organic_returns_zero_results(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response(organic=[]))
            result = serper_search('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['results'], [])

    def test_missing_organic_field_treated_as_empty(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success({})
            result = serper_search('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)

    def test_non_dict_organic_items_skipped(self):
        organic = [
            'not a dict',
            {'title': 'Valid', 'link': 'https://valid.com', 'snippet': 's', 'position': 1},
            None,
            42,
        ]
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response(organic=organic))
            result = serper_search('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['results'][0]['title'], 'Valid')

    def test_non_list_organic_treated_as_empty(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success({'organic': 'not a list'})
            result = serper_search('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)

    def test_search_parameters_returned(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_response(search_params={'q': 'test', 'gl': 'us'})
            )
            result = serper_search('test')
        self.assertIn('search_parameters', result)
        self.assertEqual(result['search_parameters']['q'], 'test')

    def test_missing_fields_default_to_empty_string(self):
        organic = [{}]  # 完全空的 dict
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response(organic=organic))
            result = serper_search('test')
        self.assertTrue(result['success'])
        self.assertEqual(result['results'][0]['title'], '')
        self.assertEqual(result['results'][0]['url'], '')
        self.assertEqual(result['results'][0]['snippet'], '')


# ── HTTP 失败路径 ─────────────────────────────────────────
class TestSerperSearchHTTPErrors(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key-12345'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_http_error_returns_failure(self):
        error = urllib.error.HTTPError(
            url='https://google.serper.dev/search',
            code=401,
            msg='Unauthorized',
            hdrs=None,
            fp=None,
        )
        # 模拟错误响应 body
        error.read = MagicMock(return_value=b'{"error": "Invalid API key"}')
        with patch('urllib.request.urlopen', side_effect=error):
            result = serper_search('test')
        self.assertFalse(result['success'])
        self.assertIn('401', result['error'])
        self.assertIn('error_body', result)

    def test_http_error_body_with_api_key_redacted(self):
        api_key = 'secret-key-xyz'
        error = urllib.error.HTTPError(
            url='https://google.serper.dev/search',
            code=403,
            msg='Forbidden',
            hdrs=None,
            fp=None,
        )
        # 错误 body 中包含 API key
        error_body = f'{{"error": "key {api_key} is invalid"}}'
        error.read = MagicMock(return_value=error_body.encode('utf-8'))
        with patch('urllib.request.urlopen', side_effect=error):
            result = serper_search('test', api_key=api_key)
        self.assertFalse(result['success'])
        # API key 必须被替换
        self.assertNotIn(api_key, result.get('error_body', ''))
        self.assertIn('[REDACTED-KEY]', result['error_body'])

    def test_url_error_returns_failure(self):
        error = urllib.error.URLError(reason='DNS resolution failed')
        with patch('urllib.request.urlopen', side_effect=error):
            result = serper_search('test')
        self.assertFalse(result['success'])
        self.assertIn('URL error', result['error'])

    def test_timeout_returns_failure(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError()):
            result = serper_search('test')
        self.assertFalse(result['success'])
        self.assertIn('Timeout', result['error'])

    def test_json_decode_error_returns_failure(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.getcode = MagicMock(return_value=200)
        cm.read = MagicMock(return_value=b'not valid json{{{')
        with patch('urllib.request.urlopen', return_value=cm):
            result = serper_search('test')
        self.assertFalse(result['success'])
        self.assertIn('parse failed', result['error'])

    def test_generic_exception_returns_failure(self):
        with patch('urllib.request.urlopen', side_effect=RuntimeError('unexpected')):
            result = serper_search('test')
        self.assertFalse(result['success'])
        self.assertIn('RuntimeError', result['error'])

    def test_non_2xx_status_returns_failure(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.getcode = MagicMock(return_value=302)
        cm.read = MagicMock(return_value=b'{}')
        with patch('urllib.request.urlopen', return_value=cm):
            result = serper_search('test')
        self.assertFalse(result['success'])
        self.assertIn('HTTP 302', result['error'])


# ── PII 脱敏测试 ───────────────────────────────────────────
class TestPIIRedaction(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_pii_redacted_by_default(self):
        captured_query = []

        def capture_query(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured_query.append(body['q'])
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture_query):
            result = serper_search('contact 13800138000 for info')
        self.assertTrue(result['success'])
        # 原始 PII 不应出现在发送给 Serper 的 query 中
        self.assertNotIn('13800138000', captured_query[0])
        self.assertIn('[REDACTED-PHONE]', captured_query[0])

    def test_pii_redaction_can_be_disabled(self):
        captured_query = []

        def capture_query(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured_query.append(body['q'])
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture_query):
            result = serper_search('contact 13800138000', redact=False)
        # redact=False 时，PII 应该被原样发送
        self.assertIn('13800138000', captured_query[0])

    def test_email_redacted(self):
        captured_query = []

        def capture_query(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured_query.append(body['q'])
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture_query):
            serper_search('email me at john@example.com')
        self.assertNotIn('john@example.com', captured_query[0])
        self.assertIn('[REDACTED-EMAIL]', captured_query[0])


# ── HTTP 请求构造测试 ─────────────────────────────────────
class TestHTTPRequestConstruction(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key-67890'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_request_uses_post_method(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.get_method(), 'POST')

    def test_request_url_is_serper_endpoint(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.full_url, SERPER_API_URL)

    def test_request_has_api_key_header(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', api_key='my-secret-key')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.headers['X-api-key'], 'my-secret-key')

    def test_request_has_content_type_json(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test')
            req = mock_urlopen.call_args[0][0]
            # urllib normalizes header names
            self.assertIn('Content-type', req.headers)
            self.assertEqual(req.headers['Content-type'], 'application/json')

    def test_request_body_contains_query_and_num(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('my query', num=15, gl='cn', hl='zh')
            req = mock_urlopen.call_args[0][0]
            body = json.loads(req.data.decode('utf-8'))
            self.assertEqual(body['q'], 'my query')
            self.assertEqual(body['num'], 15)
            self.assertEqual(body['gl'], 'cn')
            self.assertEqual(body['hl'], 'zh')

    def test_explicit_api_key_overrides_env(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response())
            serper_search('test', api_key='explicit-key')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.headers['X-api-key'], 'explicit-key')


# ── MCP 协议：tools/list ──────────────────────────────────
class TestMCPToolList(unittest.TestCase):
    def test_make_tool_list_returns_dict_with_tools(self):
        result = _make_tool_list()
        self.assertIn('tools', result)
        self.assertIsInstance(result['tools'], list)

    def test_make_tool_list_has_serper_search(self):
        result = _make_tool_list()
        self.assertEqual(result['tools'][0]['name'], 'serper_search')

    def test_make_tool_list_has_input_schema(self):
        result = _make_tool_list()
        tool = result['tools'][0]
        self.assertIn('inputSchema', tool)
        schema = tool['inputSchema']
        self.assertEqual(schema['type'], 'object')
        self.assertIn('query', schema['properties'])
        self.assertIn('num', schema['properties'])
        self.assertIn('gl', schema['properties'])
        self.assertIn('hl', schema['properties'])

    def test_make_tool_list_query_required(self):
        result = _make_tool_list()
        schema = result['tools'][0]['inputSchema']
        self.assertIn('query', schema['required'])

    def test_make_tool_list_has_description(self):
        result = _make_tool_list()
        tool = result['tools'][0]
        self.assertIn('description', tool)
        self.assertIsInstance(tool['description'], str)
        self.assertGreater(len(tool['description']), 10)


# ── MCP 协议：_handle_request ─────────────────────────────
class TestMCPHandleRequest(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_initialize_returns_protocol_version(self):
        req = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        resp = _handle_request(req)
        self.assertEqual(resp['jsonrpc'], '2.0')
        self.assertEqual(resp['id'], 1)
        self.assertEqual(resp['result']['protocolVersion'], PROTOCOL_VERSION)
        self.assertEqual(resp['result']['serverInfo']['name'], SERVER_NAME)

    def test_initialize_capabilities_has_tools(self):
        req = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
        resp = _handle_request(req)
        self.assertIn('tools', resp['result']['capabilities'])

    def test_notifications_initialized_returns_none(self):
        req = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
        resp = _handle_request(req)
        self.assertIsNone(resp)

    def test_tools_list_returns_tools(self):
        req = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}
        resp = _handle_request(req)
        self.assertEqual(resp['id'], 2)
        self.assertIn('tools', resp['result'])
        self.assertGreater(len(resp['result']['tools']), 0)

    def test_tools_call_unknown_tool_returns_error(self):
        req = {
            'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'unknown_tool', 'arguments': {}}
        }
        resp = _handle_request(req)
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)

    def test_tools_call_empty_query_returns_error_content(self):
        req = {
            'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
            'params': {'name': 'serper_search', 'arguments': {'query': ''}}
        }
        resp = _handle_request(req)
        self.assertTrue(resp['result']['isError'])
        content_text = resp['result']['content'][0]['text']
        content = json.loads(content_text)
        self.assertFalse(content['success'])

    def test_tools_call_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_response([{'title': 'R1', 'link': 'https://e.com', 'snippet': 's', 'position': 1}])
            )
            req = {
                'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                'params': {
                    'name': 'serper_search',
                    'arguments': {'query': 'test query'}
                }
            }
            resp = _handle_request(req)
        self.assertFalse(resp['result']['isError'])
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 1)

    def test_unknown_method_returns_error(self):
        req = {'jsonrpc': '2.0', 'id': 6, 'method': 'nonexistent/method'}
        resp = _handle_request(req)
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)

    def test_invalid_request_not_dict_returns_error(self):
        resp = _handle_request('not a dict')
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32600)

    def test_notification_unknown_method_returns_none(self):
        # 无 id 的请求是 notification，即使方法未知也不返回响应
        req = {'jsonrpc': '2.0', 'method': 'nonexistent'}
        resp = _handle_request(req)
        self.assertIsNone(resp)

    def test_tools_call_with_num_gl_hl(self):
        captured = []

        def capture(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured.append(body)
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture):
            req = {
                'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                'params': {
                    'name': 'serper_search',
                    'arguments': {'query': 'test', 'num': 5, 'gl': 'cn', 'hl': 'zh'}
                }
            }
            _handle_request(req)
        self.assertEqual(captured[0]['num'], 5)
        self.assertEqual(captured[0]['gl'], 'cn')
        self.assertEqual(captured[0]['hl'], 'zh')


# ── MCP 批量请求 ──────────────────────────────────────────
class TestMCPBatchRequest(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_batch_request_returns_array_of_responses(self):
        batch = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        ]
        responses = []
        for single in batch:
            resp = _handle_request(single)
            if resp is not None:
                responses.append(resp)
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]['id'], 1)
        self.assertEqual(responses[1]['id'], 2)

    def test_batch_with_notification_omits_response(self):
        # notification 不返回响应
        items = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        ]
        responses = []
        for single in items:
            resp = _handle_request(single)
            if resp is not None:
                responses.append(resp)
        self.assertEqual(len(responses), 1)


# ── _run_server (stdin/stdout 模拟) ──────────────────────
class TestRunServer(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_server_handles_initialize(self):
        request = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}
        })
        stdin = StringIO(request + '\n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        output = stdout.getvalue().strip()
        self.assertTrue(output)
        response = json.loads(output)
        self.assertEqual(response['id'], 1)
        self.assertEqual(response['result']['serverInfo']['name'], SERVER_NAME)

    def test_server_handles_parse_error(self):
        stdin = StringIO('not valid json{{{\n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        output = stdout.getvalue().strip()
        response = json.loads(output)
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], -32700)

    def test_server_skips_empty_lines(self):
        stdin = StringIO('\n\n   \n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        self.assertEqual(stdout.getvalue(), '')

    def test_server_handles_multiple_requests(self):
        req1 = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        req2 = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        stdin = StringIO(req1 + '\n' + req2 + '\n')
        stdout = StringIO()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            _run_server()
        lines = [l for l in stdout.getvalue().split('\n') if l.strip()]
        self.assertEqual(len(lines), 2)


# ── CLI 测试 ──────────────────────────────────────────────
class TestCLI(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_search_command_json_output(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_response([{'title': 'R1', 'link': 'https://e.com', 'snippet': 's', 'position': 1}])
            )
            args = ['search', '--query', 'test query', '--json']
            with patch('sys.argv', ['serper_mcp.py'] + args):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        output = mock_out.getvalue()
        parsed = json.loads(output)
        self.assertTrue(parsed['success'])
        self.assertEqual(parsed['count'], 1)

    def test_search_command_human_readable(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_response([{'title': 'Test Title', 'link': 'https://e.com', 'snippet': 'snippet', 'position': 1}])
            )
            args = ['search', '--query', 'test query']
            with patch('sys.argv', ['serper_mcp.py'] + args):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = _cli()
        self.assertEqual(ret, 0)
        output = mock_out.getvalue()
        self.assertIn('Test Title', output)
        self.assertIn('https://e.com', output)

    def test_search_command_failure_returns_1(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('DNS failed')):
            args = ['search', '--query', 'test query']
            with patch('sys.argv', ['serper_mcp.py'] + args):
                with patch('sys.stderr', StringIO()):
                    ret = _cli()
        self.assertEqual(ret, 1)

    def test_search_command_with_num_gl_hl(self):
        captured = []

        def capture(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured.append(body)
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture):
            args = ['search', '--query', 'test', '--num', '5', '--gl', 'cn', '--hl', 'zh', '--json']
            with patch('sys.argv', ['serper_mcp.py'] + args):
                with patch('sys.stdout', StringIO()):
                    ret = _cli()
        self.assertEqual(ret, 0)
        self.assertEqual(captured[0]['num'], 5)
        self.assertEqual(captured[0]['gl'], 'cn')
        self.assertEqual(captured[0]['hl'], 'zh')

    def test_search_command_no_redact_flag(self):
        captured = []

        def capture(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured.append(body)
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture):
            args = ['search', '--query', '13800138000', '--no-redact', '--json']
            with patch('sys.argv', ['serper_mcp.py'] + args):
                with patch('sys.stdout', StringIO()):
                    _cli()
        # --no-redact 时 PII 应保留
        self.assertIn('13800138000', captured[0]['q'])

    def test_serve_command_runs_server(self):
        # serve 子命令应调用 _run_server
        stdin = StringIO(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n')
        stdout = StringIO()
        with patch('sys.argv', ['serper_mcp.py', 'serve']):
            with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
                ret = _cli()
        self.assertEqual(ret, 0)
        response = json.loads(stdout.getvalue().strip())
        self.assertEqual(response['id'], 1)


# ── 集成 / 端到端 ────────────────────────────────────────
class TestIntegration(unittest.TestCase):
    def setUp(self):
        os.environ['SERPER_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('SERPER_API_KEY', None)

    def test_mcp_call_to_serper_search_end_to_end(self):
        """MCP tools/call → serper_search → HTTP → 解析 → 返回。"""
        organic = [
            {'title': 'React Docs', 'link': 'https://react.dev',
             'snippet': 'React documentation', 'position': 1},
        ]
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(_make_response(organic=organic))
            req = {
                'jsonrpc': '2.0', 'id': 100, 'method': 'tools/call',
                'params': {
                    'name': 'serper_search',
                    'arguments': {'query': 'React docs', 'num': 5}
                }
            }
            resp = _handle_request(req)

        self.assertEqual(resp['id'], 100)
        self.assertFalse(resp['result']['isError'])
        content = json.loads(resp['result']['content'][0]['text'])
        self.assertTrue(content['success'])
        self.assertEqual(content['count'], 1)
        self.assertEqual(content['results'][0]['title'], 'React Docs')

    def test_pii_redacted_in_mcp_response(self):
        """MCP 调用返回的 query 字段不应包含原始 PII。"""
        captured = []

        def capture(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured.append(body)
            return _make_urlopen_success(_make_response())

        with patch('urllib.request.urlopen', side_effect=capture):
            req = {
                'jsonrpc': '2.0', 'id': 200, 'method': 'tools/call',
                'params': {
                    'name': 'serper_search',
                    'arguments': {'query': 'call me at 13800138000'}
                }
            }
            resp = _handle_request(req)

        content = json.loads(resp['result']['content'][0]['text'])
        # query 字段已被脱敏
        self.assertNotIn('13800138000', content['query'])
        self.assertIn('[REDACTED-PHONE]', content['query'])
        # 发送给 Serper 的请求 body 也已脱敏
        self.assertNotIn('13800138000', captured[0]['q'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
