#!/usr/bin/env python3
"""search.py log_search() MindSearch 字段单元测试 — spec §8.3。

覆盖 v4.3 新增字段：
  - --deep-search + --planner-success + --degradation + --sub-queries-*
  - --aggregated
  - 默认不记录 MindSearch 字段（向后兼容）

验证：
  - 字段类型 / 默认值 / 边界值
  - JSON 序列化成功
  - 实际写入到日志文件
"""
import sys
import os
import json
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from argparse import Namespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import search


def _make_args(query='test', deep_search='false', aggregated='false',
               planner_success='', degradation='',
               sub_queries_planned=0, sub_queries_succeeded=0,
               sub_queries_failed=0, layers='duckduckgo',
               results_count=5, score=7.5, **kwargs):
    """构造模拟 argparse Namespace。"""
    return Namespace(
        query=query,
        layers=layers,
        results_count=results_count,
        score=score,
        satisfied='false',
        location='unknown',
        top_results='',
        saved='false',
        layer_hint='',
        deep_search=deep_search,
        aggregated=aggregated,
        planner_success=planner_success,
        degradation=degradation,
        sub_queries_planned=sub_queries_planned,
        sub_queries_succeeded=sub_queries_succeeded,
        sub_queries_failed=sub_queries_failed,
    )


class TestMindSearchLogFields(unittest.TestCase):
    """spec §8.3 字段验证。"""

    def setUp(self):
        """临时 _runtime/search 目录，避免污染真实日志。"""
        self._tmpdir = tempfile.mkdtemp(prefix='search_test_')
        self._orig_runtime = search._RUNTIME_DIR if hasattr(search, '_RUNTIME_DIR') else None
        # patch _ensure_log_dir + _today_log_file
        self._log_file = os.path.join(self._tmpdir, 'test_log.jsonl')
        self._ensure_patch = patch.object(search, '_ensure_log_dir')
        self._today_patch = patch.object(search, '_today_log_file',
                                         return_value=self._log_file)
        self._ensure_patch.start()
        self._today_patch.start()
        # cache_store 也 patch 掉，避免污染真实缓存
        self._cache_patch = patch.object(search, 'cache_store', return_value=True)
        self._cache_patch.start()

    def tearDown(self):
        self._ensure_patch.stop()
        self._today_patch.stop()
        self._cache_patch.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _read_last_entry(self):
        """读取最后一条日志记录。"""
        with open(self._log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        return json.loads(lines[-1].strip())

    def test_default_log_has_no_mindsearch_fields(self):
        """默认（非 --deep / 非 --aggregate）不应记录 MindSearch 字段。"""
        args = _make_args(deep_search='false', aggregated='false')
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertNotIn('deep_search', entry)
        self.assertNotIn('aggregated', entry)
        self.assertNotIn('planner_success', entry)
        self.assertNotIn('degradation', entry)
        self.assertNotIn('sub_queries_planned', entry)

    def test_deep_search_true_records_mindsearch_fields(self):
        """--deep-search true 应记录 deep_search + sub_queries_* 字段。"""
        args = _make_args(
            deep_search='true',
            planner_success='true',
            sub_queries_planned=5,
            sub_queries_succeeded=4,
            sub_queries_failed=1,
            degradation='',
        )
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertTrue(entry['deep_search'])
        self.assertTrue(entry['planner_success'])
        self.assertEqual(entry['sub_queries_planned'], 5)
        self.assertEqual(entry['sub_queries_succeeded'], 4)
        self.assertEqual(entry['sub_queries_failed'], 1)
        # degradation 空字符串 → 不记录
        self.assertNotIn('degradation', entry)

    def test_aggregated_true_records_aggregated_field(self):
        """--aggregated true 应记录 aggregated 字段。"""
        args = _make_args(deep_search='false', aggregated='true')
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertTrue(entry['aggregated'])

    def test_deep_search_with_aggregated(self):
        """--deep-search + --aggregated 组合应同时记录两者。"""
        args = _make_args(
            deep_search='true',
            aggregated='true',
            planner_success='false',
            degradation='aggregator_timeout',
            sub_queries_planned=4,
            sub_queries_succeeded=3,
            sub_queries_failed=1,
        )
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertTrue(entry['deep_search'])
        self.assertTrue(entry['aggregated'])
        self.assertFalse(entry['planner_success'])
        self.assertEqual(entry['degradation'], 'aggregator_timeout')
        self.assertEqual(entry['sub_queries_planned'], 4)
        self.assertEqual(entry['sub_queries_succeeded'], 3)
        self.assertEqual(entry['sub_queries_failed'], 1)

    def test_degradation_truncated_to_100_chars(self):
        """degradation 字段应截断到 100 字符防超长。"""
        long_deg = 'x' * 200
        args = _make_args(
            deep_search='true',
            degradation=long_deg,
        )
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertEqual(len(entry['degradation']), 100)
        self.assertEqual(entry['degradation'], 'x' * 100)

    def test_sub_queries_negative_clamped_to_zero(self):
        """sub_queries_* 负值应被钳到 0。"""
        args = _make_args(
            deep_search='true',
            sub_queries_planned=-5,
            sub_queries_succeeded=-1,
            sub_queries_failed=-10,
        )
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertEqual(entry['sub_queries_planned'], 0)
        self.assertEqual(entry['sub_queries_succeeded'], 0)
        self.assertEqual(entry['sub_queries_failed'], 0)

    def test_planner_success_empty_string_not_recorded(self):
        """planner_success 空字符串 → 不记录（保持日志简洁）。"""
        args = _make_args(
            deep_search='true',
            planner_success='',  # 空字符串
        )
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertTrue(entry['deep_search'])
        self.assertNotIn('planner_success', entry)

    def test_aggregated_only_without_deep_search(self):
        """--aggregated true 但未 --deep-search 也应记录 aggregated 字段。"""
        args = _make_args(deep_search='false', aggregated='true')
        result = search.log_search(args)
        self.assertEqual(result, 0)
        entry = self._read_last_entry()
        self.assertTrue(entry['aggregated'])
        # deep_search 不应被记录
        self.assertNotIn('deep_search', entry)

    def test_log_entry_is_valid_json(self):
        """日志条目应为有效 JSON（可被 json.loads 解析）。"""
        args = _make_args(
            deep_search='true',
            aggregated='true',
            planner_success='true',
            degradation='planner_timeout',
            sub_queries_planned=5,
            sub_queries_succeeded=0,
            sub_queries_failed=5,
        )
        result = search.log_search(args)
        self.assertEqual(result, 0)
        # 读取并解析
        with open(self._log_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    entry = json.loads(line)  # 不应抛异常
                    self.assertIsInstance(entry, dict)


if __name__ == '__main__':
    unittest.main(verbosity=2)
