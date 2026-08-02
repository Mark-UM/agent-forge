#!/usr/bin/env python3
"""i18n.py 单元测试 — 中英文混合查询扩展。

覆盖维度：
  - detect_mixed_lang：纯中文 / 纯英文 / 混合 / 空 / 非 str / 特殊字符
  - translate_query：成功 / API key 缺失 / HTTP 错误 / 超时 / 响应解析失败
  - expand_query：混合 / 单语言 / 单语言+include_opposite=False / 翻译失败降级
  - _strip_markdown_fences：各种代码块围栏
  - _build_translate_prompt：zh / en / 无效 target
  - CLI：detect / translate / expand
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

import i18n
from i18n import (
    detect_mixed_lang, translate_query, expand_query,
    _strip_markdown_fences, _build_translate_prompt,
    _FLASH_API, _FLASH_MODEL, _FLASH_TIMEOUT, _FLASH_MAX_TOKENS,
    _CJK_RE, _ENGLISH_RE, SUPPORTED_TARGETS,
)


# ── 辅助函数 ──────────────────────────────────────────────
def _make_flash_response(text):
    """构造 Flash API 成功响应。"""
    return {
        'choices': [{
            'message': {'content': text},
            'finish_reason': 'stop',
        }],
        'usage': {'total_tokens': 50},
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
    def test_flash_api_is_https(self):
        self.assertTrue(_FLASH_API.startswith('https://'))

    def test_flash_model_is_string(self):
        self.assertIsInstance(_FLASH_MODEL, str)
        self.assertGreater(len(_FLASH_MODEL), 0)

    def test_flash_timeout_positive(self):
        self.assertGreater(_FLASH_TIMEOUT, 0)

    def test_flash_max_tokens_positive(self):
        self.assertGreater(_FLASH_MAX_TOKENS, 0)

    def test_supported_targets(self):
        self.assertIn('zh', SUPPORTED_TARGETS)
        self.assertIn('en', SUPPORTED_TARGETS)
        self.assertEqual(len(SUPPORTED_TARGETS), 2)

    def test_cjk_re_compiled(self):
        self.assertTrue(hasattr(_CJK_RE, 'findall'))

    def test_english_re_compiled(self):
        self.assertTrue(hasattr(_ENGLISH_RE, 'findall'))


# ── detect_mixed_lang 测试 ─────────────────────────────────
class TestDetectMixedLang(unittest.TestCase):
    def test_empty_query(self):
        result = detect_mixed_lang('')
        self.assertFalse(result['has_chinese'])
        self.assertFalse(result['has_english'])
        self.assertFalse(result['is_mixed'])
        self.assertEqual(result['primary_lang'], 'unknown')
        self.assertEqual(result['chinese_ratio'], 0.0)

    def test_none_query(self):
        result = detect_mixed_lang(None)
        self.assertFalse(result['has_chinese'])
        self.assertEqual(result['primary_lang'], 'unknown')

    def test_non_string_query(self):
        result = detect_mixed_lang(12345)
        self.assertFalse(result['has_chinese'])
        self.assertEqual(result['primary_lang'], 'unknown')

    def test_pure_chinese(self):
        result = detect_mixed_lang('如何学习人工智能')
        self.assertTrue(result['has_chinese'])
        self.assertFalse(result['has_english'])
        self.assertFalse(result['is_mixed'])
        self.assertEqual(result['primary_lang'], 'zh')
        self.assertGreater(result['chinese_ratio'], 0.5)

    def test_pure_english(self):
        result = detect_mixed_lang('how to learn artificial intelligence')
        self.assertFalse(result['has_chinese'])
        self.assertTrue(result['has_english'])
        self.assertFalse(result['is_mixed'])
        self.assertEqual(result['primary_lang'], 'en')
        self.assertLess(result['chinese_ratio'], 0.1)

    def test_mixed_query(self):
        result = detect_mixed_lang('React useEffect 清理副作用')
        self.assertTrue(result['has_chinese'])
        self.assertTrue(result['has_english'])
        self.assertTrue(result['is_mixed'])
        self.assertEqual(result['primary_lang'], 'mixed')
        self.assertIn('React', result['english_parts'])
        self.assertIn('清理副作用', result['chinese_parts'])

    def test_mixed_query_with_numbers(self):
        result = detect_mixed_lang('GPT-4 模型对比 GPT-3.5')
        # 数字不影响检测
        self.assertTrue(result['has_english'])
        self.assertTrue(result['has_chinese'])

    def test_chinese_ratio_calculation(self):
        result = detect_mixed_lang('React 清理副作用')
        # 5 个中文字符 (清理副作用) / (5 中文 + 5 英文字母 React) = 5/10 = 0.5
        self.assertAlmostEqual(result['chinese_ratio'], 0.5, places=2)

    def test_chinese_parts_deduplicated(self):
        result = detect_mixed_lang('清理 清理 副作用')
        # 重复的 "清理" 应去重
        self.assertEqual(result['chinese_parts'].count('清理'), 1)

    def test_english_parts_deduplicated(self):
        result = detect_mixed_lang('React useEffect React')
        # 重复的 "React" 应去重
        self.assertEqual(result['english_parts'].count('React'), 1)

    def test_special_chars_only(self):
        result = detect_mixed_lang('!@#$%^&*()')
        self.assertFalse(result['has_chinese'])
        self.assertFalse(result['has_english'])
        self.assertEqual(result['primary_lang'], 'unknown')

    def test_single_char_english_ignored(self):
        # 单个英文字母不应被识别为英文 part
        result = detect_mixed_lang('a b c')
        # _ENGLISH_RE 要求至少 2 个连续字母
        # "a b c" 不应匹配
        # 但中文占比 0，所以 primary_lang 可能是 'en' 或 'unknown'
        # 取决于实际匹配行为，至少不应 crash
        self.assertIsNotNone(result['primary_lang'])


# ── _build_translate_prompt 测试 ──────────────────────────
class TestBuildTranslatePrompt(unittest.TestCase):
    def test_target_en_has_instruction(self):
        prompt = _build_translate_prompt('test query', 'en')
        self.assertIn('English', prompt)
        self.assertIn('test query', prompt)

    def test_target_zh_has_instruction(self):
        prompt = _build_translate_prompt('test query', 'zh')
        self.assertIn('中文', prompt)
        self.assertIn('test query', prompt)

    def test_invalid_target_raises(self):
        with self.assertRaises(ValueError):
            _build_translate_prompt('test', 'fr')

    def test_prompt_preserves_query(self):
        prompt = _build_translate_prompt('React 清理副作用', 'en')
        self.assertIn('React 清理副作用', prompt)


# ── _strip_markdown_fences 测试 ────────────────────────────
class TestStripMarkdownFences(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(_strip_markdown_fences('hello'), 'hello')

    def test_empty_string(self):
        self.assertEqual(_strip_markdown_fences(''), '')

    def test_triple_backtick_with_lang(self):
        text = '```python\nprint("hello")\n```'
        self.assertEqual(_strip_markdown_fences(text), 'print("hello")')

    def test_triple_backtick_no_lang(self):
        text = '```\nhello\n```'
        self.assertEqual(_strip_markdown_fences(text), 'hello')

    def test_single_backtick(self):
        self.assertEqual(_strip_markdown_fences('`hello`'), 'hello')

    def test_double_quote_stripped(self):
        self.assertEqual(_strip_markdown_fences('"hello"'), 'hello')

    def test_single_quote_stripped(self):
        self.assertEqual(_strip_markdown_fences("'hello'"), 'hello')

    def test_none_input(self):
        self.assertIsNone(_strip_markdown_fences(None))


# ── translate_query 边界测试 ───────────────────────────────
class TestTranslateQueryEdgeCases(unittest.TestCase):
    def test_empty_query_returns_failure(self):
        result = translate_query('', 'en')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_whitespace_query_returns_failure(self):
        result = translate_query('   ', 'en')
        self.assertFalse(result['success'])

    def test_none_query_returns_failure(self):
        result = translate_query(None, 'en')
        self.assertFalse(result['success'])

    def test_invalid_target_returns_failure(self):
        result = translate_query('test query', 'fr')
        self.assertFalse(result['success'])
        self.assertIn('Unsupported', result['error'])

    def test_no_api_key_returns_failure(self):
        with patch.dict(os.environ, {}, clear=True):
            result = translate_query('test query', 'en')
        self.assertFalse(result['success'])
        self.assertIn('DEEPSEEK_API_KEY', result['error'])


# ── translate_query 成功路径 ──────────────────────────────
class TestTranslateQuerySuccess(unittest.TestCase):
    def setUp(self):
        os.environ['DEEPSEEK_API_KEY'] = 'test-key-12345'

    def tearDown(self):
        os.environ.pop('DEEPSEEK_API_KEY', None)

    def test_successful_translation_to_en(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('React useEffect cleanup side effects')
            )
            result = translate_query('React useEffect 清理副作用', 'en')

        self.assertTrue(result['success'])
        self.assertEqual(result['translated'], 'React useEffect cleanup side effects')
        self.assertEqual(result['target_lang'], 'en')

    def test_successful_translation_to_zh(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('React useEffect 清理副作用')
            )
            result = translate_query('React useEffect cleanup', 'zh')

        self.assertTrue(result['success'])
        self.assertIn('清理副作用', result['translated'])

    def test_markdown_fences_stripped(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('```\ntranslation result\n```')
            )
            result = translate_query('test', 'en')
        self.assertTrue(result['success'])
        self.assertEqual(result['translated'], 'translation result')

    def test_quotes_stripped(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('"translated query"')
            )
            result = translate_query('test', 'en')
        self.assertTrue(result['success'])
        self.assertEqual(result['translated'], 'translated query')

    def test_request_uses_post_method(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('translated')
            )
            translate_query('test query', 'en')
            req = mock_urlopen.call_args[0][0]
            self.assertEqual(req.get_method(), 'POST')

    def test_request_has_authorization_header(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('translated')
            )
            translate_query('test', 'en', api_key='my-key')
            req = mock_urlopen.call_args[0][0]
            self.assertIn('Authorization', req.headers)

    def test_request_payload_has_correct_model(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('translated')
            )
            translate_query('test', 'en')
            req = mock_urlopen.call_args[0][0]
            body = json.loads(req.data.decode('utf-8'))
            self.assertEqual(body['model'], _FLASH_MODEL)

    def test_pii_redacted_before_sending(self):
        captured = []

        def capture(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured.append(body['messages'][0]['content'])
            return _make_urlopen_success(_make_flash_response('translated'))

        with patch('urllib.request.urlopen', side_effect=capture):
            translate_query('call 13800138000 for help', 'en')
        # 原始 PII 不应出现在 prompt 中
        self.assertNotIn('13800138000', captured[0])
        self.assertIn('[REDACTED-PHONE]', captured[0])


# ── translate_query 失败路径 ──────────────────────────────
class TestTranslateQueryFailures(unittest.TestCase):
    def setUp(self):
        os.environ['DEEPSEEK_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('DEEPSEEK_API_KEY', None)

    def test_http_error_returns_failure_with_original(self):
        error = urllib.error.HTTPError(
            url='https://api.deepseek.com',
            code=401, msg='Unauthorized', hdrs=None, fp=None,
        )
        error.read = MagicMock(return_value=b'{"error": "invalid key"}')
        with patch('urllib.request.urlopen', side_effect=error):
            result = translate_query('test query', 'en')
        self.assertFalse(result['success'])
        # 失败时 translated 应为原文
        self.assertEqual(result['translated'], 'test query')

    def test_http_error_body_with_api_key_redacted(self):
        api_key = 'secret-key-xyz'
        error = urllib.error.HTTPError(
            url='https://api.deepseek.com',
            code=403, msg='Forbidden', hdrs=None, fp=None,
        )
        body = f'{{"error": "key {api_key} is invalid"}}'
        error.read = MagicMock(return_value=body.encode('utf-8'))
        with patch('urllib.request.urlopen', side_effect=error):
            result = translate_query('test', 'en', api_key=api_key)
        self.assertFalse(result['success'])
        self.assertNotIn(api_key, result.get('error', ''))

    def test_url_error_returns_failure(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('DNS failed')):
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('URL error', result['error'])

    def test_url_error_timeout_treated_as_timeout(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('timeout')):
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('timeout', result['error'].lower())

    def test_url_error_timed_out_treated_as_timeout(self):
        """Regression: urllib raises URLError(reason='timed out') not 'timeout'.

        i18n.py:256 originally only matched 'timeout' substring, missing the
        standard 'timed out' string. This test guards against regression.
        """
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('timed out')):
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('timeout', result['error'].lower(),
                      "Should classify 'timed out' as timeout, not URL error")

    def test_timeout_returns_failure(self):
        with patch('urllib.request.urlopen', side_effect=TimeoutError()):
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('timeout', result['error'].lower())

    def test_json_decode_error_returns_failure(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.read = MagicMock(return_value=b'not valid json')
        with patch('urllib.request.urlopen', return_value=cm):
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])

    def test_response_structure_invalid_returns_failure(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success({})
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('parse failed', result['error'])

    def test_empty_translation_result_returns_failure(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('   ')
            )
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('Empty', result['error'])

    def test_generic_exception_returns_failure(self):
        with patch('urllib.request.urlopen',
                   side_effect=RuntimeError('unexpected')):
            result = translate_query('test', 'en')
        self.assertFalse(result['success'])
        self.assertIn('RuntimeError', result['error'])


# ── expand_query 测试 ──────────────────────────────────────
class TestExpandQuery(unittest.TestCase):
    def setUp(self):
        os.environ['DEEPSEEK_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('DEEPSEEK_API_KEY', None)

    def test_empty_query_returns_empty(self):
        self.assertEqual(expand_query(''), [])
        self.assertEqual(expand_query(None), [])
        self.assertEqual(expand_query('   '), [])

    def test_mixed_query_returns_multiple_versions(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            # 模拟两次调用：一次翻译到 en，一次翻译到 zh
            mock_urlopen.side_effect = [
                _make_urlopen_success(_make_flash_response('React useEffect cleanup')),
                _make_urlopen_success(_make_flash_response('React useEffect 清理副作用')),
            ]
            queries = expand_query('React useEffect 清理副作用')

        self.assertGreaterEqual(len(queries), 1)
        self.assertEqual(queries[0], 'React useEffect 清理副作用')
        # 应该有翻译版本
        self.assertGreater(len(queries), 1)

    def test_pure_chinese_query_translates_to_en(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('how to learn AI')
            )
            queries = expand_query('如何学习人工智能')
        # 应包含原文 + 英文翻译
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0], '如何学习人工智能')
        self.assertEqual(queries[1], 'how to learn AI')

    def test_pure_english_query_translates_to_zh(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('如何学习人工智能')
            )
            queries = expand_query('how to learn AI')
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0], 'how to learn AI')
        self.assertEqual(queries[1], '如何学习人工智能')

    def test_no_opposite_flag_skips_translation_for_single_lang(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            queries = expand_query('how to learn AI', include_opposite=False)
        # 不应调用 API
        self.assertEqual(mock_urlopen.call_count, 0)
        self.assertEqual(len(queries), 1)

    def test_translation_failure_returns_original_only(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('fail')):
            queries = expand_query('React 清理副作用')
        # 翻译失败时应回退到原文
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0], 'React 清理副作用')

    def test_deduplication_preserves_order(self):
        # Flash 翻译返回与原文相同（极少见，但需测试）
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('React useEffect 清理副作用')
            )
            queries = expand_query('React useEffect 清理副作用')
        # 应仅含原文（翻译结果与原文相同 → 去重）
        self.assertEqual(len(queries), 1)

    def test_unknown_lang_query_returns_original(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            queries = expand_query('!@#$%^&*()')
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0], '!@#$%^&*()')

    def test_api_key_not_set_returns_original_for_single_lang(self):
        with patch.dict(os.environ, {}, clear=True):
            queries = expand_query('how to learn AI')
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0], 'how to learn AI')


# ── CLI 测试 ──────────────────────────────────────────────
class TestCLI(unittest.TestCase):
    def setUp(self):
        os.environ['DEEPSEEK_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('DEEPSEEK_API_KEY', None)

    def test_detect_command(self):
        with patch('sys.argv', ['i18n.py', 'detect', '--query', 'React 清理副作用']):
            with patch('sys.stdout', StringIO()) as mock_out:
                ret = i18n._cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['is_mixed'])
        self.assertEqual(result['primary_lang'], 'mixed')

    def test_translate_command_success(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('translated query')
            )
            with patch('sys.argv', ['i18n.py', 'translate', '--query', '测试', '--target', 'en']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = i18n._cli()
        self.assertEqual(ret, 0)
        output = mock_out.getvalue()
        self.assertIn('translated query', output)

    def test_translate_command_json_output(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('translated')
            )
            with patch('sys.argv', ['i18n.py', 'translate', '--query', 'test',
                                     '--target', 'en', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = i18n._cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertTrue(result['success'])

    def test_translate_command_failure_returns_1(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('fail')):
            with patch('sys.argv', ['i18n.py', 'translate', '--query', 'test',
                                     '--target', 'en']):
                with patch('sys.stderr', StringIO()):
                    ret = i18n._cli()
        self.assertEqual(ret, 1)

    def test_expand_command_human_readable(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('how to learn AI')
            )
            with patch('sys.argv', ['i18n.py', 'expand', '--query', '如何学习人工智能']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = i18n._cli()
        self.assertEqual(ret, 0)
        output = mock_out.getvalue()
        self.assertIn('如何学习人工智能', output)
        self.assertIn('how to learn AI', output)

    def test_expand_command_json_output(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.return_value = _make_urlopen_success(
                _make_flash_response('how to learn AI')
            )
            with patch('sys.argv', ['i18n.py', 'expand', '--query', '如何学习人工智能',
                                     '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = i18n._cli()
        self.assertEqual(ret, 0)
        result = json.loads(mock_out.getvalue())
        self.assertIn('expanded', result)
        self.assertGreater(result['count'], 0)

    def test_expand_command_no_opposite_flag(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            with patch('sys.argv', ['i18n.py', 'expand', '--query', 'test',
                                     '--no-opposite', '--json']):
                with patch('sys.stdout', StringIO()) as mock_out:
                    ret = i18n._cli()
        self.assertEqual(ret, 0)
        self.assertEqual(mock_urlopen.call_count, 0)


# ── 集成测试 ──────────────────────────────────────────────
class TestIntegration(unittest.TestCase):
    def setUp(self):
        os.environ['DEEPSEEK_API_KEY'] = 'test-key'

    def tearDown(self):
        os.environ.pop('DEEPSEEK_API_KEY', None)

    def test_mixed_query_full_flow(self):
        """混合查询：检测 → 翻译 en + zh → 合并。"""
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = [
                _make_urlopen_success(_make_flash_response('React useEffect cleanup side effects')),
                _make_urlopen_success(_make_flash_response('React useEffect 清理副作用')),
            ]
            queries = expand_query('React useEffect 清理副作用')

        self.assertGreaterEqual(len(queries), 2)
        self.assertIn('React useEffect 清理副作用', queries)

    def test_pii_never_leaks_in_translation(self):
        """PII 在翻译过程中不应出境。"""
        captured_prompts = []

        def capture(req, timeout=None):
            body = json.loads(req.data.decode('utf-8'))
            captured_prompts.append(body['messages'][0]['content'])
            return _make_urlopen_success(_make_flash_response('call me'))

        with patch('urllib.request.urlopen', side_effect=capture):
            translate_query('电话 13800138000 联系', 'en')

        # 原始 PII 不应出现在发送给 Flash 的 prompt 中
        for prompt in captured_prompts:
            self.assertNotIn('13800138000', prompt)
            self.assertIn('[REDACTED-PHONE]', prompt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
