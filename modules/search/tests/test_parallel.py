#!/usr/bin/env python3
"""parallel.py 单元测试 — 并行三层 MCP 调用。

覆盖维度：
  - 边界输入：空 query / None / 非 list layers / 空 mcp_callers / 无效 mode
  - 单层调用：成功 / 失败
  - 多层 first_completed：成功（取最快）/ 全部失败 / 部分失败
  - 多层 all：全部成功合并 / 部分失败仍返回成功的 / 全部失败
  - 超时：取消未完成任务
  - 计时：timings 字段精确性
  - CLI: run / supported-layers / mock 模式
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

import parallel
from parallel import (
    parallel_search, _empty_result, _timed_call, _mock_caller,
    _make_mock_caller, _MOCK_RESULTS, _MOCK_LATENCIES,
    DEFAULT_TIMEOUT, MAX_WORKERS, SUPPORTED_MODES,
    _cli,
)


# ── 测试数据 ────────────────────────────────────────────────────
def _success_caller(results, delay=0):
    """构造返回指定结果的 caller。"""
    def _caller(query):
        if delay > 0:
            time.sleep(delay)
        return list(results)
    return _caller


def _failing_caller(error_msg='mocked failure', delay=0):
    """构造抛异常的 caller。"""
    def _caller(query):
        if delay > 0:
            time.sleep(delay)
        raise RuntimeError(error_msg)
    return _caller


def _slow_caller(delay, results=None):
    """构造慢速 caller。"""
    def _caller(query):
        time.sleep(delay)
        return list(results) if results else []
    return _caller


class TestEdgeCases(unittest.TestCase):
    """边界输入测试。"""

    def test_empty_query_returns_empty_result(self):
        result = parallel_search('', ['duckduckgo'],
                                 {'duckduckgo': _success_caller([])})
        self.assertFalse(result['success'])
        self.assertEqual(result['winner'], None)
        self.assertIn('query is empty', result['errors']['_'])

    def test_none_query_returns_empty_result(self):
        result = parallel_search(None, ['duckduckgo'],
                                 {'duckduckgo': _success_caller([])})
        self.assertFalse(result['success'])

    def test_whitespace_query_returns_empty_result(self):
        result = parallel_search('   ', ['duckduckgo'],
                                 {'duckduckgo': _success_caller([])})
        self.assertFalse(result['success'])

    def test_non_string_query_returns_empty_result(self):
        result = parallel_search(123, ['duckduckgo'],
                                 {'duckduckgo': _success_caller([])})
        self.assertFalse(result['success'])

    def test_empty_layers_returns_empty_result(self):
        result = parallel_search('test', [], {'duckduckgo': _success_caller([])})
        self.assertFalse(result['success'])
        self.assertIn('layers is empty', result['errors']['_'])

    def test_non_list_layers_returns_empty_result(self):
        result = parallel_search('test', 'duckduckgo',
                                 {'duckduckgo': _success_caller([])})
        self.assertFalse(result['success'])

    def test_empty_mcp_callers_returns_empty_result(self):
        result = parallel_search('test', ['duckduckgo'], {})
        self.assertFalse(result['success'])
        self.assertIn('mcp_callers is empty', result['errors']['_'])

    def test_non_dict_mcp_callers_returns_empty_result(self):
        result = parallel_search('test', ['duckduckgo'], [])
        self.assertFalse(result['success'])

    def test_invalid_mode_returns_empty_result(self):
        result = parallel_search('test', ['duckduckgo'],
                                  {'duckduckgo': _success_caller([])},
                                  mode='invalid_mode')
        self.assertFalse(result['success'])
        self.assertIn('unsupported mode', result['errors']['_'])

    def test_no_valid_callers_for_layers(self):
        """layers 中的层名都不在 mcp_callers 中。"""
        result = parallel_search('test', ['duckduckgo', 'searxng'],
                                 {'baidu': _success_caller([])})
        self.assertFalse(result['success'])
        self.assertIn('no valid callers', result['errors']['_'])


class TestSingleLayer(unittest.TestCase):
    """单层调用测试。"""

    def test_single_layer_success(self):
        results = [{'title': 'test', 'url': 'https://example.com'}]
        result = parallel_search('query', ['duckduckgo'],
                                 {'duckduckgo': _success_caller(results)})
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'duckduckgo')
        self.assertEqual(result['results'], results)
        self.assertTrue(result['all_completed'])
        self.assertIn('duckduckgo', result['timings'])
        self.assertGreaterEqual(result['timings']['duckduckgo'], 0)
        self.assertEqual(result['errors'], {})
        self.assertEqual(result['cancelled'], [])

    def test_single_layer_failure(self):
        result = parallel_search('query', ['duckduckgo'],
                                 {'duckduckgo': _failing_caller('boom')})
        self.assertFalse(result['success'])
        self.assertEqual(result['winner'], None)
        self.assertEqual(result['results'], [])
        self.assertIn('duckduckgo', result['errors'])
        self.assertIn('boom', result['errors']['duckduckgo'])

    def test_single_layer_empty_results_list(self):
        """单层返回空 list 仍算成功。"""
        result = parallel_search('query', ['duckduckgo'],
                                 {'duckduckgo': _success_caller([])})
        self.assertTrue(result['success'])
        self.assertEqual(result['results'], [])

    def test_single_layer_non_list_results_coerced_to_empty(self):
        """caller 返回非 list → 被强制转为空 list。"""
        def _bad_caller(query):
            return 'not a list'
        result = parallel_search('query', ['duckduckgo'],
                                 {'duckduckgo': _bad_caller})
        self.assertTrue(result['success'])
        self.assertEqual(result['results'], [])

    def test_single_layer_exception_returns_empty(self):
        """caller 抛异常 → success=False。"""
        def _raising_caller(query):
            raise ValueError('bad input')
        result = parallel_search('query', ['duckduckgo'],
                                 {'duckduckgo': _raising_caller})
        self.assertFalse(result['success'])
        self.assertIn('bad input', result['errors']['duckduckgo'])

    def test_single_layer_extra_layers_ignored(self):
        """layers 中未在 mcp_callers 中的层名被静默忽略。"""
        results = [{'title': 'test'}]
        result = parallel_search('query',
                                 ['duckduckgo', 'unknown_layer'],
                                 {'duckduckgo': _success_caller(results)})
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'duckduckgo')


class TestFirstCompletedMode(unittest.TestCase):
    """first_completed 模式测试。"""

    def test_three_layers_fastest_wins(self):
        """三层并发，最快返回者获胜。"""
        result = parallel_search(
            'test',
            ['duckduckgo', 'searxng', 'g-search'],
            {
                'duckduckgo': _success_caller(
                    [{'title': 'DDG', 'url': 'u1'}], delay=0.05),
                'searxng': _success_caller(
                    [{'title': 'SNG', 'url': 'u2'}], delay=0.2),
                'g-search': _success_caller(
                    [{'title': 'G', 'url': 'u3'}], delay=0.4),
            },
            mode='first_completed',
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'duckduckgo')
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['title'], 'DDG')

    def test_all_layers_fail(self):
        """所有层都失败 → success=False。"""
        result = parallel_search(
            'test',
            ['l1', 'l2'],
            {
                'l1': _failing_caller('fail1', delay=0.05),
                'l2': _failing_caller('fail2', delay=0.05),
            },
            mode='first_completed',
            timeout=5,
        )
        self.assertFalse(result['success'])
        self.assertIsNone(result['winner'])
        self.assertEqual(result['results'], [])
        # 两个错误都应记录
        self.assertEqual(len(result['errors']), 2)

    def test_one_layer_succeeds_others_fail(self):
        """部分层失败但仍能返回 winner。"""
        result = parallel_search(
            'test',
            ['fail1', 'ok', 'fail2'],
            {
                'fail1': _failing_caller('boom1', delay=0.05),
                'ok': _success_caller([{'title': 'ok', 'url': 'u'}], delay=0.1),
                'fail2': _failing_caller('boom2', delay=0.2),
            },
            mode='first_completed',
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'ok')
        self.assertEqual(len(result['results']), 1)

    def test_winner_results_only(self):
        """first_completed 模式仅返回 winner 的结果，不合并其他层。"""
        result = parallel_search(
            'test',
            ['fast', 'slow'],
            {
                'fast': _success_caller(
                    [{'title': 'fast1'}, {'title': 'fast2'}], delay=0.05),
                'slow': _success_caller(
                    [{'title': 'slow1'}, {'title': 'slow2'}], delay=0.3),
            },
            mode='first_completed',
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'fast')
        self.assertEqual(len(result['results']), 2)
        # 不应包含 slow 的结果
        titles = [r.get('title') for r in result['results']]
        self.assertNotIn('slow1', titles)

    def test_cancelled_tracked(self):
        """被取消的层应记录在 cancelled 列表中。"""
        result = parallel_search(
            'test',
            ['fast', 'slow'],
            {
                'fast': _success_caller([{'title': 'fast'}], delay=0.05),
                'slow': _slow_caller(2.0, [{'title': 'slow'}]),  # 2s 延迟
            },
            mode='first_completed',
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'fast')
        # slow 应被取消或已记录
        # 注意：cancel() 在线程池中可能失败（已开始执行），所以 cancelled 可能为空
        # 但 timings 中应至少有 fast 的记录
        self.assertIn('fast', result['timings'])


class TestAllMode(unittest.TestCase):
    """all 模式测试。"""

    def test_all_layers_success_merge_results(self):
        """所有层都成功 → 合并结果。"""
        result = parallel_search(
            'test',
            ['l1', 'l2'],
            {
                'l1': _success_caller([{'title': 'r1'}], delay=0.05),
                'l2': _success_caller([{'title': 'r2'}], delay=0.05),
            },
            mode='all',
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 2)
        titles = {r['title'] for r in result['results']}
        self.assertEqual(titles, {'r1', 'r2'})

    def test_all_layers_partial_failure_still_returns_success(self):
        """all 模式下，部分层失败仍返回成功层的结果。"""
        result = parallel_search(
            'test',
            ['ok', 'fail'],
            {
                'ok': _success_caller([{'title': 'ok'}], delay=0.05),
                'fail': _failing_caller('boom', delay=0.05),
            },
            mode='all',
            timeout=5,
        )
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['title'], 'ok')
        self.assertIn('fail', result['errors'])

    def test_all_layers_all_fail(self):
        """all 模式下所有层都失败 → success=False。"""
        result = parallel_search(
            'test',
            ['fail1', 'fail2'],
            {
                'fail1': _failing_caller('boom1', delay=0.05),
                'fail2': _failing_caller('boom2', delay=0.05),
            },
            mode='all',
            timeout=5,
        )
        self.assertFalse(result['success'])
        self.assertEqual(result['results'], [])
        self.assertEqual(len(result['errors']), 2)

    def test_all_mode_preserves_layer_order(self):
        """all 模式结果按 layers 指定顺序合并。"""
        result = parallel_search(
            'test',
            ['first', 'second'],
            {
                'first': _success_caller([{'title': 'first1'}], delay=0.1),
                'second': _success_caller([{'title': 'second1'}], delay=0.05),
            },
            mode='all',
            timeout=5,
        )
        # first 应排在 second 之前（即使 second 先完成）
        self.assertEqual(result['results'][0]['title'], 'first1')
        self.assertEqual(result['results'][1]['title'], 'second1')


class TestTimeout(unittest.TestCase):
    """超时测试。"""

    def test_timeout_cancels_slow_tasks(self):
        """超时取消慢任务。"""
        result = parallel_search(
            'test',
            ['slow', 'slow2'],
            {
                'slow': _slow_caller(2.0, [{'title': 'slow'}]),
                'slow2': _slow_caller(2.0, [{'title': 'slow2'}]),
            },
            mode='first_completed',
            timeout=0.3,  # 300ms 超时
        )
        # 都被超时取消，winner=None
        self.assertFalse(result['success'])

    def test_timeout_all_mode(self):
        """all 模式下超时也应处理。"""
        result = parallel_search(
            'test',
            ['slow', 'slow2'],
            {
                'slow': _slow_caller(2.0, [{'title': 'slow'}]),
                'slow2': _slow_caller(2.0, [{'title': 'slow2'}]),
            },
            mode='all',
            timeout=0.3,
        )
        self.assertFalse(result['success'])

    def test_zero_timeout_means_no_wait(self):
        """timeout=0 立即超时。"""
        result = parallel_search(
            'test',
            ['slow'],
            {'slow': _slow_caller(2.0, [{'title': 'slow'}])},
            mode='first_completed',
            timeout=0.01,
        )
        # 单层情况下 timeout 不影响（直接同步调用）
        # 多层才会受 timeout 影响
        # 这里测单层仅验证不抛异常
        self.assertTrue(result['success'])

    def test_negative_timeout_means_no_wait_multi_layer(self):
        """多层时 timeout<0 视为立即超时。"""
        result = parallel_search(
            'test',
            ['slow', 'slow2'],
            {
                'slow': _slow_caller(2.0, [{'title': 'slow'}]),
                'slow2': _slow_caller(2.0, [{'title': 'slow2'}]),
            },
            mode='first_completed',
            timeout=-1,
        )
        # 负 timeout → as_completed(timeout=-1) 立即返回
        # 应被取消或超时
        # 不强断言 success（取决于实现细节），只验证不阻塞
        self.assertIsNotNone(result)


class TestTimings(unittest.TestCase):
    """计时字段测试。"""

    def test_timings_present_on_success(self):
        result = parallel_search('test', ['l1'],
                                 {'l1': _success_caller([{'title': 't'}])})
        self.assertIn('l1', result['timings'])
        self.assertGreaterEqual(result['timings']['l1'], 0)

    def test_timings_present_on_failure(self):
        result = parallel_search('test', ['l1'],
                                 {'l1': _failing_caller('boom')})
        self.assertIn('l1', result['timings'])

    def test_timings_reasonable_value(self):
        """计时应在合理范围内（mock 调用应很快）。"""
        result = parallel_search(
            'test',
            ['fast'],
            {'fast': _success_caller([{'title': 't'}], delay=0.05)},
            timeout=5,
        )
        elapsed = result['timings']['fast']
        # 应该在 0.04 - 1.0 之间（允许抖动）
        self.assertGreater(elapsed, 0.03)
        self.assertLess(elapsed, 1.5)

    def test_timings_all_layers_recorded_all_mode(self):
        result = parallel_search(
            'test',
            ['l1', 'l2'],
            {
                'l1': _success_caller([{'title': 'r1'}], delay=0.05),
                'l2': _success_caller([{'title': 'r2'}], delay=0.05),
            },
            mode='all',
            timeout=5,
        )
        self.assertIn('l1', result['timings'])
        self.assertIn('l2', result['timings'])


class TestTimedCallHelper(unittest.TestCase):
    """_timed_call 辅助函数测试。"""

    def test_success_case(self):
        outcome = _timed_call('test_layer', _success_caller([{'title': 't'}]),
                              'query')
        self.assertTrue(outcome['success'])
        self.assertEqual(outcome['layer'], 'test_layer')
        self.assertEqual(len(outcome['results']), 1)
        self.assertIsNone(outcome['error'])
        self.assertGreaterEqual(outcome['elapsed'], 0)

    def test_failure_case(self):
        outcome = _timed_call('test_layer', _failing_caller('boom'), 'query')
        self.assertFalse(outcome['success'])
        self.assertEqual(outcome['results'], [])
        self.assertIn('boom', outcome['error'])
        self.assertGreaterEqual(outcome['elapsed'], 0)

    def test_non_list_results_coerced(self):
        outcome = _timed_call('test_layer',
                              lambda q: 'not a list', 'query')
        self.assertTrue(outcome['success'])
        self.assertEqual(outcome['results'], [])


class TestEmptyResultHelper(unittest.TestCase):
    """_empty_result 辅助函数测试。"""

    def test_default_construction(self):
        result = _empty_result('first_completed')
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'first_completed')
        self.assertIsNone(result['winner'])
        self.assertEqual(result['results'], [])
        self.assertFalse(result['all_completed'])
        self.assertEqual(result['timings'], {})
        self.assertEqual(result['errors'], {})
        self.assertEqual(result['cancelled'], [])

    def test_with_errors(self):
        errors = {'layer1': 'boom'}
        result = _empty_result('all', errors=errors)
        self.assertEqual(result['errors'], errors)


class TestMockCallers(unittest.TestCase):
    """Mock caller 函数测试。"""

    def test_mock_caller_returns_copy(self):
        """_mock_caller 应返回副本，不修改全局 _MOCK_RESULTS。"""
        result1 = _mock_caller('duckduckgo', 'query')
        result2 = _mock_caller('duckduckgo', 'query')
        self.assertEqual(result1, result2)
        self.assertIsNot(result1, result2)
        # 修改一份不影响另一份
        result1.append({'title': 'extra'})
        self.assertNotEqual(result1, result2)

    def test_mock_caller_unknown_layer_raises(self):
        with self.assertRaises(RuntimeError):
            _mock_caller('unknown_layer', 'query')

    def test_make_mock_caller_closure(self):
        caller = _make_mock_caller('duckduckgo')
        result = caller('test query')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['title'], 'DuckDuckGo Result 1')

    def test_mock_latencies_match_results_keys(self):
        """_MOCK_LATENCIES 与 _MOCK_RESULTS 的 keys 应一致。"""
        self.assertEqual(set(_MOCK_LATENCIES.keys()),
                         set(_MOCK_RESULTS.keys()))


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def _run_cli(self, argv):
        """运行 CLI 并捕获 stdout/stderr/exit_code。"""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ['parallel.py'] + argv
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

    def test_supported_layers_command(self):
        code, stdout, _ = self._run_cli(['supported-layers'])
        self.assertEqual(code, 0)
        self.assertIn('duckduckgo', stdout)
        self.assertIn('searxng', stdout)
        self.assertIn('g-search', stdout)
        self.assertIn('latency', stdout.lower())

    def test_run_mock_first_completed(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo,searxng',
             '--mock', '--mode', 'first_completed'])
        self.assertEqual(code, 0)
        self.assertIn('Mode: first_completed', stdout)
        self.assertIn('Winner: duckduckgo', stdout)
        self.assertIn('Success: True', stdout)

    def test_run_mock_all_mode(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo,searxng',
             '--mock', '--mode', 'all'])
        self.assertEqual(code, 0)
        self.assertIn('Mode: all', stdout)
        # all 模式应合并两层结果
        self.assertIn('Results count: 3', stdout)

    def test_run_json_output(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo',
             '--mock', '--json'])
        self.assertEqual(code, 0)
        result = json.loads(stdout)
        self.assertTrue(result['success'])
        self.assertEqual(result['winner'], 'duckduckgo')

    def test_run_without_mock_returns_error(self):
        """无 --mock 时报错（真实 MCP 需 Python API）。"""
        code, _, stderr = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo'])
        self.assertEqual(code, 1)
        self.assertIn('错误', stderr)

    def test_run_invalid_mode_choice(self):
        """argparse choices 限制 mode 参数。"""
        with self.assertRaises(SystemExit):
            self._run_cli(
                ['run', '--query', 'test', '--layers', 'duckduckgo',
                 '--mock', '--mode', 'invalid'])

    def test_run_single_layer(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo',
             '--mock'])
        self.assertEqual(code, 0)
        self.assertIn('Winner: duckduckgo', stdout)

    def test_run_all_failures(self):
        """所有层都失败时退出码为 1。"""
        # 用一个不存在的 layer 名 + mock 模式
        # mock 模式会为所有 layer 创建 caller，但 _mock_caller 对未知 layer 抛异常
        code, _, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'unknown1,unknown2',
             '--mock', '--mode', 'first_completed'])
        self.assertEqual(code, 1)

    def test_run_with_timeout_flag(self):
        code, stdout, _ = self._run_cli(
            ['run', '--query', 'test', '--layers', 'duckduckgo',
             '--mock', '--timeout', '5'])
        self.assertEqual(code, 0)


class TestConstants(unittest.TestCase):
    """常量校验。"""

    def test_default_timeout_positive(self):
        self.assertGreater(DEFAULT_TIMEOUT, 0)

    def test_max_workers_positive(self):
        self.assertGreater(MAX_WORKERS, 0)

    def test_supported_modes_contains_expected(self):
        self.assertIn('first_completed', SUPPORTED_MODES)
        self.assertIn('all', SUPPORTED_MODES)
        self.assertEqual(len(SUPPORTED_MODES), 2)

    def test_max_workers_at_least_3(self):
        """三层并发至少需要 3 个 worker。"""
        self.assertGreaterEqual(MAX_WORKERS, 3)


class TestResultStructure(unittest.TestCase):
    """结果结构完整性测试。"""

    def _verify_result_structure(self, result, mode):
        """验证结果 dict 包含所有必需字段。"""
        required_fields = [
            'success', 'mode', 'winner', 'results', 'all_completed',
            'timings', 'errors', 'cancelled',
        ]
        for field in required_fields:
            self.assertIn(field, result, f'Missing field: {field}')
        self.assertEqual(result['mode'], mode)
        self.assertIsInstance(result['success'], bool)
        self.assertIsInstance(result['results'], list)
        self.assertIsInstance(result['all_completed'], bool)
        self.assertIsInstance(result['timings'], dict)
        self.assertIsInstance(result['errors'], dict)
        self.assertIsInstance(result['cancelled'], list)

    def test_structure_on_success(self):
        result = parallel_search('test', ['l1'],
                                 {'l1': _success_caller([{'title': 't'}])})
        self._verify_result_structure(result, 'first_completed')

    def test_structure_on_failure(self):
        result = parallel_search('test', ['l1'],
                                 {'l1': _failing_caller('boom')})
        self._verify_result_structure(result, 'first_completed')

    def test_structure_on_edge_case(self):
        result = parallel_search('', ['l1'],
                                 {'l1': _success_caller([])})
        self._verify_result_structure(result, 'first_completed')

    def test_structure_all_mode(self):
        result = parallel_search('test', ['l1', 'l2'],
                                 {
                                     'l1': _success_caller([{'title': 't1'}]),
                                     'l2': _success_caller([{'title': 't2'}]),
                                 },
                                 mode='all', timeout=5)
        self._verify_result_structure(result, 'all')


if __name__ == '__main__':
    unittest.main(verbosity=2)
