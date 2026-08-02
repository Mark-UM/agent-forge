#!/usr/bin/env python3
"""stream.py 单元测试 — 流式结果输出。

覆盖维度：
  - 边界输入：空 query / None / 非 list layers / 空 mcp_callers
  - 单层流：start → result → done / start → error → done
  - 多层流：每个 start 事件 + 完成事件（result/error）+ done 事件
  - 超时：timeout 事件 + 取消未完成任务
  - collect_stream：聚合为单一 dict
  - 事件结构：type/timestamp/layer 等字段完整性
  - CLI: run / mock 模式 / json 输出
"""
import sys
import os
import json
import time
import unittest
from unittest.mock import patch, MagicMock
from io import StringIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import stream
from stream import (
    stream_search, collect_stream, _timed_call, _now_iso,
    DEFAULT_TIMEOUT, MAX_WORKERS,
    _cli,
)


# ── 测试数据 ────────────────────────────────────────────────────
def _success_caller(results, delay=0):
    def _caller(query):
        if delay > 0:
            time.sleep(delay)
        return list(results)
    return _caller


def _failing_caller(error_msg='mocked failure', delay=0):
    def _caller(query):
        if delay > 0:
            time.sleep(delay)
        raise RuntimeError(error_msg)
    return _caller


def _slow_caller(delay, results=None):
    def _caller(query):
        time.sleep(delay)
        return list(results) if results else []
    return _caller


def _collect_events(gen):
    """收集 generator 的所有事件。"""
    return list(gen)


class TestEdgeCases(unittest.TestCase):
    """边界输入测试。"""

    def test_empty_query_emits_error_and_done(self):
        events = _collect_events(stream_search(
            '', ['duckduckgo'], {'duckduckgo': _success_caller([])}))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['type'], 'error')
        self.assertIn('query is empty', events[0]['error'])
        self.assertEqual(events[1]['type'], 'done')
        self.assertEqual(events[1]['successful'], 0)
        self.assertEqual(events[1]['failed'], 1)

    def test_none_query_emits_error_and_done(self):
        events = _collect_events(stream_search(
            None, ['duckduckgo'], {'duckduckgo': _success_caller([])}))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['type'], 'error')

    def test_whitespace_query_emits_error(self):
        events = _collect_events(stream_search(
            '   ', ['duckduckgo'], {'duckduckgo': _success_caller([])}))
        self.assertEqual(events[0]['type'], 'error')
        self.assertIn('query is empty', events[0]['error'])

    def test_non_string_query_emits_error(self):
        events = _collect_events(stream_search(
            123, ['duckduckgo'], {'duckduckgo': _success_caller([])}))
        self.assertEqual(events[0]['type'], 'error')

    def test_empty_layers_emits_error(self):
        events = _collect_events(stream_search(
            'test', [], {'duckduckgo': _success_caller([])}))
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['type'], 'error')
        self.assertIn('layers is empty', events[0]['error'])

    def test_non_list_layers_emits_error(self):
        events = _collect_events(stream_search(
            'test', 'duckduckgo', {'duckduckgo': _success_caller([])}))
        self.assertEqual(events[0]['type'], 'error')

    def test_empty_mcp_callers_emits_error(self):
        events = _collect_events(stream_search(
            'test', ['duckduckgo'], {}))
        self.assertEqual(events[0]['type'], 'error')
        self.assertIn('mcp_callers is empty', events[0]['error'])

    def test_non_dict_mcp_callers_emits_error(self):
        events = _collect_events(stream_search(
            'test', ['duckduckgo'], []))
        self.assertEqual(events[0]['type'], 'error')

    def test_no_valid_callers_emits_error(self):
        events = _collect_events(stream_search(
            'test', ['duckduckgo'], {'baidu': _success_caller([])}))
        self.assertEqual(events[0]['type'], 'error')
        self.assertIn('no valid callers', events[0]['error'])


class TestSingleLayerStream(unittest.TestCase):
    """单层流测试。"""

    def test_single_layer_success_events(self):
        results = [{'title': 'r1'}]
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _success_caller(results)}))
        # 应有 start + result + done 三个事件
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]['type'], 'start')
        self.assertEqual(events[0]['layer'], 'l1')
        self.assertEqual(events[1]['type'], 'result')
        self.assertEqual(events[1]['layer'], 'l1')
        self.assertEqual(events[1]['results'], results)
        self.assertIn('elapsed', events[1])
        self.assertEqual(events[2]['type'], 'done')
        self.assertEqual(events[2]['successful'], 1)
        self.assertEqual(events[2]['failed'], 0)
        self.assertEqual(events[2]['total_layers'], 1)

    def test_single_layer_failure_events(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _failing_caller('boom')}))
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]['type'], 'start')
        self.assertEqual(events[1]['type'], 'error')
        self.assertEqual(events[1]['layer'], 'l1')
        self.assertIn('boom', events[1]['error'])
        self.assertEqual(events[2]['type'], 'done')
        self.assertEqual(events[2]['successful'], 0)
        self.assertEqual(events[2]['failed'], 1)

    def test_single_layer_empty_results_list(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _success_caller([])}))
        # 空列表仍算成功
        self.assertEqual(events[1]['type'], 'result')
        self.assertEqual(events[1]['results'], [])
        self.assertEqual(events[2]['successful'], 1)


