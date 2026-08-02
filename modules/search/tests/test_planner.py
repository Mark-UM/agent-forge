#!/usr/bin/env python3
"""planner.py 单元测试 — JSON 解析 + 降级链 + CLI。

覆盖维度：
  - _strip_markdown_fences: ```json / ``` / 无 fence / 多余空行
  - _parse_planner_json: 正常 / 字段缺失 / 类型错误 / sub_queries 截断
  - _call_planner_api: mock HTTP 成功 + HTTP 错误 + 超时 + 无 API key
  - plan_query: 完整降级链（API 失败 → 原查询）
  - 边界输入：空 query / None / 非字符串
  - CLI: plan / show-prompt
"""
import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import planner
from planner import (
    _strip_markdown_fences, _parse_planner_json, _call_planner_api,
    plan_query, _load_prompt_template, MAX_SUB_QUERIES,
)


class TestStripMarkdownFences(unittest.TestCase):
    """markdown fence 去除测试。"""

    def test_plain_json_unchanged(self):
        text = '{"key": "value"}'
        self.assertEqual(_strip_markdown_fences(text), text)

    def test_json_fence_stripped(self):
        text = '```json\n{"key": "value"}\n```'
        self.assertEqual(_strip_markdown_fences(text), '{"key": "value"}')

    def test_plain_fence_stripped(self):
        text = '```\n{"key": "value"}\n```'
        self.assertEqual(_strip_markdown_fences(text), '{"key": "value"}')

    def test_empty_string(self):
        self.assertEqual(_strip_markdown_fences(''), '')

    def test_only_fence_marker(self):
        self.assertEqual(_strip_markdown_fences('```'), '')

    def test_multiline_json_in_fence(self):
        text = '```json\n{\n  "key": "value",\n  "list": [1, 2]\n}\n```'
        expected = '{\n  "key": "value",\n  "list": [1, 2]\n}'
        self.assertEqual(_strip_markdown_fences(text), expected)

    def test_text_with_extra_whitespace(self):
        text = '  ```json\n{"key": "value"}\n```  '
        result = _strip_markdown_fences(text)
        self.assertIn('"key"', result)


class TestParsePlannerJson(unittest.TestCase):
    """JSON 字段解析测试。"""

    def test_valid_complete_json(self):
        text = json.dumps({
            'intent': 'comparative',
            'complexity': 'complex',
            'decompose': True,
            'sub_queries': ['q1', 'q2', 'q3'],
            'rationale': 'Comparison needs split',
        })
        result = _parse_planner_json(text)
        self.assertEqual(result['intent'], 'comparative')
        self.assertEqual(result['complexity'], 'complex')
        self.assertTrue(result['decompose'])
        self.assertEqual(result['sub_queries'], ['q1', 'q2', 'q3'])
        self.assertEqual(result['rationale'], 'Comparison needs split')

    def test_truncates_to_max_subqueries(self):
        """sub_queries 超过 MAX_SUB_QUERIES 被截断。"""
        sub_queries = [f'q{i}' for i in range(10)]
        text = json.dumps({
            'intent': 'factual',
            'complexity': 'simple',
            'decompose': True,
            'sub_queries': sub_queries,
            'rationale': 'test',
        })
        result = _parse_planner_json(text)
        self.assertEqual(len(result['sub_queries']), MAX_SUB_QUERIES)
        self.assertEqual(result['sub_queries'], ['q0', 'q1', 'q2', 'q3', 'q4'])

    def test_invalid_json_raises(self):
        with self.assertRaises(RuntimeError):
            _parse_planner_json('not json at all')

    def test_empty_json_string_raises(self):
        with self.assertRaises(RuntimeError):
            _parse_planner_json('')

    def test_dict_with_missing_subqueries_raises(self):
        """sub_queries 字段缺失抛异常。"""
        text = json.dumps({'intent': 'factual', 'complexity': 'simple'})
        with self.assertRaises(RuntimeError):
            _parse_planner_json(text)

    def test_empty_subqueries_raises(self):
        """空 sub_queries 列表抛异常。"""
        text = json.dumps({
            'intent': 'factual',
            'sub_queries': [],
        })
        with self.assertRaises(RuntimeError):
            _parse_planner_json(text)

    def test_subqueries_with_non_string_items_coerced(self):
        """sub_queries 中非字符串元素被转为字符串。

        None 和 '' 被过滤（falsy），但 123 被转为 '123'。
        """
        text = json.dumps({
            'intent': 'factual',
            'sub_queries': ['q1', 123, None, 'q2', ''],
        })
        result = _parse_planner_json(text)
        # None 和 '' 被过滤；123 被转为 '123'；'q1' 'q2' 保留
        self.assertEqual(result['sub_queries'], ['q1', '123', 'q2'])

    def test_non_dict_json_raises(self):
        """JSON 是 list 而非 dict → 抛异常。"""
        text = json.dumps(['q1', 'q2'])
        with self.assertRaises(RuntimeError):
            _parse_planner_json(text)

    def test_missing_intent_defaults_unknown(self):
        text = json.dumps({
            'sub_queries': ['q1'],
        })
        result = _parse_planner_json(text)
        self.assertEqual(result['intent'], 'unknown')

    def test_decompose_defaults_based_on_subquery_count(self):
        """decompose 缺失时根据 sub_queries 数量推断。"""
        text = json.dumps({
            'sub_queries': ['q1'],
        })
        result = _parse_planner_json(text)
        # 单个 sub_query → decompose=False
        self.assertFalse(result['decompose'])

        text2 = json.dumps({
            'sub_queries': ['q1', 'q2'],
        })
        result2 = _parse_planner_json(text2)
        self.assertTrue(result2['decompose'])

    def test_rationale_truncated(self):
        """rationale 超长被截断到 200 字符。"""
        long_rationale = 'x' * 500
        text = json.dumps({
            'sub_queries': ['q1'],
            'rationale': long_rationale,
        })
        result = _parse_planner_json(text)
        self.assertEqual(len(result['rationale']), 200)


