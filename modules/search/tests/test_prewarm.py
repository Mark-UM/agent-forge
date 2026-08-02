#!/usr/bin/env python3
"""prewarm.py 单元测试 — 缓存预热。

覆盖维度：
  - 边界输入：无日志目录 / 空目录 / 无效 JSON / 无效时间戳
  - _all_log_files: 文件发现 + 排序
  - _load_history_entries: 时间窗口过滤 / UnicodeDecodeError
  - _extract_query_stats: 频率统计 / 最新条目选择 / 无 top_results 跳过
  - _load_cache / _save_cache_atomic: 原子写入 + 失败处理
  - prewarm_cache: 完整流程 / dry_run / 已缓存跳过 / 无结果跳过
  - show_stats: 统计报告
  - CLI: run / stats / --dry-run / --json
"""
import sys
import os
import json
import time
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from io import StringIO
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import prewarm
from prewarm import (
    _all_log_files, _load_history_entries, _extract_query_stats,
    _load_cache, _save_cache_atomic, _cache_key,
    prewarm_cache, show_stats, _cli,
    DEFAULT_TOP_N, DEFAULT_DAYS_WINDOW, CACHE_TTL,
)


# ── 测试数据 ────────────────────────────────────────────────────
SAMPLE_ENTRY_FULL = {
    'timestamp': '2026-07-20T10:00:00',
    'query': 'React useEffect cleanup',
    'score': 8.5,
    'satisfied': True,
    'saved': False,
    'results_count': 5,
    'layers_used': ['duckduckgo', 'searxng'],
    'location': 'zhuhai',
    'layer_hint': '',
    'top_results': [
        {
            'title': 'React useEffect Cleanup',
            'url': 'https://react.dev/docs/hooks-effect',
            'snippet': 'Cleanup function runs on unmount.',
            'source': 'official-docs',
        },
    ],
}

SAMPLE_ENTRY_SAVED = {
    'timestamp': '2026-07-19T15:30:00',
    'query': 'DeepSeek V4 Pro',
    'score': 9.0,
    'satisfied': True,
    'saved': True,
    'results_count': 3,
    'layers_used': ['searxng'],
    'location': 'zhuhai',
    'layer_hint': '',
    'top_results': [
        {'title': 'DeepSeek V4', 'url': 'https://deepseek.com',
         'snippet': 'Official site', 'source': 'official-docs'},
    ],
}

SAMPLE_ENTRY_NO_RESULTS = {
    'timestamp': '2026-07-18T08:00:00',
    'query': 'React useEffect cleanup',  # 同 query 但无 top_results
    'score': 0,
    'satisfied': False,
    'saved': False,
    'results_count': 0,
    'layers_used': ['duckduckgo'],
    'location': 'zhuhai',
    'layer_hint': '',
    'top_results': [],
}

SAMPLE_ENTRY_OLD = {
    'timestamp': '2026-06-01T08:00:00',  # 远早于 30 天
    'query': 'Old query',
    'score': 5.0,
    'satisfied': False,
    'saved': False,
    'results_count': 1,
    'layers_used': ['duckduckgo'],
    'location': 'zhuhai',
    'layer_hint': '',
    'top_results': [
        {'title': 'Old', 'url': 'https://example.com/old',
         'snippet': 'old', 'source': 'blog'},
    ],
}