class TestMultiLayerStream(unittest.TestCase):
    """多层流测试。"""

    def test_multi_layer_all_success(self):
        events = _collect_events(stream_search(
            'test',
            ['l1', 'l2'],
            {
                'l1': _success_caller([{'title': 'r1'}], delay=0.05),
                'l2': _success_caller([{'title': 'r2'}], delay=0.05),
            },
            timeout=5,
        ))
        # 应有 2 start + 2 result/error + 1 done = 5 events
        self.assertEqual(len(events), 5)

        # 前两个是 start 事件（按 layers 顺序）
        self.assertEqual(events[0]['type'], 'start')
        self.assertEqual(events[1]['type'], 'start')

        # 中间两个是 result 或 error
        result_events = [e for e in events if e['type'] == 'result']
        self.assertEqual(len(result_events), 2)

        # 最后是 done
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['successful'], 2)
        self.assertEqual(events[-1]['failed'], 0)

    def test_multi_layer_partial_failure(self):
        events = _collect_events(stream_search(
            'test',
            ['ok', 'fail'],
            {
                'ok': _success_caller([{'title': 'r'}], delay=0.05),
                'fail': _failing_caller('boom', delay=0.05),
            },
            timeout=5,
        ))
        result_events = [e for e in events if e['type'] == 'result']
        error_events = [e for e in events if e['type'] == 'error']
        self.assertEqual(len(result_events), 1)
        self.assertEqual(len(error_events), 1)
        self.assertEqual(events[-1]['successful'], 1)
        self.assertEqual(events[-1]['failed'], 1)

    def test_multi_layer_all_fail(self):
        events = _collect_events(stream_search(
            'test',
            ['l1', 'l2'],
            {
                'l1': _failing_caller('boom1', delay=0.05),
                'l2': _failing_caller('boom2', delay=0.05),
            },
            timeout=5,
        ))
        result_events = [e for e in events if e['type'] == 'result']
        error_events = [e for e in events if e['type'] == 'error']
        self.assertEqual(len(result_events), 0)
        self.assertEqual(len(error_events), 2)
        self.assertEqual(events[-1]['successful'], 0)
        self.assertEqual(events[-1]['failed'], 2)

    def test_start_events_emitted_before_results(self):
        """所有 start 事件应在 result/error 之前发出。"""
        events = _collect_events(stream_search(
            'test',
            ['l1', 'l2', 'l3'],
            {
                'l1': _success_caller([{'title': 'r1'}], delay=0.05),
                'l2': _success_caller([{'title': 'r2'}], delay=0.05),
                'l3': _success_caller([{'title': 'r3'}], delay=0.05),
            },
            timeout=5,
        ))
        # 找到第一个非 start 事件的索引
        first_non_start = next(
            (i for i, e in enumerate(events) if e['type'] != 'start'),
            len(events))
        # 所有 start 事件应在前 3 个位置
        start_events = [e for e in events if e['type'] == 'start']
        self.assertEqual(len(start_events), 3)
        # 验证 start 事件确实在前面
        self.assertLessEqual(first_non_start, 3)

    def test_done_event_total_elapsed(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _success_caller([{'title': 'r'}])}))
        done = events[-1]
        self.assertIn('total_elapsed', done)
        self.assertGreaterEqual(done['total_elapsed'], 0)


class TestTimeout(unittest.TestCase):
    """超时测试。"""

    def test_timeout_emits_timeout_event(self):
        events = _collect_events(stream_search(
            'test',
            ['slow', 'slow2'],
            {
                'slow': _slow_caller(2.0, [{'title': 'slow'}]),
                'slow2': _slow_caller(2.0, [{'title': 'slow2'}]),
            },
            timeout=0.3,
        ))
        # 应有 timeout 事件
        timeout_events = [e for e in events if e['type'] == 'timeout']
        self.assertGreater(len(timeout_events), 0)

    def test_timeout_done_event_includes_cancelled(self):
        events = _collect_events(stream_search(
            'test',
            ['slow', 'slow2'],
            {
                'slow': _slow_caller(2.0),
                'slow2': _slow_caller(2.0),
            },
            timeout=0.3,
        ))
        done = events[-1]
        self.assertEqual(done['type'], 'done')
        # cancelled 字段应有内容（除非已开始执行）
        self.assertIn('cancelled', done)


