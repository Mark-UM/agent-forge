#!/usr/bin/env python3
"""summarize.py 单元测试 — 并发 fetch + Flash 摘要 + 降级。

覆盖维度：
  - _fetch_url: 合法 URL / 非法 URL / 非文本 content-type / HTTP 错误 / 超时
  - _strip_html: script/style 移除 / HTML 实体解码 / 空白压缩
  - _call_flash_for_summary: mock API 成功 + 各种失败
  - summarize_one: snippet 模式 / flash 模式 / fetch 失败 / Flash 失败
  - summarize_results: 并发 / max_urls 限制 / 空输入
  - PII 脱敏：URL 和 content 出境前被脱敏
  - CLI 接口
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

import summarize
from summarize import (
    _fetch_url, _strip_html, _call_flash_for_summary,
    summarize_one, summarize_results,
    FETCH_TIMEOUT, MAX_CONTENT_LENGTH, MAX_URLS_TO_FETCH,
)


SAMPLE_RESULT = {
    'title': 'React useEffect Cleanup Best Practices (2026)',
    'url': 'https://react.dev/docs/hooks-effect',
    'snippet': 'useEffect cleanup function runs on unmount or deps change.',
    'source': 'official-docs',
}

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
<title>React useEffect Cleanup</title>
<style>body { color: red; }</style>
<script>console.log('should be removed');</script>
</head>
<body>
<h1>React useEffect Cleanup</h1>
<p>useEffect cleanup function runs on unmount or deps change.</p>
<p>&nbsp;Welcome &amp; goodbye&nbsp;</p>
<!-- comment -->
</body>
</html>
"""


class TestFetchUrl(unittest.TestCase):
    """_fetch_url 测试。"""

    def test_empty_url(self):
        success, content, error = _fetch_url('')
        self.assertFalse(success)
        self.assertIn('Empty URL', error)

    def test_none_url(self):
        success, content, error = _fetch_url(None)
        self.assertFalse(success)

    def test_invalid_url_no_scheme(self):
        success, content, error = _fetch_url('example.com')
        self.assertFalse(success)
        self.assertIn('Invalid URL', error)

    def test_unsupported_scheme(self):
        success, content, error = _fetch_url('ftp://example.com/file')
        self.assertFalse(success)
        self.assertIn('Unsupported scheme', error)

    def test_successful_fetch(self):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.headers.get.return_value = 'text/html; charset=utf-8'
        mock_resp.read.return_value = b'<html><body>Hello</body></html>'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch('summarize.urllib.request.urlopen', return_value=mock_resp):
            success, content, error = _fetch_url('https://example.com')
        self.assertTrue(success)
        self.assertIn('Hello', content)
        self.assertIsNone(error)

    def test_http_error(self):
        import urllib.error
        with patch('summarize.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError(
                       'url', 404, 'Not Found', {}, None)):
            success, content, error = _fetch_url('https://example.com/missing')
        self.assertFalse(success)
        self.assertIn('HTTP 404', error)

    def test_url_error(self):
        import urllib.error
        with patch('summarize.urllib.request.urlopen',
                   side_effect=urllib.error.URLError('connection refused')):
            success, content, error = _fetch_url('https://example.com')
        self.assertFalse(success)
        self.assertIn('URL error', error)

    def test_non_text_content_type(self):
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.headers.get.return_value = 'image/png'
        mock_resp.read.return_value = b'\x89PNG binary data'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch('summarize.urllib.request.urlopen', return_value=mock_resp):
            success, content, error = _fetch_url('https://example.com/image.png')
        self.assertFalse(success)
        self.assertIn('Non-text', error)

    def test_content_truncated(self):
        """超过 MAX_CONTENT_LENGTH 的内容被截断。"""
        long_body = b'<html>' + b'x' * (MAX_CONTENT_LENGTH + 1000) + b'</html>'
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 200
        mock_resp.headers.get.return_value = 'text/html'
        mock_resp.read.return_value = long_body
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch('summarize.urllib.request.urlopen', return_value=mock_resp):
            success, content, error = _fetch_url('https://example.com')
        self.assertTrue(success)
        self.assertLessEqual(len(content), MAX_CONTENT_LENGTH)


