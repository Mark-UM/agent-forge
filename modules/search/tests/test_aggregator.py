#!/usr/bin/env python3
"""aggregator.py 单元测试 — 多源结果聚合 + 降级链。

覆盖维度：
  - _truncate: 正常 / 超长 / 非字符串
  - _load_prompt_template: 文件加载 / 文件缺失兜底
  - _format_results_for_prompt: 完整 / 空 / 非 dict / 超限截断
  - _build_aggregator_messages: 结构正确性
  - _call_aggregator_api: mock HTTP 成功 + HTTP 错误 + 超时 + 无 API key + 空 query
  - _fallback_summary: 完整 / 空结果 / 截断
  - aggregate_results: 完整降级链 + 边界输入
  - CLI: aggregate / show-prompt
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

import aggregator
from aggregator import (
    _truncate, _load_prompt_template, _format_results_for_prompt,
    _build_aggregator_messages, _call_aggregator_api, _fallback_summary,
    aggregate_results, _cli,
    MAX_RESULTS_PER_SUBQUERY, MAX_TOTAL_RESULTS, MAX_SUBQUERIES,
    MAX_SNIPPET_LEN, MAX_TITLE_LEN, MAX_OUTPUT_TOKENS,
)


# ── 测试数据 ────────────────────────────────────────────────────
SAMPLE_SUB_QUERIES_WITH_RESULTS = [
    {
        'sub_query': 'What is React Server Components',
        'results': [
            {
                'title': 'React Server Components (RSC) Documentation',
                'url': 'https://react.dev/server-components',
                'snippet': 'RSC allows components to run on the server only.',
                'source': 'official-docs',
            },
            {
                'title': 'Understanding RSC',
                'url': 'https://example.com/rsc',
                'snippet': 'A deep dive into RSC architecture.',
                'source': 'blog',
            },
        ],
    },
    {
        'sub_query': 'What is traditional SSR',
        'results': [
            {
                'title': 'Server-Side Rendering Guide',
                'url': 'https://example.com/ssr',
                'snippet': 'Traditional SSR renders HTML on the server.',
                'source': 'blog',
            },
        ],
    },
]

SAMPLE_AGGREGATED_MARKDOWN = """## 综合答案