class TestCallPlannerApi(unittest.TestCase):
    """Planner API 调用测试（mock）。"""

    def _mock_api_response(self, content):
        """构造 mock API response。"""
        return {
            'choices': [{
                'message': {'content': content}
            }]
        }

    def test_api_success(self):
        content = json.dumps({
            'intent': 'factual',
            'complexity': 'simple',
            'decompose': False,
            'sub_queries': ['What is Python GIL?'],
            'rationale': 'Atomic query',
        })
        mock_resp = self._mock_api_response(content)

        with patch('planner.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = _call_planner_api('What is Python GIL?')
        self.assertEqual(result['intent'], 'factual')
        self.assertFalse(result['decompose'])
        self.assertEqual(result['sub_queries'], ['What is Python GIL?'])

    def test_api_success_with_markdown_fences(self):
        content_raw = json.dumps({
            'intent': 'comparative',
            'complexity': 'complex',
            'decompose': True,
            'sub_queries': ['q1', 'q2'],
            'rationale': 'test',
        })
        content_wrapped = f'```json\n{content_raw}\n```'
        mock_resp = self._mock_api_response(content_wrapped)

        with patch('planner.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = _call_planner_api('React vs Vue')
        self.assertTrue(result['decompose'])
        self.assertEqual(len(result['sub_queries']), 2)

    def test_api_no_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DEEPSEEK_API_KEY', None)
            with self.assertRaises(RuntimeError) as ctx:
                _call_planner_api('test query')
            self.assertIn('未设置', str(ctx.exception))

    def test_api_empty_query_raises(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
            with self.assertRaises(ValueError):
                _call_planner_api('')
            with self.assertRaises(ValueError):
                _call_planner_api('   ')

    def test_api_http_error_raises(self):
        import urllib.error
        with patch('planner.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError(
                       'url', 500, 'Server Error', {}, None)):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_planner_api('test')
            self.assertIn('HTTP 500', str(ctx.exception))

    def test_api_url_error_raises(self):
        import urllib.error
        with patch('planner.urllib.request.urlopen',
                   side_effect=urllib.error.URLError('connection refused')):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_planner_api('test')
            self.assertIn('URL error', str(ctx.exception))

    def test_api_invalid_response_structure_raises(self):
        # choices[0].message.content 缺失
        mock_resp = {'choices': [{'message': {}}]}
        with patch('planner.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError):
                    _call_planner_api('test')

    def test_api_invalid_json_in_content_raises(self):
        mock_resp = self._mock_api_response('not valid json')
        with patch('planner.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError):
                    _call_planner_api('test')


class TestPlanQueryFallback(unittest.TestCase):
    """plan_query 降级链测试。"""

    def test_empty_query_returns_fallback_empty(self):
        result = plan_query('')
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')
        self.assertEqual(result['sub_queries'], [])

    def test_none_query_returns_fallback_empty(self):
        result = plan_query(None)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')

    def test_non_string_query_returns_fallback_empty(self):
        result = plan_query(12345)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')

    def test_whitespace_query_returns_fallback_empty(self):
        result = plan_query('   ')
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')

    def test_api_failure_falls_back_to_original_query(self):
        """API 失败时降级到原查询作为单一 sub_query。"""
        with patch('planner._call_planner_api',
                   side_effect=RuntimeError('mocked failure')):
            result = plan_query('React vs Vue performance')
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-original')
        self.assertEqual(result['sub_queries'], ['React vs Vue performance'])
        self.assertIn('error', result)
        self.assertIn('mocked failure', result['error'])

    def test_successful_planner_returns_planner_mode(self):
        with patch('planner._call_planner_api',
                   return_value={
                       'intent': 'comparative',
                       'complexity': 'complex',
                       'decompose': True,
                       'sub_queries': ['q1', 'q2', 'q3'],
                       'rationale': 'Comparison',
                   }):
            result = plan_query('React vs Vue vs Angular')
        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'planner')
        self.assertEqual(len(result['sub_queries']), 3)
        self.assertNotIn('error', result)


class TestLoadPromptTemplate(unittest.TestCase):
    """prompt 模板加载测试。"""

    def test_loads_from_file(self):
        prompt = _load_prompt_template()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 100)
        # 应包含关键章节
        self.assertIn('Output Format', prompt)
        self.assertIn('sub_queries', prompt)

    def test_fallback_when_file_missing(self):
        with patch('planner.os.path.exists', return_value=False):
            with patch('builtins.open', side_effect=OSError('not found')):
                prompt = _load_prompt_template()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 50)
        # 兜底 prompt 也应包含关键说明
        self.assertIn('sub_queries', prompt)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def test_cli_plan_text_output(self):
        with patch('planner.plan_query',
                   return_value={
                       'success': True,
                       'intent': 'factual',
                       'complexity': 'simple',
                       'decompose': False,
                       'sub_queries': ['What is Python GIL?'],
                       'rationale': 'Atomic query',
                       'mode': 'planner',
                       'planner_mode': 'flash',
                       'max_subqueries': 5,
                       'original_query': 'What is Python GIL?',
                   }):
            with patch('sys.argv',
                       ['planner.py', 'plan', '--query', 'What is Python GIL?']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    from planner import _cli
                    exit_code = _cli()
                output = buf.getvalue()
                self.assertEqual(exit_code, 0)
                self.assertIn('Planner 结果', output)
                self.assertIn('What is Python GIL?', output)
                self.assertIn('planner', output)

    def test_cli_plan_json_output(self):
        result_data = {
            'success': True,
            'intent': 'comparative',
            'complexity': 'complex',
            'decompose': True,
            'sub_queries': ['q1', 'q2'],
            'rationale': 'Comparison',
            'mode': 'planner',
            'planner_mode': 'flash',
            'max_subqueries': 5,
            'original_query': 'React vs Vue',
        }
        with patch('planner.plan_query', return_value=result_data):
            with patch('sys.argv',
                       ['planner.py', 'plan', '--query', 'React vs Vue', '--json']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    from planner import _cli
                    exit_code = _cli()
                output = buf.getvalue()
                self.assertEqual(exit_code, 0)
                data = json.loads(output)
                self.assertEqual(data['mode'], 'planner')
                self.assertEqual(len(data['sub_queries']), 2)

    def test_cli_plan_fallback_displayed(self):
        """失败时显示警告 + 降级提示。"""
        with patch('planner.plan_query',
                   return_value={
                       'success': False,
                       'intent': 'unknown',
                       'complexity': 'unknown',
                       'decompose': False,
                       'sub_queries': ['original query'],
                       'rationale': 'Planner failed',
                       'mode': 'fallback-original',
                       'planner_mode': 'flash',
                       'max_subqueries': 5,
                       'error': 'mocked error',
                       'original_query': 'original query',
                   }):
            with patch('sys.argv',
                       ['planner.py', 'plan', '--query', 'original query']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    from planner import _cli
                    exit_code = _cli()
                output = buf.getvalue()
                self.assertEqual(exit_code, 0)
                self.assertIn('警告', output)
                self.assertIn('fallback', output)

    def test_cli_show_prompt(self):
        with patch('sys.argv', ['planner.py', 'show-prompt']):
            buf = StringIO()
            with patch('sys.stdout', new=buf):
                from planner import _cli
                exit_code = _cli()
            output = buf.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn('Planner', output)
            self.assertIn('sub_queries', output)


class TestConstraints(unittest.TestCase):
    """约束遵守验证。"""

    def test_max_subqueries_constant(self):
        self.assertEqual(MAX_SUB_QUERIES, 5)

    def test_no_external_dependencies(self):
        """验证仅用标准库。"""
        import importlib
        # planner.py 应仅 import: sys, os, json, argparse, urllib, io
        # 不应有 numpy/pandas/requests 等
        source_file = os.path.join(_SEARCH_DIR, 'planner.py')
        with open(source_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # 检查 import 语句
        forbidden_imports = ['numpy', 'pandas', 'requests',
                             'aiohttp', 'httpx', 'openai', 'anthropic']
        for forbidden in forbidden_imports:
            self.assertNotIn(f'import {forbidden}', content,
                             f'forbidden import: {forbidden}')
            self.assertNotIn(f'from {forbidden}', content,
                             f'forbidden from-import: {forbidden}')


class TestPlannerModeParameter(unittest.TestCase):
    """v4.3 spec §3.2: --planner-mode flash|pro 参数验证。"""

    def test_planner_modes_constant(self):
        """PLANNER_MODES 应包含 flash + pro 两个模式。"""
        self.assertIn('flash', planner.PLANNER_MODES)
        self.assertIn('pro', planner.PLANNER_MODES)
        self.assertEqual(planner.PLANNER_MODES['flash'], 'deepseek-chat')
        self.assertEqual(planner.PLANNER_MODES['pro'], 'deepseek-reasoner')

    def test_default_planner_mode_is_pro(self):
        """Phase 2 Decision 1: 默认模式应从 flash 迁移到 pro。

        原因：planner 是深度搜索入口点，Flash 在 planner 任务上质量塌方。
        用户约束：核心/大部分任务用 Pro，planner 属于核心。
        """
        self.assertEqual(planner.DEFAULT_PLANNER_MODE, 'pro')

    def test_call_planner_api_rejects_invalid_mode(self):
        """无效 planner_mode 应抛 ValueError。"""
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'test_key'}):
            with self.assertRaises(ValueError) as ctx:
                _call_planner_api('test query', planner_mode='invalid_mode')
        self.assertIn('Invalid planner_mode', str(ctx.exception))

    @patch('urllib.request.urlopen')
    def test_planner_mode_pro_uses_pro_model(self, mock_urlopen):
        """pro 模式应使用 deepseek-reasoner 模型。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"sub_queries":["q1"],"decompose":false,"rationale":"r"}'}}]
        }).encode('utf-8')
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'test_key'}):
            _call_planner_api('test', planner_mode='pro')

        # 验证 request body 使用 pro 模型
        req_body = json.loads(mock_urlopen.call_args[0][0].data.decode('utf-8'))
        self.assertEqual(req_body['model'], 'deepseek-reasoner')
        # Pro 模式应有更多 max_tokens
        self.assertGreater(req_body['max_tokens'], 500)

    @patch('urllib.request.urlopen')
    def test_planner_mode_flash_uses_flash_model(self, mock_urlopen):
        """flash 模式应使用 deepseek-chat 模型。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"sub_queries":["q1"],"decompose":false,"rationale":"r"}'}}]
        }).encode('utf-8')
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'test_key'}):
            _call_planner_api('test', planner_mode='flash')

        req_body = json.loads(mock_urlopen.call_args[0][0].data.decode('utf-8'))
        self.assertEqual(req_body['model'], 'deepseek-chat')

    def test_plan_query_includes_planner_mode_in_result(self):
        """plan_query 返回结果应包含 planner_mode 字段。"""
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': ''}):
            # 无 API key → 降级
            result = plan_query('test query', planner_mode='pro')
        self.assertEqual(result['planner_mode'], 'pro')