class TestEventStructure(unittest.TestCase):
    """事件结构完整性测试。"""

    def test_start_event_fields(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _success_caller([{'title': 'r'}])}))
        start_event = events[0]
        self.assertEqual(start_event['type'], 'start')
        self.assertEqual(start_event['layer'], 'l1')
        self.assertIn('timestamp', start_event)

    def test_result_event_fields(self):
        events = _collect_events(stream_search(
            'test', ['l1'],
            {'l1': _success_caller([{'title': 'r'}])}))
        result_event = next(e for e in events if e['type'] == 'result')
        self.assertEqual(result_event['layer'], 'l1')
        self.assertIn('results', result_event)
        self.assertIn('elapsed', result_event)
        self.assertIn('timestamp', result_event)

    def test_error_event_fields(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _failing_caller('boom')}))
        error_event = next(e for e in events if e['type'] == 'error')
        self.assertEqual(error_event['layer'], 'l1')
        self.assertIn('error', error_event)
        self.assertIn('elapsed', error_event)
        self.assertIn('timestamp', error_event)

    def test_done_event_fields(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _success_caller([{'title': 'r'}])}))
        done_event = events[-1]
        self.assertEqual(done_event['type'], 'done')
        self.assertIn('total_layers', done_event)
        self.assertIn('successful', done_event)
        self.assertIn('failed', done_event)
        self.assertIn('total_elapsed', done_event)
        self.assertIn('timestamp', done_event)

    def test_all_events_have_timestamp(self):
        events = _collect_events(stream_search(
            'test', ['l1', 'l2'],
            {
                'l1': _success_caller([{'title': 'r1'}], delay=0.05),
                'l2': _success_caller([{'title': 'r2'}], delay=0.05),
            },
            timeout=5,
        ))
        for event in events:
            self.assertIn('timestamp', event,
                          f"Event type={event['type']} missing timestamp")

    def test_timestamp_is_iso_format(self):
        events = _collect_events(stream_search(
            'test', ['l1'], {'l1': _success_caller([])}))
        ts = events[0]['timestamp']
        # ISO 8601 格式应可被 datetime.fromisoformat 解析
        from datetime import datetime
        datetime.fromisoformat(ts)