def _make_log_dir_with_entries(entries, dir_path):
    """在指定目录下写入 search_history.YYYY-MM-DD.jsonl 文件。"""
    today = datetime.now().strftime('%Y-%m-%d')
    fname = f'search_history.{today}.jsonl'
    fpath = os.path.join(dir_path, fname)
    with open(fpath, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    return fpath


class TestAllLogFiles(unittest.TestCase):
    """_all_log_files 测试。"""

    def test_no_log_dir_returns_empty(self):
        with patch('prewarm._LOG_DIR', '/nonexistent/path/xyz'):
            result = _all_log_files()
        self.assertEqual(result, [])

    def test_empty_dir_returns_empty(self):
        tmp = tempfile.mkdtemp()
        try:
            with patch('prewarm._LOG_DIR', tmp):
                result = _all_log_files()
            self.assertEqual(result, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_returns_matching_files_sorted_reverse(self):
        tmp = tempfile.mkdtemp()
        try:
            for date in ['2026-07-20', '2026-07-19', '2026-07-18']:
                fpath = os.path.join(tmp, f'search_history.{date}.jsonl')
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write('')
            with patch('prewarm._LOG_DIR', tmp):
                result = _all_log_files()
            self.assertEqual(len(result), 3)
            # 倒序：最新在前
            self.assertIn('2026-07-20', result[0])
            self.assertIn('2026-07-19', result[1])
            self.assertIn('2026-07-18', result[2])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ignores_non_matching_files(self):
        tmp = tempfile.mkdtemp()
        try:
            for name in ['other.jsonl', 'search_history.txt',
                         'history.jsonl']:
                with open(os.path.join(tmp, name), 'w',
                          encoding='utf-8') as f:
                    f.write('')
            with open(os.path.join(tmp, 'search_history.2026-07-20.jsonl'),
                      'w', encoding='utf-8') as f:
                f.write('')
            with patch('prewarm._LOG_DIR', tmp):
                result = _all_log_files()
            self.assertEqual(len(result), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLoadHistoryEntries(unittest.TestCase):
    """_load_history_entries 测试。"""

    def test_no_files_returns_empty(self):
        self.assertEqual(_load_history_entries([]), [])

    def test_loads_all_entries_no_filter(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_history_entries(files)
            self.assertEqual(len(entries), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_days_filter_excludes_old_entries(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_OLD], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_history_entries(files, days_limit=30)
            # SAMPLE_ENTRY_OLD (2026-06-01) 应被排除
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['query'], 'React useEffect cleanup')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invalid_json_line_skipped(self):
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(json.dumps(SAMPLE_ENTRY_FULL) + '\n')
                f.write('this is not json\n')
                f.write(json.dumps(SAMPLE_ENTRY_SAVED) + '\n')
            with patch('prewarm._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_history_entries(files)
            self.assertEqual(len(entries), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unicode_decode_error_skipped(self):
        """非 UTF-8 字节序列导致整个文件被跳过。"""
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            with open(fpath, 'wb') as f:
                f.write(json.dumps(SAMPLE_ENTRY_FULL).encode('utf-8') + b'\n')
                f.write(b'\xff\xfe invalid utf-8\n')
            with patch('prewarm._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_history_entries(files)
            # 整个文件因 UnicodeDecodeError 被跳过
            self.assertEqual(entries, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_io_error_skips_file(self):
        with patch('builtins.open',
                   side_effect=OSError('permission denied')):
            entries = _load_history_entries(['/fake/path.jsonl'])
        self.assertEqual(entries, [])

    def test_non_dict_entry_skipped(self):
        """JSON 是 list 或 scalar 时跳过。"""
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(json.dumps(SAMPLE_ENTRY_FULL) + '\n')
                f.write(json.dumps([1, 2, 3]) + '\n')  # list
                f.write('"just a string"\n')  # scalar
                f.write(json.dumps(SAMPLE_ENTRY_SAVED) + '\n')
            with patch('prewarm._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_history_entries(files)
            self.assertEqual(len(entries), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestExtractQueryStats(unittest.TestCase):
    """_extract_query_stats 测试。"""

    def test_basic_frequency_count(self):
        entries = [
            {'query': 'q1', 'timestamp': '2026-07-20T10:00:00',
             'top_results': [{'title': 'r1'}]},
            {'query': 'q1', 'timestamp': '2026-07-20T11:00:00',
             'top_results': [{'title': 'r1'}]},
            {'query': 'q2', 'timestamp': '2026-07-20T12:00:00',
             'top_results': [{'title': 'r2'}]},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(stats['q1']['count'], 2)
        self.assertEqual(stats['q2']['count'], 1)

    def test_latest_entry_selected(self):
        entries = [
            {'query': 'q1', 'timestamp': '2026-07-19T10:00:00',
             'top_results': [{'title': 'old'}]},
            {'query': 'q1', 'timestamp': '2026-07-20T10:00:00',
             'top_results': [{'title': 'new'}]},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(stats['q1']['latest_entry']['timestamp'],
                         '2026-07-20T10:00:00')
        self.assertEqual(stats['q1']['latest_entry']['top_results'][0]['title'],
                         'new')

    def test_entry_without_top_results_still_counted(self):
        """无 top_results 的条目仍计入频率，但 latest_entry 为 None。"""
        entries = [
            {'query': 'q1', 'timestamp': '2026-07-20T10:00:00',
             'top_results': []},
            {'query': 'q1', 'timestamp': '2026-07-20T11:00:00',
             'top_results': [{'title': 'r1'}]},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(stats['q1']['count'], 2)
        self.assertIsNotNone(stats['q1']['latest_entry'])

    def test_all_entries_without_results(self):
        entries = [
            {'query': 'q1', 'timestamp': '2026-07-20T10:00:00',
             'top_results': []},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(stats['q1']['count'], 1)
        self.assertIsNone(stats['q1']['latest_entry'])

    def test_empty_query_skipped(self):
        entries = [
            {'query': '', 'timestamp': '2026-07-20T10:00:00',
             'top_results': [{'title': 'r'}]},
            {'query': '   ', 'timestamp': '2026-07-20T11:00:00',
             'top_results': [{'title': 'r'}]},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(len(stats), 0)

    def test_first_and_last_seen_timestamps(self):
        entries = [
            {'query': 'q1', 'timestamp': '2026-07-19T10:00:00',
             'top_results': [{'title': 'r'}]},
            {'query': 'q1', 'timestamp': '2026-07-20T10:00:00',
             'top_results': [{'title': 'r'}]},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(stats['q1']['first_seen'], '2026-07-19T10:00:00')
        self.assertEqual(stats['q1']['last_seen'], '2026-07-20T10:00:00')

    def test_non_dict_top_results_treated_as_empty(self):
        entries = [
            {'query': 'q1', 'timestamp': '2026-07-20T10:00:00',
             'top_results': 'not a list'},
        ]
        stats = _extract_query_stats(entries)
        self.assertEqual(stats['q1']['count'], 1)
        self.assertIsNone(stats['q1']['latest_entry'])


class TestLoadSaveCache(unittest.TestCase):
    """_load_cache / _save_cache_atomic 测试。"""

    def test_load_nonexistent_returns_empty(self):
        with patch('prewarm.CACHE_FILE', '/nonexistent/cache.json'):
            result = _load_cache()
        self.assertEqual(result, {})

    def test_load_valid_cache(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            cache_data = {'key1': {'query': 'q1', 'cached_at': time.time()}}
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f)
            with patch('prewarm.CACHE_FILE', cache_file):
                result = _load_cache()
            self.assertEqual(result, cache_data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_invalid_json_returns_empty(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                f.write('not valid json')
            with patch('prewarm.CACHE_FILE', cache_file):
                result = _load_cache()
            self.assertEqual(result, {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_non_dict_json_returns_empty(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump([1, 2, 3], f)  # list, not dict
            with patch('prewarm.CACHE_FILE', cache_file):
                result = _load_cache()
            self.assertEqual(result, {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_save_atomic_writes_file(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            with patch('prewarm.CACHE_FILE', cache_file):
                with patch('prewarm._LOG_DIR', tmp):
                    result = _save_cache_atomic({'key1': 'value1'})
            self.assertTrue(result)
            self.assertTrue(os.path.exists(cache_file))
            # tmp 文件应被清理
            self.assertFalse(os.path.exists(cache_file + '.tmp'))
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(data, {'key1': 'value1'})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_save_atomic_overwrites_existing(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            # 先写旧内容
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({'old': True}, f)
            # 再写新内容
            with patch('prewarm.CACHE_FILE', cache_file):
                with patch('prewarm._LOG_DIR', tmp):
                    result = _save_cache_atomic({'new': True})
            self.assertTrue(result)
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.assertEqual(data, {'new': True})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCacheKey(unittest.TestCase):
    """_cache_key 测试。"""

    def test_basic_key_generation(self):
        key = _cache_key('React useEffect', 'zhuhai', '')
        self.assertEqual(key, 'react useeffect|zhuhai|')

    def test_case_insensitive(self):
        key1 = _cache_key('React', 'zhuhai', '')
        key2 = _cache_key('REACT', 'zhuhai', '')
        self.assertEqual(key1, key2)

    def test_whitespace_stripped(self):
        key1 = _cache_key('  React  ', 'zhuhai', '')
        key2 = _cache_key('React', 'zhuhai', '')
        self.assertEqual(key1, key2)

    def test_layer_hint_included(self):
        key = _cache_key('React', 'zhuhai', 'layer1')
        self.assertIn('layer1', key)


class TestPrewarmCache(unittest.TestCase):
    """prewarm_cache 主流程测试。"""

    def test_no_log_dir_returns_success_with_zero(self):
        with patch('prewarm._LOG_DIR', '/nonexistent/xyz'):
            with patch('prewarm.CACHE_FILE',
                       '/nonexistent/xyz/cache.json'):
                result = prewarm_cache(top_n=10)
        self.assertTrue(result['success'])
        self.assertEqual(result['total_queries_seen'], 0)
        self.assertEqual(result['prewarmed_count'], 0)

    def test_no_entries_returns_success(self):
        tmp = tempfile.mkdtemp()
        try:
            # 空日志目录
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE',
                           os.path.join(tmp, 'cache.json')):
                    result = prewarm_cache(top_n=10)
            self.assertTrue(result['success'])
            self.assertEqual(result['total_queries_seen'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prewarm_writes_to_cache(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=10)
            self.assertTrue(result['success'])
            self.assertGreater(result['prewarmed_count'], 0)
            # 缓存文件应存在
            self.assertTrue(os.path.exists(cache_file))
            cache = _load_cache()
            with patch('prewarm.CACHE_FILE', cache_file):
                cache = _load_cache()
            self.assertGreater(len(cache), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dry_run_does_not_write_cache(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=10, dry_run=True)
            self.assertTrue(result['success'])
            self.assertTrue(result['dry_run'])
            # 缓存文件不应存在（dry_run 不写入）
            self.assertFalse(os.path.exists(cache_file))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_already_cached_skipped(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            # 预先在缓存中放入该查询
            key = _cache_key(SAMPLE_ENTRY_FULL['query'],
                             SAMPLE_ENTRY_FULL['location'],
                             SAMPLE_ENTRY_FULL.get('layer_hint', ''))
            existing_cache = {key: {'query': SAMPLE_ENTRY_FULL['query'],
                                     'cached_at': time.time()}}
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(existing_cache, f)

            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=10)
            self.assertTrue(result['success'])
            self.assertEqual(result['prewarmed_count'], 0)
            self.assertGreater(result['skipped_already_cached'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_results_skipped(self):
        """历史条目无 top_results 时跳过（但仍计入频率）。"""
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_NO_RESULTS], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=10)
            self.assertTrue(result['success'])
            self.assertEqual(result['prewarmed_count'], 0)
            # 该查询被统计但 latest_entry 为 None
            self.assertGreater(result['total_queries_seen'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_top_n_limit(self):
        """只预热 Top N 查询。"""
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            # 创建 5 个不同查询，每个 1-5 次出现
            entries = []
            for i in range(1, 6):
                for _ in range(i):
                    entries.append({
                        'timestamp': '2026-07-20T10:00:00',
                        'query': f'q{i}',
                        'location': 'zhuhai',
                        'layer_hint': '',
                        'top_results': [{'title': f'r{i}'}],
                        'results_count': 1,
                        'layers_used': ['duckduckgo'],
                    })
            _make_log_dir_with_entries(entries, tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=2)  # 只取前 2
            self.assertTrue(result['success'])
            # 只预热前 2（频率最高的 q5 和 q4）
            self.assertEqual(result['prewarmed_count'], 2)
            self.assertEqual(len(result['top_queries']), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_days_window_filter(self):
        """时间窗口过滤排除旧条目。"""
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_OLD], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    # 30 天窗口：SAMPLE_ENTRY_OLD (6月1日) 被排除
                    result = prewarm_cache(top_n=10, days_window=30)
            self.assertTrue(result['success'])
            # 只统计到 SAMPLE_ENTRY_FULL
            self.assertEqual(result['total_queries_seen'], 1)

            # 全部时间窗口：两条都统计
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=10, days_window=0)
            self.assertEqual(result['total_queries_seen'], 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_top_queries_detail_structure(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    result = prewarm_cache(top_n=10)
            self.assertGreater(len(result['top_queries']), 0)
            top_q = result['top_queries'][0]
            self.assertIn('query', top_q)
            self.assertIn('count', top_q)
            self.assertIn('status', top_q)
            self.assertIn('location', top_q)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_result_structure_complete(self):
        """验证返回 dict 包含所有必需字段。"""
        with patch('prewarm._LOG_DIR', '/nonexistent/xyz'):
            with patch('prewarm.CACHE_FILE',
                       '/nonexistent/xyz/cache.json'):
                result = prewarm_cache(top_n=5)
        required_fields = [
            'success', 'top_n', 'days_window',
            'total_queries_seen', 'prewarm_candidates',
            'prewarmed_count', 'skipped_already_cached',
            'skipped_no_results', 'cache_total_after',
            'top_queries',
        ]
        for field in required_fields:
            self.assertIn(field, result, f'Missing field: {field}')


class TestShowStats(unittest.TestCase):
    """show_stats 测试。"""

    def test_no_log_dir(self):
        with patch('prewarm._LOG_DIR', '/nonexistent/xyz'):
            with patch('prewarm.CACHE_FILE',
                       '/nonexistent/xyz/cache.json'):
                with patch('sys.stdout', new=StringIO()) as fake_out:
                    code = show_stats(days_window=30)
        self.assertEqual(code, 0)
        output = fake_out.getvalue()
        result = json.loads(output)
        self.assertTrue(result['success'])
        self.assertEqual(result['total_files'], 0)

    def test_with_entries(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    with patch('sys.stdout', new=StringIO()) as fake_out:
                        code = show_stats(days_window=30)
            self.assertEqual(code, 0)
            result = json.loads(fake_out.getvalue())
            self.assertTrue(result['success'])
            self.assertGreater(result['total_entries'], 0)
            self.assertGreater(result['unique_queries'], 0)
            self.assertGreater(len(result['top_queries']), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def _run_cli(self, argv):
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ['prewarm.py'] + argv
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        try:
            exit_code = _cli()
            stdout = sys.stdout.getvalue()
            stderr = sys.stderr.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_run_no_log_dir(self):
        with patch('prewarm._LOG_DIR', '/nonexistent/xyz'):
            with patch('prewarm.CACHE_FILE',
                       '/nonexistent/xyz/cache.json'):
                code, stdout, _ = self._run_cli(
                    ['run', '--top', '10', '--json'])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertTrue(result['success'])
        self.assertEqual(result['prewarmed_count'], 0)

    def test_run_with_entries(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    code, stdout, _ = self._run_cli(
                        ['run', '--top', '10', '--json'])
            self.assertEqual(code, 0)
            result = json.loads(stdout)
            self.assertTrue(result['success'])
            self.assertGreater(result['prewarmed_count'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_dry_run(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    code, stdout, _ = self._run_cli(
                        ['run', '--top', '10', '--dry-run', '--json'])
            self.assertEqual(code, 0)
            result = json.loads(stdout)
            self.assertTrue(result['dry_run'])
            # 缓存文件不应存在
            self.assertFalse(os.path.exists(cache_file))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_run_human_readable(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    code, stdout, _ = self._run_cli(
                        ['run', '--top', '10'])
            self.assertEqual(code, 0)
            self.assertIn('Prewarm', stdout)
            self.assertIn('Total queries seen', stdout)
            self.assertIn('Top queries', stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stats_command(self):
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    code, stdout, _ = self._run_cli(
                        ['stats', '--days', '30'])
            self.assertEqual(code, 0)
            result = json.loads(stdout)
            self.assertTrue(result['success'])
            self.assertGreater(result['total_entries'], 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stats_no_log_dir(self):
        with patch('prewarm._LOG_DIR', '/nonexistent/xyz'):
            with patch('prewarm.CACHE_FILE',
                       '/nonexistent/xyz/cache.json'):
                code, stdout, _ = self._run_cli(['stats'])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertTrue(result['success'])
        self.assertEqual(result['total_files'], 0)

    def test_run_with_days_zero(self):
        """--days 0 表示全部时间窗口。"""
        tmp = tempfile.mkdtemp()
        cache_file = os.path.join(tmp, 'cache.json')
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_OLD], tmp)
            with patch('prewarm._LOG_DIR', tmp):
                with patch('prewarm.CACHE_FILE', cache_file):
                    code, stdout, _ = self._run_cli(
                        ['run', '--days', '0', '--json'])
            self.assertEqual(code, 0)
            result = json.loads(stdout)
            # days_window=0 → 全部 → 2 个查询
            self.assertEqual(result['total_queries_seen'], 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestConstants(unittest.TestCase):
    """常量校验。"""

    def test_default_top_n_positive(self):
        self.assertGreater(DEFAULT_TOP_N, 0)

    def test_default_days_window_positive(self):
        self.assertGreater(DEFAULT_DAYS_WINDOW, 0)

    def test_cache_ttl_positive(self):
        self.assertGreater(CACHE_TTL, 0)

    def test_cache_ttl_at_least_one_day(self):
        self.assertGreaterEqual(CACHE_TTL, 24 * 3600)


if __name__ == '__main__':
    unittest.main(verbosity=2)
