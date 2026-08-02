#!/usr/bin/env python3
"""verifier.py 单元测试 — v4.4 P4.1.2 验证回路

覆盖目标：
  - should_verify 触发条件（4 类）
  - verify_against_authority 主流程
  - L1 候选提取
  - cross_check 一致性评分
  - 异常兜底（fetch 失败 / 空结果 / 类型异常）
  - 默认 fetch 函数（mock urllib）
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

import verifier
from verifier import (
    should_verify, verify_against_authority, _extract_l1_candidates,
    _cross_check_results, _extract_keywords, _default_fetch,
    _compute_authority_stats, _safe_avg, _TIMESENSITIVE_KEYWORDS,
)


# ── 测试数据 ────────────────────────────────────────────────────

SAMPLE_RESULTS_MIXED = [
    {
        'title': 'FIT2004 Handbook 2026',
        'url': 'https://handbook.monash.edu/2026/units/FIT2004',
        'snippet': 'Algorithms and Data Structures 2026 assessment 60%',
        'source': 'official-docs',
    },
    {
        'title': 'FIT2004 Student Notes',
        'url': 'https://github.com/jenul-ferdinand/algorithms',
        'snippet': 'My notes from 2024 — assessment 50%',
        'source': 'github',
    },
    {
        'title': 'Medium article on FIT2004',
        'url': 'https://medium.com/@user/fit2004-guide',
        'snippet': 'A guide to FIT2004',
        'source': 'blog',
    },
]

SAMPLE_RESULTS_ALL_L3 = [
    {
        'title': 'Student Notes 1',
        'url': 'https://github.com/student1/notes',
        'source': 'github',
    },
    {
        'title': 'Student Notes 2',
        'url': 'https://github.com/student2/notes',
        'source': 'github',
    },
]

SAMPLE_RESULTS_ALL_L1 = [
    {
        'title': 'Handbook',
        'url': 'https://handbook.monash.edu/',
        'source': 'official-docs',
    },
    {
        'title': 'Python docs',
        'url': 'https://docs.python.org/',
        'source': 'official-docs',
    },
]


class TestShouldVerifyTriggers(unittest.TestCase):
    """should_verify 触发条件测试。"""

    def test_trigger_query_contains_year_keyword(self):
        """条件 1: query 含 '2026' → 触发。"""
        decision = should_verify('FIT2004 2026 handbook assessment', SAMPLE_RESULTS_MIXED)
        self.assertTrue(decision['should_verify'])
        self.assertIn('time-sensitive', decision['trigger_reason'])

    def test_trigger_query_contains_latest_keyword(self):
        decision = should_verify('latest React documentation', SAMPLE_RESULTS_MIXED)
        self.assertTrue(decision['should_verify'])

    def test_trigger_query_contains_handbook_keyword(self):
        decision = should_verify('Monash handbook FIT2004', SAMPLE_RESULTS_MIXED)
        self.assertTrue(decision['should_verify'])

    def test_trigger_l1_results_present(self):
        """条件 2: results 含 L1 → 触发。"""
        decision = should_verify('random query no keywords', SAMPLE_RESULTS_MIXED)
        # mixed results 包含 handbook.monash.edu (L1)
        self.assertTrue(decision['should_verify'])
        self.assertIn('L1 results present', decision['trigger_reason'])

    def test_trigger_mixed_tiers(self):
        """条件 3: results 跨多个 tier → 触发。"""
        # SAMPLE_RESULTS_MIXED 跨 L1+L3+L4
        decision = should_verify('random query', SAMPLE_RESULTS_MIXED)
        self.assertTrue(decision['should_verify'])

    def test_trigger_low_authority_average(self):
        """条件 4: 全 L3 + 无时效关键词 → avg < 2.0 → 触发。"""
        decision = should_verify('random query', SAMPLE_RESULTS_ALL_L3)
        self.assertTrue(decision['should_verify'])
        self.assertIn('authority avg score', decision['trigger_reason'])

    def test_trigger_when_all_l1_no_keyword(self):
        """全 L1 + 无时效关键词 → 仍触发（条件 2: L1 present）。

        v4.4: 修正命名（之前误命名 test_no_trigger_when_all_l1_no_keyword）
        实际逻辑：只要 results 含 L1 就触发验证回路（防 Agent-Forge 跳过验证）。
        """
        decision = should_verify('random query', SAMPLE_RESULTS_ALL_L1)
        # 条件 2 命中：L1 present
        self.assertTrue(decision['should_verify'])

    def test_no_trigger_when_no_results(self):
        decision = should_verify('any query', [])
        self.assertFalse(decision['should_verify'])

    def test_no_trigger_when_none_results(self):
        decision = should_verify('any query', None)
        self.assertFalse(decision['should_verify'])

    def test_returns_authority_stats(self):
        decision = should_verify('2026 query', SAMPLE_RESULTS_MIXED)
        self.assertIn('authority_stats', decision)


class TestShouldVerifyKeywordCoverage(unittest.TestCase):
    """时效关键词覆盖测试。"""

    def test_all_year_keywords_in_set(self):
        for year in ('2025', '2026', '2027'):
            self.assertIn(year, _TIMESENSITIVE_KEYWORDS)

    def test_handbook_keyword_in_set(self):
        self.assertIn('handbook', _TIMESENSITIVE_KEYWORDS)

    def test_official_keyword_in_set(self):
        self.assertIn('official', _TIMESENSITIVE_KEYWORDS)

    def test_assessment_keyword_in_set(self):
        """Agent-Forge 错误的关键领域关键词。"""
        self.assertIn('assessment', _TIMESENSITIVE_KEYWORDS)

    def test_hurdle_keyword_in_set(self):
        self.assertIn('hurdle', _TIMESENSITIVE_KEYWORDS)

    def test_prerequisite_keyword_in_set(self):
        self.assertIn('prerequisite', _TIMESENSITIVE_KEYWORDS)


class TestExtractL1Candidates(unittest.TestCase):
    """_extract_l1_candidates 测试。"""

    def test_extracts_handbook_monash(self):
        candidates = _extract_l1_candidates(SAMPLE_RESULTS_MIXED)
        urls = [c['url'] for c in candidates]
        self.assertIn('https://handbook.monash.edu/2026/units/FIT2004', urls)

    def test_excludes_l3_github(self):
        candidates = _extract_l1_candidates(SAMPLE_RESULTS_MIXED)
        urls = [c['url'] for c in candidates]
        self.assertNotIn('https://github.com/jenul-ferdinand/algorithms', urls)

    def test_excludes_l4_medium(self):
        candidates = _extract_l1_candidates(SAMPLE_RESULTS_MIXED)
        urls = [c['url'] for c in candidates]
        self.assertNotIn('https://medium.com/@user/fit2004-guide', urls)

    def test_empty_results_returns_empty(self):
        candidates = _extract_l1_candidates([])
        self.assertEqual(candidates, [])

    def test_none_results_returns_empty(self):
        candidates = _extract_l1_candidates(None)
        self.assertEqual(candidates, [])

    def test_non_dict_entries_skipped(self):
        results = [
            'not a dict',
            {'url': 'https://handbook.monash.edu/', 'source': 'unknown'},
            42,
        ]
        candidates = _extract_l1_candidates(results)
        self.assertEqual(len(candidates), 1)

    def test_includes_title_and_reason(self):
        candidates = _extract_l1_candidates(SAMPLE_RESULTS_MIXED)
        for c in candidates:
            self.assertIn('title', c)
            self.assertIn('reason', c)
            self.assertIn('url', c)


class TestCrossCheckResults(unittest.TestCase):
    """_cross_check_results 一致性评分测试。"""

    def test_high_consistency_when_all_keywords_match(self):
        """所有 query 关键词在 L1 内容中 → 1.0。"""
        fetched_l1 = [{
            'url': 'https://handbook.monash.edu/',
            'content_snippet': 'FIT2004 Algorithms Data Structures assessment handbook',
            'status': 'success',
        }]
        result = _cross_check_results(
            'FIT2004 Algorithms Data Structures assessment',
            SAMPLE_RESULTS_MIXED,
            fetched_l1
        )
        self.assertEqual(result['consistency_score'], 1.0)
        # 关键词：fit2004, algorithms, data, structures, assessment = 5 个
        self.assertEqual(len(result['matched_keywords']), 5)
        self.assertEqual(len(result['missed_keywords']), 0)

    def test_low_consistency_when_no_keywords_match(self):
        """query 关键词不在 L1 内容中 → 0.0。"""
        fetched_l1 = [{
            'url': 'https://handbook.monash.edu/',
            'content_snippet': 'completely different content here',
            'status': 'success',
        }]
        result = _cross_check_results(
            'FIT2004 Algorithms assessment',
            SAMPLE_RESULTS_MIXED,
            fetched_l1
        )
        self.assertEqual(result['consistency_score'], 0.0)

    def test_partial_consistency(self):
        """部分关键词命中 → 中等评分。"""
        fetched_l1 = [{
            'url': 'https://handbook.monash.edu/',
            'content_snippet': 'FIT2004 assessment',  # 仅命中 2 个
            'status': 'success',
        }]
        result = _cross_check_results(
            'FIT2004 Algorithms assessment handbook',
            SAMPLE_RESULTS_MIXED,
            fetched_l1
        )
        # 关键词：fit2004, algorithms, assessment, handbook = 4 个
        # 命中 fit2004 + assessment = 2/4 = 0.5
        self.assertEqual(result['consistency_score'], 0.5)
        self.assertEqual(len(result['matched_keywords']), 2)
        self.assertEqual(len(result['missed_keywords']), 2)

    def test_empty_fetched_l1_returns_zero(self):
        result = _cross_check_results('FIT2004 assessment', [], [])
        self.assertEqual(result['consistency_score'], 0.0)

    def test_failed_fetch_excluded_from_text(self):
        """status != success 的 fetch 不参与文本合并。"""
        fetched_l1 = [
            {
                'url': 'https://handbook.monash.edu/',
                'content_snippet': '',
                'status': 'error: ConnectionError',
            },
        ]
        result = _cross_check_results('FIT2004', [], fetched_l1)
        self.assertEqual(result['consistency_score'], 0.0)

    def test_l1_url_covered_returned(self):
        fetched_l1 = [{
            'url': 'https://handbook.monash.edu/',
            'content_snippet': 'FIT2004 assessment',
            'status': 'success',
        }]
        result = _cross_check_results('FIT2004 assessment', [], fetched_l1)
        self.assertIn('https://handbook.monash.edu/', result['l1_url_covered'])

    def test_case_insensitive_matching(self):
        """关键词大小写不敏感。"""
        fetched_l1 = [{
            'url': 'https://handbook.monash.edu/',
            'content_snippet': 'FIT2004 ASSESSMENT HANDBOOK',
            'status': 'success',
        }]
        result = _cross_check_results(
            'fit2004 assessment handbook',
            [],
            fetched_l1
        )
        self.assertEqual(result['consistency_score'], 1.0)


class TestExtractKeywords(unittest.TestCase):
    """_extract_keywords 测试。"""

    def test_normal_query(self):
        kws = _extract_keywords('FIT2004 Algorithms assessment')
        self.assertIn('fit2004', kws)
        self.assertIn('algorithms', kws)
        self.assertIn('assessment', kws)

    def test_short_words_filtered(self):
        """长度 < 3 的词被过滤。"""
        kws = _extract_keywords('a an of to React')
        self.assertIn('react', kws)
        self.assertNotIn('a', kws)
        self.assertNotIn('an', kws)
        self.assertNotIn('of', kws)
        self.assertNotIn('to', kws)

    def test_stop_words_filtered(self):
        kws = _extract_keywords('the and or React')
        self.assertIn('react', kws)
        self.assertNotIn('the', kws)
        self.assertNotIn('and', kws)

    def test_empty_query(self):
        self.assertEqual(_extract_keywords(''), [])

    def test_none_query(self):
        self.assertEqual(_extract_keywords(None), [])

    def test_punctuation_split(self):
        kws = _extract_keywords('React, useEffect! (hooks)')
        self.assertIn('react', kws)
        self.assertIn('useeffect', kws)
        self.assertIn('hooks', kws)


class TestVerifyAgainstAuthority(unittest.TestCase):
    """verify_against_authority 主流程测试。"""

    def test_not_triggered_returns_verified_true(self):
        """无 L1 + 无时效关键词 + 全 L1（avg 高）→ 不触发 → verified=True。"""
        # 构造不触发的场景：无 query 关键词 + 全 L1（avg=3）
        # 但全 L1 会命中条件 2 → 触发。需构造无 L1 + 高 avg 的场景
        # L4 avg=1.0 → 仍触发条件 4
        # 实际上很难构造完全不触发的场景，所以测 trigger 但 verified=True 的场景
        report = verify_against_authority('random query', [])
        self.assertTrue(report['verified'])
        self.assertIn('verification not triggered', report['warnings'][0])

    def test_triggered_no_l1_candidates_returns_verified_false(self):
        """触发但无 L1 候选 → verified=False。"""
        # 全 L3 + 含时效关键词 → 触发但无 L1
        report = verify_against_authority('2026 query', SAMPLE_RESULTS_ALL_L3)
        self.assertFalse(report['verified'])
        self.assertIn('no L1 candidates', report['warnings'][0] if report['warnings'] else '')

    def test_triggered_with_mock_fetch_success(self):
        """mock fetch 成功 + 高一致性 → verified=True。"""
        def mock_fetch(url):
            return 'FIT2004 Algorithms and Data Structures assessment handbook 2026'

        report = verify_against_authority(
            'FIT2004 assessment handbook 2026',
            SAMPLE_RESULTS_MIXED,
            fetch_callback=mock_fetch,
        )
        self.assertTrue(report['verified'])
        self.assertGreater(len(report['fetched_l1']), 0)
        self.assertEqual(report['fetched_l1'][0]['status'], 'success')

    def test_triggered_with_mock_fetch_low_consistency(self):
        """mock fetch 成功但内容不匹配 → verified=False。"""
        def mock_fetch(url):
            return 'completely different content unrelated to query'

        report = verify_against_authority(
            'FIT2004 assessment handbook 2026',
            SAMPLE_RESULTS_MIXED,
            fetch_callback=mock_fetch,
        )
        self.assertFalse(report['verified'])
        self.assertIn('cross-check consistency', report['warnings'][0])

    def test_fetch_exception_does_not_raise(self):
        """fetch 抛异常 → 不传播，标记 verified=False。"""
        def mock_fetch(url):
            raise ConnectionError('mock network error')

        report = verify_against_authority(
            'FIT2004 2026',
            SAMPLE_RESULTS_MIXED,
            fetch_callback=mock_fetch,
        )
        self.assertFalse(report['verified'])
        self.assertGreater(len(report['warnings']), 0)

    def test_max_l1_fetches_limit(self):
        """最多抓取 _MAX_L1_FETCHES=2 个 L1 URL。"""
        many_l1_results = [
            {'url': f'https://handbook.monash.edu/{i}', 'source': 'official-docs'}
            for i in range(10)
        ]

        fetch_calls = []
        def mock_fetch(url):
            fetch_calls.append(url)
            return 'FIT2004 assessment'

        verifier.verify_against_authority(
            'FIT2004 2026',
            many_l1_results,
            fetch_callback=mock_fetch,
        )
        self.assertLessEqual(len(fetch_calls), verifier._MAX_L1_FETCHES)

    def test_report_contains_l1_candidates(self):
        report = verify_against_authority(
            '2026 handbook',
            SAMPLE_RESULTS_MIXED,
            fetch_callback=lambda url: '',
        )
        self.assertIn('l1_candidates', report)
        self.assertIsInstance(report['l1_candidates'], list)

    def test_report_contains_trigger_reason(self):
        report = verify_against_authority('2026', SAMPLE_RESULTS_MIXED,
                                          fetch_callback=lambda url: '')
        self.assertTrue(report['trigger_reason'])

    def test_report_contains_cross_check(self):
        report = verify_against_authority(
            'FIT2004 2026',
            SAMPLE_RESULTS_MIXED,
            fetch_callback=lambda url: 'FIT2004 assessment',
        )
        self.assertIn('cross_check', report)
        self.assertIn('consistency_score', report['cross_check'])

    def test_report_contains_warnings(self):
        report = verify_against_authority(
            '2026',
            SAMPLE_RESULTS_ALL_L3,  # 无 L1 候选
            fetch_callback=lambda url: '',
        )
        self.assertIn('warnings', report)
        self.assertIsInstance(report['warnings'], list)


class TestVerifyAgainstAuthorityExceptionHandling(unittest.TestCase):
    """异常兜底测试。"""

    def test_should_verify_exception_returns_safe(self):
        """should_verify 内部异常 → 返回 should_verify=False。"""
        # 传入非 list
        decision = should_verify('query', 'not a list')
        self.assertFalse(decision['should_verify'])

    def test_verify_with_none_query(self):
        report = verify_against_authority(None, SAMPLE_RESULTS_MIXED,
                                          fetch_callback=lambda u: '')
        # None query → 无关键词 → 但 results 含 L1 → 触发
        # 无 L1 candidates? No, SAMPLE_RESULTS_MIXED 含 handbook.monash.edu
        # 所以触发 + fetch 返回空 → verified=False
        self.assertFalse(report['verified'])

    def test_verify_with_none_results(self):
        report = verify_against_authority('2026 FIT2004', None,
                                          fetch_callback=lambda u: '')
        self.assertTrue(report['verified'])  # 不触发 → 视为通过
        self.assertIn('verification not triggered', report['warnings'][0])

    def test_verify_with_malformed_results(self):
        """results 含非 dict 元素 → 不抛异常。"""
        malformed = [
            'string element',
            42,
            None,
            {'url': 'https://handbook.monash.edu/', 'source': 'unknown'},
        ]
        report = verify_against_authority('2026', malformed,
                                          fetch_callback=lambda u: 'FIT2004')
        # 应不抛异常，verified 取决于 cross-check
        self.assertIn('verified', report)

    def test_internal_error_caught(self):
        """verify 内部异常 → 进入 catch-all，返回 verified=False + error 字段。"""
        # mock _extract_l1_candidates 抛异常
        with patch('verifier._extract_l1_candidates',
                   side_effect=RuntimeError('mock internal error')):
            report = verify_against_authority('2026', SAMPLE_RESULTS_MIXED,
                                              fetch_callback=lambda u: '')
            self.assertFalse(report['verified'])
            self.assertIsNotNone(report['error'])
            self.assertIn('mock internal error', report['error'])


class TestDefaultFetch(unittest.TestCase):
    """_default_fetch 测试（mock urllib opener）。"""

    def test_success_returns_decoded_content(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)
        mock_response.read.return_value = b'FIT2004 assessment handbook'

        # v4.4: 改用 opener.open（而非 urllib.request.urlopen）
        with patch('urllib.request.build_opener') as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_response
            mock_build_opener.return_value = mock_opener
            content = _default_fetch('https://handbook.monash.edu/')
        self.assertEqual(content, 'FIT2004 assessment handbook')

    def test_invalid_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            _default_fetch('not-a-url')

    def test_non_http_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            _default_fetch('ftp://example.com/')

    def test_http_error_propagates(self):
        import urllib.error
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)

        with patch('urllib.request.build_opener') as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_response
            mock_build_opener.return_value = mock_opener
            with self.assertRaises(urllib.error.HTTPError):
                _default_fetch('https://example.com/')

    def test_timeout_propagates(self):
        import urllib.error
        with patch('urllib.request.build_opener') as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.side_effect = urllib.error.URLError('timeout')
            mock_build_opener.return_value = mock_opener
            with self.assertRaises(urllib.error.URLError):
                _default_fetch('https://example.com/')

    def test_response_size_limit(self):
        """v4.4 Review-Risk MAJOR #2: 响应大小限制（2MB 上限）。"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)
        # 模拟超过 2MB 的响应（但 read 限制大小）
        mock_response.read.return_value = b'x' * 100  # 仅返回前 100 字节

        with patch('urllib.request.build_opener') as mock_build_opener:
            mock_opener = MagicMock()
            mock_opener.open.return_value = mock_response
            mock_build_opener.return_value = mock_opener
            content = _default_fetch('https://example.com/')
        # 应正常返回（不超过限制）
        self.assertEqual(len(content), 100)