class TestMaxSubqueriesParameter(unittest.TestCase):
    """v4.3 spec §3.2: --max-subqueries N 参数验证。"""

    def test_max_subqueries_truncates_output(self):
        """max_subqueries 应截断输出。"""
        json_text = json.dumps({
            'sub_queries': ['q1', 'q2', 'q3', 'q4', 'q5'],
            'decompose': True,
        })
        result = _parse_planner_json(json_text, max_subqueries=3)
        self.assertEqual(len(result['sub_queries']), 3)
        self.assertEqual(result['sub_queries'], ['q1', 'q2', 'q3'])

    def test_max_subqueries_1(self):
        """max_subqueries=1 只保留 1 个子查询。"""
        json_text = json.dumps({
            'sub_queries': ['q1', 'q2', 'q3'],
            'decompose': True,
        })
        result = _parse_planner_json(json_text, max_subqueries=1)
        self.assertEqual(len(result['sub_queries']), 1)
        self.assertEqual(result['sub_queries'], ['q1'])

    @patch('urllib.request.urlopen')
    def test_max_subqueries_injected_into_prompt(self, mock_urlopen):
        """max_subqueries 应注入到 prompt 文本中（动态覆盖默认 5）。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            'choices': [{'message': {'content': '{"sub_queries":["q1"],"decompose":false,"rationale":"r"}'}}]
        }).encode('utf-8')
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'test_key'}):
            _call_planner_api('test', max_subqueries=3)

        # 验证 prompt 中包含 "Maximum 3 sub-queries"
        req_body = json.loads(mock_urlopen.call_args[0][0].data.decode('utf-8'))
        system_msg = req_body['messages'][0]['content']
        self.assertIn('Maximum 3 sub-queries', system_msg)

    def test_plan_query_includes_max_subqueries_in_result(self):
        """plan_query 返回结果应包含 max_subqueries 字段。"""
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': ''}):
            result = plan_query('test', max_subqueries=2)
        self.assertEqual(result['max_subqueries'], 2)


class TestPlannerJsonListDictCompat(unittest.TestCase):
    """v4.3 spec §4.4: list[dict] (spec 原始格式) 与 list[str] (简化格式) 兼容性。"""

    def test_parse_list_of_strings(self):
        """简化格式：list[str]。"""
        json_text = json.dumps({
            'sub_queries': ['q1', 'q2', 'q3'],
            'decompose': True,
        })
        result = _parse_planner_json(json_text)
        self.assertEqual(result['sub_queries'], ['q1', 'q2', 'q3'])

    def test_parse_list_of_dicts_extracts_query_field(self):
        """spec 原始格式：list[dict]，应提取 query 字段。"""
        json_text = json.dumps({
            'sub_queries': [
                {'query': 'React RSC 原理', 'priority': 'high', 'rationale': '理解核心'},
                {'query': 'RSC vs SSR 对比', 'priority': 'medium', 'rationale': '对比基准'},
            ],
            'decompose': True,
        })
        result = _parse_planner_json(json_text)
        # 应归一化为 list[str]
        self.assertEqual(result['sub_queries'], ['React RSC 原理', 'RSC vs SSR 对比'])

    def test_parse_list_of_dicts_missing_query_skipped(self):
        """dict 缺 query 字段应跳过。"""
        json_text = json.dumps({
            'sub_queries': [
                {'query': 'valid q', 'priority': 'high'},
                {'priority': 'low', 'rationale': 'no query'},  # 缺 query
            ],
            'decompose': True,
        })
        result = _parse_planner_json(json_text)
        self.assertEqual(len(result['sub_queries']), 1)
        self.assertEqual(result['sub_queries'], ['valid q'])

    def test_parse_mixed_list_strings_and_dicts(self):
        """混合 list[str] + list[dict] 也应正常解析。"""
        json_text = json.dumps({
            'sub_queries': [
                'plain string query',
                {'query': 'dict query', 'priority': 'high'},
            ],
            'decompose': True,
        })
        result = _parse_planner_json(json_text)
        self.assertEqual(result['sub_queries'], ['plain string query', 'dict query'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