class TestCollectStream(unittest.TestCase):
    """collect_stream 聚合函数测试。"""

    def test_collect_returns_dict_structure(self):
        result = collect_stream(
            'test', ['l1'],
            {'l1': _success_caller([{'title': 'r'}])})
        self.assertIn('success', result)
        self.assertIn('events', result)
        self.assertIn('results_by_layer', result)
        self.assertIn('errors_by_layer', result)
        self.assertIn('cancelled', result)
        self.assertIn('total_elapsed', result)

    def test_collect_success_case(self):
        result = collect_stream(
            'test',
            ['l1', 'l2'],
            {
                'l1': _success_caller([{'title': 'r1'}], delay=0.05),
                'l2': _success_caller([{'title': 'r2'}], delay=0.05),
            },
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results_by_layer']), 2)
        self.assertIn('l1', result['results_by_layer'])
        self.assertIn('l2', result['results_by_layer'])
        self.assertEqual(len(result['errors_by_layer']), 0)
        self.assertEqual(len(result['cancelled']), 0)

    def test_collect_partial_failure(self):
        result = collect_stream(
            'test',
            ['ok', 'fail'],
            {
                'ok': _success_caller([{'title': 'r'}], delay=0.05),
                'fail': _failing_caller('boom', delay=0.05),
            },
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertIn('ok', result['results_by_layer'])
        self.assertIn('fail', result['errors_by_layer'])
        self.assertIn('boom', result['errors_by_layer']['fail'])

    def test_collect_all_fail(self):
        result = collect_stream(
            'test',
            ['fail1', 'fail2'],
            {
                'fail1': _failing_caller('boom1', delay=0.05),
                'fail2': _failing_caller('boom2', delay=0.05),
            },
            timeout=5,
        )
        self.assertFalse(result['success'])
        self.assertEqual(len(result['errors_by_layer']), 2)

    def test_collect_edge_case_empty_query(self):
        result = collect_stream(
            '', ['l1'], {'l1': _success_caller([])})
        self.assertFalse(result['success'])
        # 边界错误事件应被收集
        self.assertGreater(len(result['events']), 0)
        # 边界错误（layer='_'）必须记录在 errors_by_layer 中
        self.assertIn('_', result['errors_by_layer'])

    def test_collect_edge_case_empty_layers_records_error(self):
        """空 layers 列表应记录边界错误到 errors_by_layer。"""
        result = collect_stream('test', [], {})
        self.assertFalse(result['success'])
        self.assertIn('_', result['errors_by_layer'])

    def test_collect_edge_case_empty_callers_records_error(self):
        """空 mcp_callers 应记录边界错误到 errors_by_layer。"""
        result = collect_stream('test', ['l1'], {})
        self.assertFalse(result['success'])
        self.assertIn('_', result['errors_by_layer'])

    def test_collect_total_elapsed_positive(self):
        result = collect_stream(
            'test', ['l1'],
            {'l1': _success_caller([{'title': 'r'}], delay=0.05)},
            timeout=5,
        )
        self.assertGreater(result['total_elapsed'], 0.04)


class TestNowIso(unittest.TestCase):
    """_now_iso 辅助函数测试。"""

    def test_returns_iso_format(self):
        ts = _now_iso()
        from datetime import datetime
        # 应可被解析
        datetime.fromisoformat(ts)

    def test_returns_string(self):
        self.assertIsInstance(_now_iso(), str)

    def test_unique_per_call(self):
        ts1 = _now_iso()
        time.sleep(0.01)
        ts2 = _now_iso()
        # 至少在 timespec='seconds' 下，连续两次可能相同
        # 所以只验证都能被解析
        from datetime import datetime
        datetime.fromisoformat(ts1)
        datetime.fromisoformat(ts2)


class TestTimedCallHelper(unittest.TestCase):
    """_timed_call 测试。"""

    def test_success_case(self):
        outcome = _timed_call('test_layer',
                              _success_caller([{'title': 't'}]), 'query')
        self.assertTrue(outcome['success'])
        self.assertEqual(outcome['layer'], 'test_layer')
        self.assertEqual(len(outcome['results']), 1)
        self.assertIsNone(outcome['error'])
        self.assertGreaterEqual(outcome['elapsed'], 0)

    def test_failure_case(self):
        outcome = _timed_call('test_layer', _failing_caller('boom'), 'query')
        self.assertFalse(outcome['success'])
        self.assertIn('boom', outcome['error'])

    def test_non_list_results_coerced(self):
        outcome = _timed_call('test_layer',
                              lambda q: 'not a list', 'query')
        self.assertTrue(outcome['success'])
        self.assertEqual(outcome['results'], [])


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def _run_cli(self, argv):
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ['stream.py'] + argv
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

    def test_run_mock_human_readable(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo',
             '--mock'])
        self.assertEqual(code, 0)
        # 应包含 start / result / done 关键字
        self.assertIn('started', stdout.lower())
        self.assertIn('returned', stdout.lower())
        self.assertIn('done', stdout.lower())

    def test_run_mock_json_lines(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo',
             '--mock', '--json'])
        self.assertEqual(code, 0)
        # 每行应是有效 JSON
        lines = [l for l in stdout.strip().split('\n') if l.strip()]
        self.assertGreater(len(lines), 0)
        for line in lines:
            event = json.loads(line)
            self.assertIn('type', event)
            self.assertIn('timestamp', event)

    def test_run_multi_layer_json(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo,searxng',
             '--mock', '--json'])
        self.assertEqual(code, 0)
        lines = [l for l in stdout.strip().split('\n') if l.strip()]
        events = [json.loads(l) for l in lines]
        # 应有 2 start + 2 result + 1 done = 5 事件
        start_count = sum(1 for e in events if e['type'] == 'start')
        result_count = sum(1 for e in events if e['type'] == 'result')
        done_count = sum(1 for e in events if e['type'] == 'done')
        self.assertEqual(start_count, 2)
        self.assertEqual(result_count, 2)
        self.assertEqual(done_count, 1)

    def test_run_without_mock_returns_error(self):
        code, _, stderr = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo'])
        self.assertEqual(code, 1)
        self.assertIn('错误', stderr)

    def test_run_with_timeout_flag(self):
        code, _, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo',
             '--mock', '--timeout', '5'])
        self.assertEqual(code, 0)


class TestConstants(unittest.TestCase):
    """常量校验。"""

    def test_default_timeout_positive(self):
        self.assertGreater(DEFAULT_TIMEOUT, 0)

    def test_max_workers_positive(self):
        self.assertGreater(MAX_WORKERS, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