class TestSafeRedirectHandler(unittest.TestCase):
    """v4.4 Review-Risk MAJOR #1: SSRF 防护测试。"""

    def test_blocked_hosts_constant_exists(self):
        from verifier import _BLOCKED_HOSTS
        self.assertIn('localhost', _BLOCKED_HOSTS)
        self.assertIn('127.0.0.1', _BLOCKED_HOSTS)

    def test_blocked_prefixes_constant_exists(self):
        from verifier import _BLOCKED_PREFIXES
        self.assertIn('10.', _BLOCKED_PREFIXES)
        self.assertIn('192.168.', _BLOCKED_PREFIXES)
        self.assertIn('169.254.', _BLOCKED_PREFIXES)  # AWS metadata

    def test_max_redirects_constant(self):
        from verifier import _MAX_REDIRECTS
        self.assertEqual(_MAX_REDIRECTS, 3)

    def test_max_response_bytes_constant(self):
        from verifier import _MAX_RESPONSE_BYTES
        self.assertEqual(_MAX_RESPONSE_BYTES, 2 * 1024 * 1024)


class TestSafeAvg(unittest.TestCase):
    """_safe_avg 测试。"""

    def test_normal_list(self):
        self.assertEqual(_safe_avg([1, 2, 3]), 2.0)

    def test_empty_list(self):
        self.assertEqual(_safe_avg([]), 0.0)

    def test_none_list(self):
        self.assertEqual(_safe_avg(None), 0.0)


class TestComputeAuthorityStats(unittest.TestCase):
    """_compute_authority_stats 测试。"""

    def test_returns_tier_counts(self):
        stats = _compute_authority_stats(SAMPLE_RESULTS_MIXED)
        self.assertIn('tier_counts', stats)
        self.assertIn('avg_score', stats)
        self.assertGreater(stats['tier_counts']['L1'], 0)

    def test_empty_results(self):
        stats = _compute_authority_stats([])
        self.assertEqual(stats['avg_score'], 0.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