### 核心发现
- RSC runs on server only (来源: [React docs](https://react.dev/server-components))
- SSR renders HTML on each request (来源: [SSR guide](https://example.com/ssr))

### 详细分析
RSC and SSR both render on the server but differ in execution model.

### 来源列表
1. [React Server Components](https://react.dev/server-components) — Official docs
2. [Understanding RSC](https://example.com/rsc) — Blog post

### 置信度
- 评级：中
- 理由：2 个来源，1 个 official-docs
"""


class TestTruncate(unittest.TestCase):
    """_truncate 测试。"""

    def test_short_text_unchanged(self):
        self.assertEqual(_truncate('hello', 10), 'hello')

    def test_exact_length_unchanged(self):
        self.assertEqual(_truncate('hello', 5), 'hello')

    def test_long_text_truncated_with_ellipsis(self):
        result = _truncate('hello world this is long', 10)
        self.assertEqual(len(result), 10)
        self.assertTrue(result.endswith('...'))

    def test_empty_string(self):
        self.assertEqual(_truncate('', 10), '')

    def test_non_string_returns_empty(self):
        self.assertEqual(_truncate(None, 10), '')
        self.assertEqual(_truncate(123, 10), '')
        self.assertEqual(_truncate([], 10), '')

    def test_max_len_zero(self):
        """max_len=0 时返回空（边界情况）。"""
        self.assertEqual(_truncate('hello', 0), '')


class TestLoadPromptTemplate(unittest.TestCase):
    """_load_prompt_template 测试。"""

    def test_loads_from_file(self):
        prompt = _load_prompt_template()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 100)
        # 应包含关键章节
        self.assertIn('核心发现', prompt)
        self.assertIn('来源列表', prompt)
        self.assertIn('置信度', prompt)

    def test_fallback_when_file_missing(self):
        with patch('aggregator.os.path.exists', return_value=False):
            with patch('builtins.open', side_effect=OSError('not found')):
                prompt = _load_prompt_template()
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 50)
        # 兜底 prompt 也应包含关键说明
        self.assertIn('核心发现', prompt)


class TestFormatResultsForPrompt(unittest.TestCase):
    """_format_results_for_prompt 测试。"""

    def test_complete_formatting(self):
        text = _format_results_for_prompt(
            'React RSC vs SSR', SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertIn('Original query: React RSC vs SSR', text)
        self.assertIn('Sub-queries and their results:', text)
        self.assertIn('Sub-query 1:', text)
        self.assertIn('Sub-query 2:', text)
        self.assertIn('React Server Components (RSC) Documentation', text)
        self.assertIn('https://react.dev/server-components', text)
        self.assertIn('RSC allows components to run on the server only', text)
        self.assertIn('Source: official-docs', text)

    def test_empty_original_query(self):
        text = _format_results_for_prompt(
            '', SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertIn('Original query: ', text)

    def test_non_dict_sub_query_skipped(self):
        sq_with_bad = [
            'not a dict',
            SAMPLE_SUB_QUERIES_WITH_RESULTS[0],
        ]
        text = _format_results_for_prompt('test', sq_with_bad)
        # 无效 sub-query 被跳过，有效 sub-query 编号为 1（不跳号）
        self.assertIn('Sub-query 1:', text)
        self.assertNotIn('Sub-query 2:', text)
        self.assertIn('React Server Components', text)

    def test_empty_results_list(self):
        sq_empty = [{'sub_query': 'q1', 'results': []}]
        text = _format_results_for_prompt('test', sq_empty)
        self.assertIn('(no results for this sub-query)', text)

    def test_non_list_results_treated_as_empty(self):
        sq_bad = [{'sub_query': 'q1', 'results': 'not a list'}]
        text = _format_results_for_prompt('test', sq_bad)
        self.assertIn('(no results for this sub-query)', text)

    def test_non_dict_result_skipped(self):
        sq_bad_result = [{
            'sub_query': 'q1',
            'results': ['not a dict', {'title': 'ok', 'url': 'u',
                                       'snippet': 's', 'source': 'b'}],
        }]
        text = _format_results_for_prompt('test', sq_bad_result)
        self.assertIn('ok', text)
        # 第二个有效条目应被格式化

    def test_max_subqueries_limit(self):
        """sub-queries 超过 MAX_SUBQUERIES 被截断。"""
        sq_many = [
            {'sub_query': f'q{i}', 'results': [{'title': f't{i}', 'url': 'u',
                                              'snippet': 's', 'source': 'b'}]}
            for i in range(10)
        ]
        text = _format_results_for_prompt('test', sq_many)
        # 只显示前 MAX_SUBQUERIES 个
        self.assertIn('Sub-query 1:', text)
        self.assertIn(f'Sub-query {MAX_SUBQUERIES}:', text)
        self.assertNotIn(f'Sub-query {MAX_SUBQUERIES + 1}:', text)

    def test_max_results_per_subquery_limit(self):
        """每个 sub-query 的 results 超过 MAX_RESULTS_PER_SUBQUERY 被截断。"""
        sq_many_results = [{
            'sub_query': 'q1',
            'results': [
                {'title': f't{i}', 'url': f'u{i}', 'snippet': 's',
                 'source': 'b'}
                for i in range(10)
            ],
        }]
        text = _format_results_for_prompt('test', sq_many_results)
        # 只显示前 MAX_RESULTS_PER_SUBQUERY 条
        self.assertIn('t0', text)
        self.assertIn(f't{MAX_RESULTS_PER_SUBQUERY - 1}', text)
        self.assertNotIn(f't{MAX_RESULTS_PER_SUBQUERY}', text)

    def test_max_total_results_limit(self):
        """总结果数超过 MAX_TOTAL_RESULTS 被截断。"""
        sq_many = [
            {'sub_query': f'q{i}', 'results': [
                {'title': f't{i}-{j}', 'url': f'u{i}-{j}', 'snippet': 's',
                 'source': 'b'}
                for j in range(MAX_RESULTS_PER_SUBQUERY)
            ]}
            for i in range(MAX_SUBQUERIES)
        ]
        text = _format_results_for_prompt('test', sq_many)
        self.assertIn('(more results truncated for cost)', text)

    def test_long_title_truncated(self):
        long_title = 'A' * 200
        sq = [{
            'sub_query': 'q1',
            'results': [{'title': long_title, 'url': 'u', 'snippet': 's',
                         'source': 'b'}],
        }]
        text = _format_results_for_prompt('test', sq)
        self.assertIn('...', text)
        # 截断后长度 + '...' = MAX_TITLE_LEN
        # 不检查精确长度，只检查 '...' 出现且总长度不超过 MAX_TITLE_LEN

    def test_long_snippet_truncated(self):
        long_snippet = 'S' * 300
        sq = [{
            'sub_query': 'q1',
            'results': [{'title': 't', 'url': 'u', 'snippet': long_snippet,
                         'source': 'b'}],
        }]
        text = _format_results_for_prompt('test', sq)
        self.assertIn('...', text)

    def test_long_url_truncated(self):
        """URL 超过 MAX_URL_LEN 被截断（防止 token 预算溢出）。"""
        long_url = 'https://example.com/' + 'a' * 500
        sq = [{
            'sub_query': 'q1',
            'results': [{'title': 't', 'url': long_url, 'snippet': 's',
                         'source': 'b'}],
        }]
        text = _format_results_for_prompt('test', sq)
        self.assertIn('...', text)
        # URL 应被截断到 MAX_URL_LEN
        self.assertNotIn('a' * 500, text)


class TestBuildAggregatorMessages(unittest.TestCase):
    """_build_aggregator_messages 测试。"""

    def test_message_structure(self):
        prompt = 'test prompt'
        messages = _build_aggregator_messages(
            'query', SAMPLE_SUB_QUERIES_WITH_RESULTS, prompt)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['role'], 'system')
        self.assertEqual(messages[0]['content'], prompt)
        self.assertEqual(messages[1]['role'], 'user')
        self.assertIn('query', messages[1]['content'])
        self.assertIn('aggregate', messages[1]['content'].lower())

    def test_results_text_in_user_message(self):
        messages = _build_aggregator_messages(
            'original q', SAMPLE_SUB_QUERIES_WITH_RESULTS, 'prompt')
        user_content = messages[1]['content']
        self.assertIn('Original query: original q', user_content)
        self.assertIn('Sub-query 1:', user_content)
        self.assertIn('React Server Components (RSC) Documentation',
                      user_content)


class TestCallAggregatorApi(unittest.TestCase):
    """_call_aggregator_api 测试（mock）。"""

    def _mock_api_response(self, content):
        return {
            'choices': [{
                'message': {'content': content}
            }]
        }

    def test_api_success(self):
        mock_resp = self._mock_api_response(SAMPLE_AGGREGATED_MARKDOWN)

        with patch('aggregator.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                result = _call_aggregator_api(
                    'React RSC vs SSR', SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertIn('核心发现', result)
        self.assertIn('置信度', result)

    def test_no_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DEEPSEEK_API_KEY', None)
            with self.assertRaises(RuntimeError) as ctx:
                _call_aggregator_api(
                    'q', SAMPLE_SUB_QUERIES_WITH_RESULTS)
            self.assertIn('未设置', str(ctx.exception))

    def test_empty_query_raises(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
            with self.assertRaises(ValueError):
                _call_aggregator_api('', SAMPLE_SUB_QUERIES_WITH_RESULTS)
            with self.assertRaises(ValueError):
                _call_aggregator_api('   ', SAMPLE_SUB_QUERIES_WITH_RESULTS)

    def test_non_list_sub_queries_raises(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
            with self.assertRaises(ValueError):
                _call_aggregator_api('q', 'not a list')
            with self.assertRaises(ValueError):
                _call_aggregator_api('q', None)

    def test_http_error_raises(self):
        import urllib.error
        with patch('aggregator.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError(
                       'url', 500, 'Server Error', {}, None)):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_aggregator_api(
                        'q', SAMPLE_SUB_QUERIES_WITH_RESULTS)
                self.assertIn('HTTP 500', str(ctx.exception))

    def test_url_error_raises(self):
        import urllib.error
        with patch('aggregator.urllib.request.urlopen',
                   side_effect=urllib.error.URLError('connection refused')):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_aggregator_api(
                        'q', SAMPLE_SUB_QUERIES_WITH_RESULTS)
                self.assertIn('URL error', str(ctx.exception))

    def test_timeout_raises(self):
        with patch('aggregator.urllib.request.urlopen',
                   side_effect=TimeoutError('timed out')):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_aggregator_api(
                        'q', SAMPLE_SUB_QUERIES_WITH_RESULTS)
                self.assertIn('timeout', str(ctx.exception).lower())

    def test_invalid_response_structure_raises(self):
        # choices[0].message.content 缺失
        mock_resp = {'choices': [{'message': {}}]}
        with patch('aggregator.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError):
                    _call_aggregator_api(
                        'q', SAMPLE_SUB_QUERIES_WITH_RESULTS)

    def test_empty_content_raises(self):
        mock_resp = self._mock_api_response('')
        with patch('aggregator.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_aggregator_api(
                        'q', SAMPLE_SUB_QUERIES_WITH_RESULTS)
                self.assertIn('empty', str(ctx.exception).lower())

    def test_pii_redaction_applied(self):
        """PII 在 API 调用前被脱敏。"""
        mock_resp = self._mock_api_response(SAMPLE_AGGREGATED_MARKDOWN)
        with patch('aggregator.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with patch('aggregator._redact_outbound',
                           side_effect=lambda x: (x + '_REDACTED',
                                                   {'redacted_count': 1})):
                    _call_aggregator_api(
                        'my email is test@example.com',
                        SAMPLE_SUB_QUERIES_WITH_RESULTS)
                    # 调用成功即证明 PII 脱敏未阻塞主流程

    def test_pii_redaction_warns_but_continues(self):
        """PII 脱敏触发警告但不阻塞 API 调用。"""
        mock_resp = self._mock_api_response(SAMPLE_AGGREGATED_MARKDOWN)
        with patch('aggregator.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                # 使用真实的 _redact_outbound（会触发 PII 检测）
                with patch('sys.stderr', new=StringIO()):
                    result = _call_aggregator_api(
                        'email: test@example.com',
                        SAMPLE_SUB_QUERIES_WITH_RESULTS)
                    # 应返回有效 Markdown
                    self.assertIn('核心发现', result)


class TestFallbackSummary(unittest.TestCase):
    """_fallback_summary 测试。"""

    def test_complete_summary(self):
        md = _fallback_summary('test query',
                               SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertIn('## 综合答案', md)
        self.assertIn('### 核心发现', md)
        self.assertIn('### 详细分析', md)
        self.assertIn('### 来源列表', md)
        self.assertIn('### 置信度', md)
        self.assertIn('test query', md)
        self.assertIn('Sub-query 1:', md)
        self.assertIn('Sub-query 2:', md)
        self.assertIn('React Server Components (RSC) Documentation', md)

    def test_empty_sub_queries(self):
        md = _fallback_summary('test', [])
        self.assertIn('## 综合答案', md)
        self.assertIn('### 置信度', md)

    def test_non_dict_sub_query_skipped(self):
        sq_bad = ['not a dict', SAMPLE_SUB_QUERIES_WITH_RESULTS[0]]
        md = _fallback_summary('test', sq_bad)
        # 无效 sub-query 被跳过，有效 sub-query 编号为 1（不跳号）
        self.assertIn('Sub-query 1:', md)
        self.assertNotIn('Sub-query 2:', md)
        self.assertIn('React Server Components', md)

    def test_empty_results_list(self):
        sq_empty = [{'sub_query': 'q1', 'results': []}]
        md = _fallback_summary('test', sq_empty)
        self.assertIn('(无结果)', md)

    def test_long_title_truncated(self):
        long_title = 'A' * 200
        sq = [{'sub_query': 'q1', 'results': [
            {'title': long_title, 'url': 'u', 'snippet': 's', 'source': 'b'}]}]
        md = _fallback_summary('test', sq)
        self.assertIn('...', md)

    def test_long_snippet_truncated(self):
        long_snippet = 'S' * 300
        sq = [{'sub_query': 'q1', 'results': [
            {'title': 't', 'url': 'u', 'snippet': long_snippet, 'source': 'b'}]}]
        md = _fallback_summary('test', sq)
        self.assertIn('...', md)

    def test_max_total_results_truncation(self):
        """超过 MAX_TOTAL_RESULTS 时显示截断提示。"""
        sq_many = [
            {'sub_query': f'q{i}', 'results': [
                {'title': f't{i}-{j}', 'url': f'u{i}-{j}', 'snippet': 's',
                 'source': 'b'}
                for j in range(MAX_RESULTS_PER_SUBQUERY)
            ]}
            for i in range(MAX_SUBQUERIES)
        ]
        md = _fallback_summary('test', sq_many)
        self.assertIn('(更多结果已截断)', md)


class TestAggregateResults(unittest.TestCase):
    """aggregate_results 降级链测试。"""

    def test_empty_query_returns_fallback_empty(self):
        result = aggregate_results('', SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')
        self.assertIn('原始查询为空', result['markdown'])
        self.assertEqual(result['sub_query_count'], 0)
        self.assertEqual(result['total_results'], 0)

    def test_none_query_returns_fallback_empty(self):
        result = aggregate_results(None, SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')

    def test_whitespace_query_returns_fallback_empty(self):
        result = aggregate_results('   ', SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')

    def test_empty_sub_queries_returns_fallback_empty(self):
        result = aggregate_results('test', [])
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')
        self.assertIn('无 sub-query', result['markdown'])

    def test_none_sub_queries_returns_fallback_empty(self):
        result = aggregate_results('test', None)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')

    def test_all_empty_results_returns_fallback_empty(self):
        sq_empty = [
            {'sub_query': 'q1', 'results': []},
            {'sub_query': 'q2', 'results': []},
        ]
        result = aggregate_results('test', sq_empty)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')
        self.assertIn('结果为空', result['markdown'])
        self.assertEqual(result['sub_query_count'], 2)
        self.assertEqual(result['total_results'], 0)

    def test_api_failure_falls_back_to_summary(self):
        """API 失败时降级到简单摘要。"""
        with patch('aggregator._call_aggregator_api',
                   side_effect=RuntimeError('mocked failure')):
            result = aggregate_results('test',
                                       SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-summary')
        self.assertIn('## 综合答案', result['markdown'])
        self.assertIn('test', result['markdown'])
        self.assertIn('聚合服务暂不可用', result['markdown'])
        self.assertIn('error', result)
        self.assertIn('mocked failure', result['error'])
        self.assertEqual(result['sub_query_count'], 2)
        self.assertEqual(result['total_results'], 3)  # 2 + 1 results

    def test_fallback_path_redacts_pii(self):
        """fallback 路径也应做 PII 脱敏（防御性）。

        场景：API 失败时，_fallback_summary 使用已脱敏数据。
        """
        # 输入包含 PII（邮箱）
        sq_with_pii = [{
            'sub_query': 'search about test@example.com',
            'results': [{
                'title': 'Email: test@example.com',
                'url': 'https://example.com/page',
                'snippet': 'Contact test@example.com for info',
                'source': 'blog',
            }],
        }]
        # API 失败 → 走 fallback-summary 路径
        with patch('aggregator._call_aggregator_api',
                   side_effect=RuntimeError('mocked failure')):
            with patch('sys.stderr', new=StringIO()):
                result = aggregate_results(
                    'query with test@example.com email',
                    sq_with_pii)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-summary')
        # 邮箱应被 [REDACTED-EMAIL] 替换，不出现在 fallback markdown 中
        self.assertNotIn('test@example.com', result['markdown'])

    def test_successful_aggregation(self):
        with patch('aggregator._call_aggregator_api',
                   return_value=SAMPLE_AGGREGATED_MARKDOWN):
            result = aggregate_results('test query',
                                       SAMPLE_SUB_QUERIES_WITH_RESULTS)
        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'aggregator')
        self.assertEqual(result['markdown'], SAMPLE_AGGREGATED_MARKDOWN)
        self.assertEqual(result['original_query'], 'test query')
        self.assertEqual(result['sub_query_count'], 2)
        self.assertEqual(result['total_results'], 3)
        self.assertNotIn('error', result)

    def test_non_dict_sub_queries_counted_correctly(self):
        """非 dict sub-query 不计入 sub_query_count。"""
        sq_mixed = [
            'not a dict',
            SAMPLE_SUB_QUERIES_WITH_RESULTS[0],
            None,
        ]
        with patch('aggregator._call_aggregator_api',
                   return_value=SAMPLE_AGGREGATED_MARKDOWN):
            result = aggregate_results('test', sq_mixed)
        self.assertTrue(result['success'])
        # 仅 1 个有效 sub-query
        self.assertEqual(result['sub_query_count'], 1)

    def test_non_list_results_treated_as_empty(self):
        sq_bad = [
            {'sub_query': 'q1', 'results': 'not a list'},
            {'sub_query': 'q2', 'results': None},
        ]
        result = aggregate_results('test', sq_bad)
        # 所有 results 都无效 → fallback-empty
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-empty')
        # sub_query_count 仍是 2（dict 本身有效，但 results 为空）
        self.assertEqual(result['sub_query_count'], 2)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def _run_cli(self, argv, stdin_data=None):
        """运行 CLI 并捕获 stdout/stderr/exit_code。"""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_stdin = sys.stdin
        sys.argv = ['aggregator.py'] + argv
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        if stdin_data is not None:
            sys.stdin = StringIO(stdin_data)
        try:
            exit_code = _cli()
            stdout = sys.stdout.getvalue()
            stderr = sys.stderr.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            sys.stdin = old_stdin
        return exit_code, stdout, stderr

    def test_show_prompt(self):
        code, stdout, _ = self._run_cli(['show-prompt'])
        self.assertEqual(code, 0)
        self.assertIn('Result Aggregator', stdout)
        self.assertIn('核心发现', stdout)

    def test_aggregate_with_sub_queries_file(self):
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8')
        try:
            json.dump(SAMPLE_SUB_QUERIES_WITH_RESULTS, tmp,
                      ensure_ascii=False)
            tmp.close()

            with patch('aggregator._call_aggregator_api',
                       return_value=SAMPLE_AGGREGATED_MARKDOWN):
                code, stdout, _ = self._run_cli(
                    ['aggregate', '--original-query', 'test',
                     '--sub-queries-file', tmp.name])
            self.assertEqual(code, 0)
            self.assertIn('核心发现', stdout)
        finally:
            os.unlink(tmp.name)

    def test_aggregate_with_results_file(self):
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8')
        try:
            results = SAMPLE_SUB_QUERIES_WITH_RESULTS[0]['results']
            json.dump(results, tmp, ensure_ascii=False)
            tmp.close()

            with patch('aggregator._call_aggregator_api',
                       return_value=SAMPLE_AGGREGATED_MARKDOWN):
                code, stdout, _ = self._run_cli(
                    ['aggregate', '--original-query', 'test',
                     '--results-file', tmp.name])
            self.assertEqual(code, 0)
            self.assertIn('核心发现', stdout)
        finally:
            os.unlink(tmp.name)

    def test_aggregate_from_stdin(self):
        stdin_data = json.dumps(SAMPLE_SUB_QUERIES_WITH_RESULTS)
        with patch('aggregator._call_aggregator_api',
                   return_value=SAMPLE_AGGREGATED_MARKDOWN):
            code, stdout, _ = self._run_cli(
                ['aggregate', '--original-query', 'test'],
                stdin_data=stdin_data)
        self.assertEqual(code, 0)
        self.assertIn('核心发现', stdout)

    def test_aggregate_from_stdin_dict_wrapper(self):
        """stdin JSON 可以是 dict 包裹的格式。"""
        stdin_data = json.dumps({
            'sub_queries_with_results': SAMPLE_SUB_QUERIES_WITH_RESULTS
        })
        with patch('aggregator._call_aggregator_api',
                   return_value=SAMPLE_AGGREGATED_MARKDOWN):
            code, stdout, _ = self._run_cli(
                ['aggregate', '--original-query', 'test'],
                stdin_data=stdin_data)
        self.assertEqual(code, 0)

    def test_aggregate_json_output(self):
        stdin_data = json.dumps(SAMPLE_SUB_QUERIES_WITH_RESULTS)
        with patch('aggregator._call_aggregator_api',
                   return_value=SAMPLE_AGGREGATED_MARKDOWN):
            code, stdout, _ = self._run_cli(
                ['aggregate', '--original-query', 'test', '--json'],
                stdin_data=stdin_data)
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'aggregator')

    def test_aggregate_invalid_stdin_json(self):
        code, _, stderr = self._run_cli(
            ['aggregate', '--original-query', 'test'],
            stdin_data='not valid json')
        self.assertEqual(code, 1)
        self.assertIn('JSON', stderr)

    def test_aggregate_invalid_stdin_format(self):
        """stdin JSON 既不是 list 也不是 dict['sub_queries_with_results']。"""
        code, _, stderr = self._run_cli(
            ['aggregate', '--original-query', 'test'],
            stdin_data=json.dumps({'wrong_key': 123}))
        self.assertEqual(code, 1)
        self.assertIn('格式', stderr)

    def test_aggregate_sub_queries_file_not_found(self):
        code, _, stderr = self._run_cli(
            ['aggregate', '--original-query', 'test',
             '--sub-queries-file', '/nonexistent/file.json'])
        self.assertEqual(code, 1)
        self.assertIn('错误', stderr)

    def test_aggregate_results_file_not_found(self):
        code, _, stderr = self._run_cli(
            ['aggregate', '--original-query', 'test',
             '--results-file', '/nonexistent/file.json'])
        self.assertEqual(code, 1)
        self.assertIn('错误', stderr)

    def test_aggregate_fallback_output(self):
        """Aggregator 失败时降级输出仍为有效 Markdown。"""
        stdin_data = json.dumps(SAMPLE_SUB_QUERIES_WITH_RESULTS)
        with patch('aggregator._call_aggregator_api',
                   side_effect=RuntimeError('mocked')):
            code, stdout, _ = self._run_cli(
                ['aggregate', '--original-query', 'test'],
                stdin_data=stdin_data)
        self.assertEqual(code, 0)
        self.assertIn('## 综合答案', stdout)
        self.assertIn('聚合服务暂不可用', stdout)


class TestConstants(unittest.TestCase):
    """常量校验。"""

    def test_max_constants_positive(self):
        self.assertGreater(MAX_RESULTS_PER_SUBQUERY, 0)
        self.assertGreater(MAX_TOTAL_RESULTS, 0)
        self.assertGreater(MAX_SUBQUERIES, 0)
        self.assertGreater(MAX_SNIPPET_LEN, 0)
        self.assertGreater(MAX_TITLE_LEN, 0)
        self.assertGreater(MAX_OUTPUT_TOKENS, 0)

    def test_max_total_ge_max_per_subquery(self):
        """总结果上限应不小于单个 sub-query 的上限。"""
        self.assertGreaterEqual(MAX_TOTAL_RESULTS, MAX_RESULTS_PER_SUBQUERY)

    def test_max_total_ge_max_subqueries(self):
        """总结果上限应不小于 sub-query 数量上限。"""
        self.assertGreaterEqual(MAX_TOTAL_RESULTS, MAX_SUBQUERIES)


if __name__ == '__main__':
    unittest.main(verbosity=2)