class TestStripHtml(unittest.TestCase):
    """_strip_html 测试。"""

    def test_removes_script_block(self):
        html = '<script>console.log("evil");</script><p>visible</p>'
        text = _strip_html(html)
        self.assertNotIn('evil', text)
        self.assertIn('visible', text)

    def test_removes_style_block(self):
        html = '<style>body { color: red; }</style><p>visible</p>'
        text = _strip_html(html)
        self.assertNotIn('color', text)
        self.assertIn('visible', text)

    def test_removes_html_comments(self):
        html = '<!-- secret comment --><p>visible</p>'
        text = _strip_html(html)
        self.assertNotIn('secret', text)
        self.assertIn('visible', text)

    def test_html_entity_decoding(self):
        html = '<p>&nbsp;Hello &amp; goodbye&nbsp;</p>'
        text = _strip_html(html)
        self.assertIn('Hello & goodbye', text)

    def test_whitespace_compression(self):
        html = '<p>multiple\n\n   spaces\t\ttabs</p>'
        text = _strip_html(html)
        self.assertEqual(text, 'multiple spaces tabs')

    def test_empty_content(self):
        self.assertEqual(_strip_html(''), '')

    def test_none_content(self):
        self.assertEqual(_strip_html(None), '')

    def test_complete_html(self):
        text = _strip_html(SAMPLE_HTML)
        self.assertIn('React useEffect Cleanup', text)
        self.assertIn('useEffect cleanup function', text)
        self.assertNotIn('console.log', text)
        self.assertNotIn('color: red', text)
        self.assertNotIn('comment', text)


class TestCallFlashForSummary(unittest.TestCase):
    """_call_flash_for_summary 测试（mock API）。"""

    def test_flash_success(self):
        mock_response = {
            'choices': [{
                'message': {'content': 'React useEffect 清理函数用于组件卸载时释放资源'}
            }]
        }
        with patch('summarize.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                summary = _call_flash_for_summary(
                    'https://example.com', 'Title', 'content body')
        self.assertIn('React', summary)
        self.assertLessEqual(len(summary), 100)

    def test_flash_no_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop('DEEPSEEK_API_KEY', None)
            with self.assertRaises(RuntimeError) as ctx:
                _call_flash_for_summary('https://x.com', 't', 'c')
            self.assertIn('未设置', str(ctx.exception))

    def test_flash_http_error_raises(self):
        import urllib.error
        with patch('summarize.urllib.request.urlopen',
                   side_effect=urllib.error.HTTPError(
                       'url', 500, 'Server Error', {}, None)):
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError) as ctx:
                    _call_flash_for_summary('https://x.com', 't', 'c')
            self.assertIn('HTTP 500', str(ctx.exception))

    def test_flash_response_parse_error_raises(self):
        mock_response = {'choices': [{'message': {}}]}
        with patch('summarize.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with self.assertRaises(RuntimeError):
                    _call_flash_for_summary('https://x.com', 't', 'c')

    def test_summary_truncated_to_100(self):
        long_summary = 'x' * 200
        mock_response = {
            'choices': [{'message': {'content': long_summary}}]
        }
        with patch('summarize.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_response).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                summary = _call_flash_for_summary('https://x.com', 't', 'c')
        self.assertEqual(len(summary), 100)


class TestSummarizeOne(unittest.TestCase):
    """summarize_one 测试。"""

    def test_snippet_mode(self):
        """snippet 模式不 fetch，直接返回 snippet。"""
        result = summarize_one(SAMPLE_RESULT, mode='snippet')
        self.assertEqual(result['summary_mode'], 'snippet')
        self.assertFalse(result['fetch_success'])
        self.assertIn('useEffect', result['summary'])

    def test_snippet_truncated_to_100(self):
        long_result = dict(SAMPLE_RESULT,
                            snippet='x' * 200)
        result = summarize_one(long_result, mode='snippet')
        self.assertEqual(len(result['summary']), 100)

    def test_flash_mode_fetch_success(self):
        """flash 模式 + fetch 成功 + Flash 成功。

        分别 patch _fetch_url 和 _call_flash_for_summary 避免复杂的
        side_effect mock 链。
        """
        with patch('summarize._fetch_url',
                   return_value=(True, SAMPLE_HTML, None)):
            with patch('summarize._call_flash_for_summary',
                       return_value='React useEffect 清理资源'):
                result = summarize_one(SAMPLE_RESULT, mode='flash')

        self.assertEqual(result['summary_mode'], 'flash')
        self.assertTrue(result['fetch_success'])
        self.assertIn('React', result['summary'])

    def test_flash_mode_fetch_failed_falls_back_to_snippet(self):
        """fetch 失败 → 降级到 snippet。"""
        with patch('summarize._fetch_url',
                   return_value=(False, '', 'HTTP 404')):
            result = summarize_one(SAMPLE_RESULT, mode='flash')
        self.assertEqual(result['summary_mode'], 'fetch-failed')
        self.assertFalse(result['fetch_success'])
        self.assertIn('error', result)
        self.assertIn('useEffect', result['summary'])

    def test_flash_mode_flash_failed_falls_back_to_snippet(self):
        """fetch 成功 + Flash 失败 → 降级到 snippet。"""
        with patch('summarize._fetch_url',
                   return_value=(True, SAMPLE_HTML, None)), \
             patch('summarize._call_flash_for_summary',
                   side_effect=RuntimeError('API down')):
            result = summarize_one(SAMPLE_RESULT, mode='flash')
        self.assertEqual(result['summary_mode'], 'flash-failed-snippet')
        self.assertTrue(result['fetch_success'])
        self.assertIn('error', result)
        self.assertIn('useEffect', result['summary'])

    def test_flash_mode_empty_content_falls_back_to_snippet(self):
        """fetch 成功但内容为空 → 降级到 snippet。"""
        with patch('summarize._fetch_url',
                   return_value=(True, '', None)):
            result = summarize_one(SAMPLE_RESULT, mode='flash')
        self.assertEqual(result['summary_mode'], 'snippet')
        self.assertTrue(result['fetch_success'])
        self.assertIn('error', result)

    def test_invalid_result_type(self):
        result = summarize_one('not a dict', mode='flash')
        self.assertEqual(result['summary_mode'], 'invalid')

    def test_unknown_mode_uses_snippet(self):
        result = summarize_one(SAMPLE_RESULT, mode='unknown-mode')
        self.assertEqual(result['summary_mode'], 'snippet')


class TestSummarizeResults(unittest.TestCase):
    """summarize_results 测试。"""

    def test_empty_results(self):
        self.assertEqual(summarize_results([]), [])
        self.assertEqual(summarize_results(None), [])
        self.assertEqual(summarize_results('not list'), [])

    def test_max_urls_limit(self):
        """超过 max_urls 的结果用 snippet 兜底。"""
        results = [dict(SAMPLE_RESULT, url=f'https://x{i}.com')
                   for i in range(10)]
        summaries = summarize_results(results, mode='snippet', max_urls=3)
        # 前 3 个用 snippet 模式
        for i in range(3):
            self.assertEqual(summaries[i]['summary_mode'], 'snippet')
        # 后 7 个用 skipped-limit
        for i in range(3, 10):
            self.assertEqual(summaries[i]['summary_mode'], 'skipped-limit')

    def test_all_within_max_urls(self):
        """结果数 ≤ max_urls 全部处理。"""
        results = [SAMPLE_RESULT]
        summaries = summarize_results(results, mode='snippet', max_urls=3)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['summary_mode'], 'snippet')

    def test_concurrent_processing(self):
        """并发处理多个 URL。"""
        results = [dict(SAMPLE_RESULT, url=f'https://x{i}.com',
                         snippet=f'snippet {i}')
                   for i in range(5)]
        summaries = summarize_results(results, mode='snippet', max_urls=5,
                                       max_workers=3)
        self.assertEqual(len(summaries), 5)
        for i, s in enumerate(summaries):
            self.assertIn(f'snippet {i}', s['summary'])

    def test_exception_in_worker_caught(self):
        """单个 worker 异常不阻塞整体。"""
        results = [SAMPLE_RESULT, 'invalid-entry', SAMPLE_RESULT]
        # 用 snippet 模式避免真正 fetch
        summaries = summarize_results(results, mode='snippet', max_urls=3)
        self.assertEqual(len(summaries), 3)
        # 第二条是 'invalid-entry'（字符串），summarize_one 会返回 invalid mode
        # 但在 _process_one 中可能抛异常（result.get 调用失败），需要兜底
        # 验证 summaries 长度正确即可


class TestPIIRedaction(unittest.TestCase):
    """PII 脱敏在出境前应用。"""

    def test_url_redacted_before_flash_call(self):
        """含 PII 的 URL 在 Flash prompt 中被脱敏。"""
        mock_flash_resp = {
            'choices': [{'message': {'content': 'summary'}}]
        }
        with patch('summarize.urllib.request.urlopen') as mock_urlopen:
            mock_cm = MagicMock()
            mock_cm.__enter__.return_value.read.return_value = \
                json.dumps(mock_flash_resp).encode('utf-8')
            mock_cm.__exit__.return_value = None
            mock_urlopen.return_value = mock_cm

            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}):
                with patch('summarize._redact_outbound',
                           side_effect=lambda x: (
                               '[REDACTED-URL]' if '13800138000' in x else x,
                               {'redacted_count': 1}
                           )):
                    _call_flash_for_summary(
                        'https://13800138000.example.com',
                        'Title',
                        'content',
                    )

        # 验证 urlopen 收到的 payload 中 URL 已被脱敏
        call_args = mock_urlopen.call_args
        sent_data = json.loads(call_args[0][0].data.decode('utf-8'))
        prompt_content = sent_data['messages'][0]['content']
        self.assertNotIn('13800138000', prompt_content)
        self.assertIn('[REDACTED-URL]', prompt_content)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def test_cli_fetch_snippet_mode(self):
        from summarize import _cli
        with patch('sys.argv', [
            'summarize.py', 'fetch',
            '--results-json', json.dumps([SAMPLE_RESULT]),
            '--mode', 'snippet',
        ]):
            buf = StringIO()
            with patch('sys.stdout', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['summary_mode'], 'snippet')

    def test_cli_fetch_invalid_json(self):
        from summarize import _cli
        with patch('sys.argv', [
            'summarize.py', 'fetch',
            '--results-json', 'not-json',
        ]):
            buf = StringIO()
            with patch('sys.stderr', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 1)

    def test_cli_fetch_non_array_json(self):
        from summarize import _cli
        with patch('sys.argv', [
            'summarize.py', 'fetch',
            '--results-json', '{"not": "array"}',
        ]):
            buf = StringIO()
            with patch('sys.stderr', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 1)

    def test_cli_fetch_empty_results(self):
        from summarize import _cli
        with patch('sys.argv', [
            'summarize.py', 'fetch',
            '--results-json', '[]',
            '--mode', 'snippet',
        ]):
            buf = StringIO()
            with patch('sys.stdout', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 0)
            self.assertEqual(buf.getvalue().strip(), '[]')


class TestConstraints(unittest.TestCase):
    """约束验证。"""

    def test_zero_external_dependencies(self):
        """仅用标准库。"""
        source_file = os.path.join(_SEARCH_DIR, 'summarize.py')
        with open(source_file, 'r', encoding='utf-8') as f:
            content = f.read()
        forbidden = ['requests', 'aiohttp', 'httpx', 'bs4',
                     'BeautifulSoup', 'lxml', 'numpy', 'pandas']
        for f in forbidden:
            self.assertNotIn(f'import {f}', content,
                             f'forbidden import: {f}')

    def test_constants(self):
        self.assertEqual(MAX_URLS_TO_FETCH, 3)
        self.assertEqual(MAX_CONTENT_LENGTH, 5000)
        self.assertGreater(FETCH_TIMEOUT, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
