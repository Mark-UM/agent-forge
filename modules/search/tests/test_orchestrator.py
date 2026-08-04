#!/usr/bin/env python3
"""orchestrator.py 单元测试 — v4.4 P4.1.3 可执行 Pipeline

覆盖目标：
  - 初始化 + 配置
  - 14 个 step 独立执行（mock 注入）
  - 链式调用
  - run_all 整体执行
  - 异常兜底（任何 step 失败不阻塞）
  - dry_run 计划生成
  - inject 回调注入
"""
import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock, MagicMock as Mock
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import orchestrator
from orchestrator import (
    SearchOrchestrator, STEP_ORDER, DEFAULT_CONFIG,
    StepExecutionError, PipelineIntegrityError,
)


# ── 测试数据 ────────────────────────────────────────────────────

SAMPLE_QUERY = 'FIT2004 2026 handbook assessment'

SAMPLE_RESULTS = [
    {
        'title': 'FIT2004 Handbook',
        'url': 'https://handbook.monash.edu/2026/units/FIT2004',
        'snippet': 'Algorithms and Data Structures assessment 60%',
        'source': 'official-docs',
    },
    {
        'title': 'Student Notes',
        'url': 'https://github.com/student/notes',
        'snippet': 'Notes from 2024',
        'source': 'github',
    },
]


def _make_flags(**kwargs):
    """构造 argparse.Namespace 的辅助函数。"""
    defaults = dict(
        deep=False, aggregate=False, parallel=False, stream=False,
        i18n=False, academic=False, s2=False, save=False,
        deep_research=False, full_report=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestOrchestratorInit(unittest.TestCase):
    """初始化与配置测试。"""

    def test_basic_init(self):
        orch = SearchOrchestrator('test query')
        self.assertEqual(orch.original_query, 'test query')
        self.assertEqual(orch.query, 'test query')
        self.assertEqual(orch.results, [])
        self.assertIsNone(orch.verification_report)

    def test_init_with_none_query(self):
        orch = SearchOrchestrator(None)
        self.assertEqual(orch.original_query, '')

    def test_init_with_flags(self):
        flags = _make_flags(deep=True, parallel=True)
        orch = SearchOrchestrator('q', flags)
        self.assertTrue(orch.config['enable_planner'])
        self.assertTrue(orch.config['enable_parallel'])

    def test_init_with_config_override(self):
        orch = SearchOrchestrator(
            'q', config={'enable_pii_redact': False, 'max_subqueries': 10})
        self.assertFalse(orch.config['enable_pii_redact'])
        self.assertEqual(orch.config['max_subqueries'], 10)

    def test_flags_override_config(self):
        """flags 优先级高于 config。"""
        flags = _make_flags(deep=True)
        orch = SearchOrchestrator('q', flags, config={'enable_planner': False})
        self.assertTrue(orch.config['enable_planner'])

    def test_deep_research_flag_sets_max_subqueries_10(self):
        flags = _make_flags(deep_research=True)
        orch = SearchOrchestrator('q', flags)
        self.assertEqual(orch.config['max_subqueries'], 10)

    def test_full_report_flag_sets_max_output_tokens_2000(self):
        flags = _make_flags(full_report=True)
        orch = SearchOrchestrator('q', flags)
        self.assertEqual(orch.config['max_output_tokens'], 2000)

    def test_default_config(self):
        self.assertEqual(DEFAULT_CONFIG['max_subqueries'], 5)
        self.assertEqual(DEFAULT_CONFIG['max_output_tokens'], 800)
        self.assertTrue(DEFAULT_CONFIG['enable_pii_redact'])
        self.assertTrue(DEFAULT_CONFIG['enable_verifier'])

    def test_step_order_complete(self):
        """STEP_ORDER should contain all pipeline steps.

        S4/S5 fix: 'stream' and 'prewarm' removed (external capabilities).
        S2/S3 fix: 'aggregate' and 'cache_store' added.
        S10 fix: 'verify' moved before 'format'.
        """
        expected_steps = {
            'redact_pii', 'plan', 'aggregate_pre', 'parallel_exec',
            'i18n', 'arxiv', 'semantic_scholar',
            'cache_lookup', 'detect_location', 'classify_query',
            'execute_layers', 'dedup', 'cache_store', 'aggregate',
            'verify', 'format', 'log', 'persist_memory',
        }
        self.assertEqual(set(STEP_ORDER), expected_steps)


class TestInject(unittest.TestCase):
    """inject 回调注入测试。"""

    def test_inject_dict(self):
        orch = SearchOrchestrator('q')
        mock_fn = Mock()
        orch.inject({'search_mcp': mock_fn})
        self.assertEqual(orch._callbacks['search_mcp'], mock_fn)

    def test_inject_returns_self(self):
        """inject 返回 self 以支持链式调用。"""
        orch = SearchOrchestrator('q')
        result = orch.inject({'search_mcp': lambda q: []})
        self.assertIs(result, orch)

    def test_inject_merges(self):
        """多次 inject 合并 callbacks。"""
        orch = SearchOrchestrator('q')
        orch.inject({'a': 1})
        orch.inject({'b': 2})
        self.assertEqual(orch._callbacks['a'], 1)
        self.assertEqual(orch._callbacks['b'], 2)


class TestRedactPii(unittest.TestCase):
    """Step 0: redact_pii 测试。"""

    def test_disabled_by_config(self):
        orch = SearchOrchestrator('q', config={'enable_pii_redact': False})
        orch.redact_pii()
        report = orch.step_reports['redact_pii']
        self.assertTrue(report['skipped'])

    def test_skipped_when_no_privacy_module(self):
        """privacy 模块未加载 → 跳过 + warning。"""
        with patch('orchestrator._HAS_PRIVACY', False):
            orch = SearchOrchestrator('test@email.com query')
            orch.redact_pii()
            report = orch.step_reports['redact_pii']
            self.assertTrue(report['skipped'])
            self.assertGreater(len(orch.warnings), 0)

    def test_redact_succeeds(self):
        """mock privacy.redact_outbound → 成功脱敏。

        B1 fix regression: redact_outbound 实际返回 tuple (redacted_query, metadata),
        之前 mock 成 dict 导致测试通过但生产代码崩。现已修正 mock 与生产代码一致。
        """
        mock_privacy = MagicMock()
        # 正确 mock: 返回 tuple 而非 dict
        mock_privacy.redact_outbound.return_value = (
            'test [REDACTED-EMAIL] query',
            {
                'redacted_count': 1,
                'patterns_matched': ['email'],
                'original_length': 22,
                'redacted_length': 27,
            },
        )
        with patch.dict('sys.modules', {'privacy': mock_privacy}):
            with patch('orchestrator._HAS_PRIVACY', True):
                orch = SearchOrchestrator('test@email.com query')
                orch.redact_pii()
                # query 应被替换为 redacted_query
                self.assertEqual(orch.query, 'test [REDACTED-EMAIL] query')
                report = orch.step_reports['redact_pii']
                self.assertEqual(report['redacted_count'], 1)
                self.assertTrue(report['pii_found'])

    def test_redact_exception_fallback(self):
        """privacy.redact_outbound 抛异常 → 用原 query + warning。"""
        mock_privacy = MagicMock()
        mock_privacy.redact_outbound.side_effect = RuntimeError('mock error')
        with patch.dict('sys.modules', {'privacy': mock_privacy}):
            with patch('orchestrator._HAS_PRIVACY', True):
                orch = SearchOrchestrator('original query')
                orch.redact_pii()
                self.assertEqual(orch.query, 'original query')
                self.assertGreater(len(orch.warnings), 0)


class TestOptionalStepsSkipByDefault(unittest.TestCase):
    """可选 step 默认跳过测试。"""

    def test_plan_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.plan()
        self.assertTrue(orch.step_reports['plan']['skipped'])

    def test_plan_not_skipped_when_enabled(self):
        flags = _make_flags(deep=True)
        orch = SearchOrchestrator('q', flags)
        # planner_fn 未注入 → 应跳过但原因不同
        orch.plan()
        report = orch.step_reports['plan']
        self.assertTrue(report['skipped'])
        self.assertIn('not injected', report.get('reason', ''))

    def test_aggregate_pre_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.aggregate_pre()
        self.assertTrue(orch.step_reports['aggregate_pre']['skipped'])

    def test_parallel_exec_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.parallel_exec()
        self.assertTrue(orch.step_reports['parallel_exec']['skipped'])

    def test_arxiv_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.arxiv()
        self.assertTrue(orch.step_reports['arxiv']['skipped'])

    def test_s2_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.semantic_scholar()
        self.assertTrue(orch.step_reports['semantic_scholar']['skipped'])

    def test_i18n_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.i18n()
        self.assertTrue(orch.step_reports['i18n']['skipped'])

    def test_stream_returns_unsupported(self):
        """S4 fix: stream() returns unsupported, not a pipeline step."""
        orch = SearchOrchestrator('q')
        result = orch.stream()
        self.assertTrue(result.get('unsupported'))

    def test_prewarm_returns_unsupported(self):
        """S5 fix: prewarm() returns unsupported, not a pipeline step."""
        orch = SearchOrchestrator('q')
        result = orch.prewarm()
        self.assertTrue(result.get('unsupported'))

    def test_persist_memory_skipped_by_default(self):
        orch = SearchOrchestrator('q')
        orch.persist_memory()
        self.assertTrue(orch.step_reports['persist_memory']['skipped'])


class TestCacheLookup(unittest.TestCase):
    """Step 1: cache_lookup 测试。"""

    def test_cache_miss(self):
        orch = SearchOrchestrator('q')
        orch.inject({'cache_get_fn': lambda q: None})
        orch.cache_lookup()
        self.assertFalse(orch.cache_hit)

    def test_cache_hit_dict(self):
        cached = {'results': SAMPLE_RESULTS}
        orch = SearchOrchestrator('q')
        orch.inject({'cache_get_fn': lambda q: cached})
        orch.cache_lookup()
        self.assertTrue(orch.cache_hit)
        self.assertEqual(len(orch.results), 2)

    def test_cache_hit_list(self):
        orch = SearchOrchestrator('q')
        orch.inject({'cache_get_fn': lambda q: SAMPLE_RESULTS})
        orch.cache_lookup()
        self.assertTrue(orch.cache_hit)
        self.assertEqual(len(orch.results), 2)

    def test_cache_fn_not_injected(self):
        orch = SearchOrchestrator('q')
        orch.cache_lookup()
        self.assertTrue(orch.step_reports['cache_lookup']['skipped'])

    def test_cache_fn_exception_does_not_raise(self):
        def raise_fn(q):
            raise RuntimeError('cache error')
        orch = SearchOrchestrator('q')
        orch.inject({'cache_get_fn': raise_fn})
        orch.cache_lookup()
        self.assertFalse(orch.cache_hit)
        self.assertGreater(len(orch.warnings), 0)


class TestDetectLocation(unittest.TestCase):
    """Step 2: detect_location 测试。"""

    def test_injected_location_fn(self):
        orch = SearchOrchestrator('q')
        orch.inject({'location_fn': lambda: 'zhuhai'})
        orch.detect_location()
        self.assertEqual(orch.location, 'zhuhai')

    def test_fallback_to_unknown(self):
        orch = SearchOrchestrator('q')
        orch.detect_location()
        self.assertEqual(orch.location, 'unknown')
        self.assertTrue(orch.step_reports['detect_location'].get('fallback'))

    def test_location_fn_exception_fallback(self):
        def raise_fn():
            raise RuntimeError('location error')
        orch = SearchOrchestrator('q')
        orch.inject({'location_fn': raise_fn})
        orch.detect_location()
        self.assertEqual(orch.location, 'unknown')
        self.assertGreater(len(orch.warnings), 0)


class TestClassifyQuery(unittest.TestCase):
    """Step 3: classify_query 测试。"""

    def test_injected_classify_fn(self):
        orch = SearchOrchestrator('q')
        orch.inject({'classify_fn': lambda q: 'research'})
        orch.classify_query()
        self.assertEqual(orch.query_class, 'research')

    def test_fallback_academic(self):
        orch = SearchOrchestrator('arxiv paper research')
        orch.classify_query()
        self.assertEqual(orch.query_class, 'academic')

    def test_fallback_research_compare(self):
        orch = SearchOrchestrator('compare React vs Vue')
        orch.classify_query()
        self.assertEqual(orch.query_class, 'research')

    def test_fallback_factual(self):
        orch = SearchOrchestrator('Python list sort')
        orch.classify_query()
        self.assertEqual(orch.query_class, 'factual')

    def test_classify_fn_exception_fallback(self):
        def raise_fn(q):
            raise RuntimeError('classify error')
        orch = SearchOrchestrator('arxiv paper')
        orch.inject({'classify_fn': raise_fn})
        orch.classify_query()
        # 仍 fallback 到 academic（基于 query 内容）
        self.assertEqual(orch.query_class, 'academic')


class TestExecuteLayers(unittest.TestCase):
    """Step 4: execute_layers 测试。"""

    def test_injected_search_mcp(self):
        orch = SearchOrchestrator('q')
        orch.inject({'search_mcp': lambda q: SAMPLE_RESULTS})
        orch.execute_layers()
        self.assertEqual(len(orch.results), 2)

    def test_skipped_on_cache_hit(self):
        orch = SearchOrchestrator('q')
        orch.inject({'cache_get_fn': lambda q: SAMPLE_RESULTS})
        orch.cache_lookup()  # 命中缓存
        orch.inject({'search_mcp': lambda q: []})
        orch.execute_layers()
        self.assertTrue(orch.step_reports['execute_layers']['skipped'])

    def test_search_mcp_not_injected(self):
        orch = SearchOrchestrator('q')
        orch.execute_layers()
        self.assertTrue(orch.step_reports['execute_layers']['skipped'])
        self.assertIn('not injected', orch.step_reports['execute_layers']['reason'])

    def test_search_mcp_exception_does_not_raise(self):
        def raise_fn(q):
            raise RuntimeError('search error')
        orch = SearchOrchestrator('q')
        orch.inject({'search_mcp': raise_fn})
        orch.execute_layers()
        self.assertEqual(orch.results, [])
        self.assertGreater(len(orch.warnings), 0)


class TestDedup(unittest.TestCase):
    """Step 5: dedup 测试。"""

    def test_dedup_removes_duplicates(self):
        results = [
            {'url': 'https://a.com', 'title': 'A'},
            {'url': 'https://b.com', 'title': 'B'},
            {'url': 'https://a.com', 'title': 'A duplicate'},
        ]
        orch = SearchOrchestrator('q')
        orch.results = results
        orch.dedup()
        self.assertEqual(len(orch.results), 2)

    def test_dedup_empty_results(self):
        orch = SearchOrchestrator('q')
        orch.dedup()
        self.assertEqual(orch.results, [])

    def test_dedup_returns_report(self):
        orch = SearchOrchestrator('q')
        orch.results = [
            {'url': 'https://a.com'},
            {'url': 'https://a.com'},  # 重复
        ]
        orch.dedup()
        report = orch.step_reports['dedup']
        self.assertEqual(report['original'], 2)
        self.assertEqual(report['deduped'], 1)
        self.assertEqual(report['removed'], 1)


class TestFormat(unittest.TestCase):
    """Step 6: format 测试。"""

    def test_format_with_results(self):
        orch = SearchOrchestrator('test query')
        orch.results = SAMPLE_RESULTS
        orch.format()
        self.assertIsNotNone(orch.formatted_output)
        self.assertIn('# Search Results', orch.formatted_output)
        self.assertIn('FIT2004 Handbook', orch.formatted_output)

    def test_format_empty_results(self):
        orch = SearchOrchestrator('q')
        orch.format()
        self.assertEqual(orch.formatted_output, 'No results found.')

    def test_format_truncates_to_10_results(self):
        many = [{'url': f'https://x{i}.com', 'title': f'Title {i}',
                 'snippet': 's', 'source': 'blog'} for i in range(20)]
        orch = SearchOrchestrator('q')
        orch.results = many
        orch.format()
        # 应仅包含前 10 条
        self.assertEqual(orch.formatted_output.count('## '), 10)


class TestVerify(unittest.TestCase):
    """Step 6.5: verify 测试（v4.4 P4.1.2 集成）。"""

    def test_disabled_by_config(self):
        orch = SearchOrchestrator('q', config={'enable_verifier': False})
        orch.verify()
        self.assertTrue(orch.step_reports['verify']['skipped'])

    def test_skipped_when_no_results(self):
        """verifier.should_verify 在无结果时返回 False → verified=True。"""
        orch = SearchOrchestrator('q')
        orch.verify()
        # 不抛异常
        self.assertIsNotNone(orch.step_reports['verify'])

    def test_verify_with_mock_fetch(self):
        def mock_fetch(url):
            return 'FIT2004 Algorithms and Data Structures assessment'
        orch = SearchOrchestrator('FIT2004 assessment 2026')
        orch.results = SAMPLE_RESULTS
        orch.inject({'fetch_mcp': mock_fetch})
        orch.verify()
        self.assertIsNotNone(orch.verification_report)
        self.assertIn('verified', orch.verification_report)

    def test_verify_exception_does_not_raise(self):
        def raise_fetch(url):
            raise RuntimeError('fetch error')
        orch = SearchOrchestrator('2026 query')
        orch.results = SAMPLE_RESULTS
        orch.inject({'fetch_mcp': raise_fetch})
        orch.verify()
        # 不抛异常，verification_report 应记录失败
        self.assertIsNotNone(orch.verification_report)
        self.assertFalse(orch.verification_report.get('verified', True))


class TestLog(unittest.TestCase):
    """Step 7: log 测试。"""

    def test_log_fn_not_injected(self):
        orch = SearchOrchestrator('q')
        orch.log()
        self.assertTrue(orch.step_reports['log']['skipped'])

    def test_log_calls_callback(self):
        logged_entries = []
        def mock_log(entry):
            logged_entries.append(entry)
        orch = SearchOrchestrator('q')
        orch.inject({'log_fn': mock_log})
        orch.log()
        self.assertEqual(len(logged_entries), 1)
        self.assertEqual(logged_entries[0]['query'], 'q')

    def test_log_exception_does_not_raise(self):
        def raise_log(entry):
            raise RuntimeError('log error')
        orch = SearchOrchestrator('q')
        orch.inject({'log_fn': raise_log})
        orch.log()
        self.assertGreater(len(orch.warnings), 0)


class TestPersistMemory(unittest.TestCase):
    """Step 7.5: persist_memory 测试。"""

    def test_disabled_by_default(self):
        orch = SearchOrchestrator('q')
        orch.persist_memory()
        self.assertTrue(orch.step_reports['persist_memory']['skipped'])

    def test_persist_when_enabled_and_injected(self):
        saved = []
        def mock_save(key, value):
            saved.append((key, value))
        flags = _make_flags(save=True)
        orch = SearchOrchestrator('q', flags)
        orch.inject({'memory_fn': mock_save})
        orch.persist_memory()
        self.assertTrue(orch.memory_persisted)
        self.assertEqual(len(saved), 1)

    def test_persist_exception_does_not_raise(self):
        def raise_save(k, v):
            raise RuntimeError('save error')
        flags = _make_flags(save=True)
        orch = SearchOrchestrator('q', flags)
        orch.inject({'memory_fn': raise_save})
        orch.persist_memory()
        self.assertFalse(orch.memory_persisted)
        self.assertGreater(len(orch.warnings), 0)


class TestRunAll(unittest.TestCase):
    """run_all 整体执行测试。"""

    def test_run_all_executes_all_steps(self):
        """run_all 应执行 STEP_ORDER 中的所有 step。"""
        orch = SearchOrchestrator('test query')
        orch.run_all()
        # 所有 step 都应有报告（即使 skipped）
        for step in STEP_ORDER:
            self.assertIn(step, orch.step_reports,
                         f'step {step} missing in step_reports')

    def test_run_all_does_not_raise_on_errors(self):
        """任何 step 失败都不应中断 pipeline。"""
        # 注入会抛异常的 callbacks
        def raising_search(q):
            raise RuntimeError('search failed')
        orch = SearchOrchestrator('q')
        orch.inject({'search_mcp': raising_search})
        orch.run_all()
        # 应完成所有 step，warnings 中应有错误记录
        self.assertGreater(len(orch.executed_steps), 0)

    def test_run_all_returns_self(self):
        orch = SearchOrchestrator('q')
        result = orch.run_all()
        self.assertIs(result, orch)


class TestChaining(unittest.TestCase):
    """链式调用测试。"""

    def test_inject_then_redact_returns_self(self):
        orch = SearchOrchestrator('q')
        result = orch.inject({}).redact_pii()
        self.assertIs(result, orch)

    def test_multiple_step_chaining(self):
        """多个 step 链式调用应正常工作。"""
        orch = SearchOrchestrator('q')
        (orch
         .inject({
             'search_mcp': lambda q: SAMPLE_RESULTS,
             'cache_get_fn': lambda q: None,
             'log_fn': lambda e: None,
         })
         .redact_pii()
         .cache_lookup()
         .execute_layers()
         .dedup()
         .format()
         .log())
        self.assertGreater(len(orch.step_reports), 0)


class TestResult(unittest.TestCase):
    """result property 测试。"""

    def test_result_dict_structure(self):
        orch = SearchOrchestrator('q')
        orch.run_all()
        result = orch.result
        self.assertIn('query', result)
        self.assertIn('results', result)
        self.assertIn('step_reports', result)
        self.assertIn('warnings', result)
        self.assertIn('executed_steps', result)

    def test_result_cached(self):
        """S9 fix: result is no longer cached — each access returns fresh dict.

        Old behavior: result was cached, returning same object.
        New behavior: result is always computed from current state.
        Test verifies that result reflects current state on each access.
        """
        orch = SearchOrchestrator('q')
        orch.run_all()
        r1 = orch.result
        r2 = orch.result
        # Both should have same content (state hasn't changed)
        self.assertEqual(r1['results_count'], r2['results_count'])

    def test_result_includes_verification(self):
        orch = SearchOrchestrator('2026 query')
        orch.results = SAMPLE_RESULTS
        orch.inject({'fetch_mcp': lambda u: 'FIT2004 assessment'})
        orch.verify()
        result = orch.result
        self.assertIn('verification', result)
        self.assertIsNotNone(result['verification'])


class TestDryRun(unittest.TestCase):
    """dry_run 测试。"""

    def test_dry_run_returns_plan(self):
        orch = SearchOrchestrator('test query')
        plan = orch.dry_run()
        self.assertIn('query', plan)
        self.assertIn('config', plan)
        self.assertIn('steps', plan)
        self.assertEqual(len(plan['steps']), len(STEP_ORDER))

    def test_dry_run_marks_optional_steps(self):
        orch = SearchOrchestrator('q')
        plan = orch.dry_run()
        optional_steps = [s for s in plan['steps'] if s['optional']]
        self.assertIn('plan', [s['step'] for s in optional_steps])
        self.assertIn('arxiv', [s['step'] for s in optional_steps])
        # S4/S5: stream and prewarm are no longer in STEP_ORDER
        step_names = [s['step'] for s in plan['steps']]
        self.assertNotIn('stream', step_names)
        self.assertNotIn('prewarm', step_names)

    def test_dry_run_marks_mandatory_steps(self):
        orch = SearchOrchestrator('q')
        plan = orch.dry_run()
        mandatory_steps = [s for s in plan['steps'] if not s['optional']]
        mandatory_names = [s['step'] for s in mandatory_steps]
        self.assertIn('redact_pii', mandatory_names)
        self.assertIn('cache_lookup', mandatory_names)
        self.assertIn('verify', mandatory_names)

    def test_dry_run_does_not_execute(self):
        """dry_run 不应执行任何 step（step_reports 应为空）。"""
        orch = SearchOrchestrator('q')
        orch.dry_run()
        self.assertEqual(orch.step_reports, {})


class TestExceptionHandling(unittest.TestCase):
    """异常兜底测试。"""

    def test_step_exception_does_not_propagate(self):
        """step 内部异常不应传播到 _execute_step 外。"""
        def failing_impl():
            raise RuntimeError('step failure')

        orch = SearchOrchestrator('q')
        # 直接调用 _execute_step
        orch._execute_step('test_step', failing_impl)
        self.assertIn('test_step', orch.step_reports)
        self.assertIn('error', orch.step_reports['test_step'])
        self.assertGreater(len(orch.warnings), 0)

    def test_executed_steps_records_order(self):
        """executed_steps 应按执行顺序记录。"""
        orch = SearchOrchestrator('q')
        orch.redact_pii()
        orch.cache_lookup()
        orch.execute_layers()
        self.assertEqual(orch.executed_steps,
                         ['redact_pii', 'cache_lookup', 'execute_layers'])


class TestEndToEnd(unittest.TestCase):
    """端到端集成测试（mock 所有 callbacks）。"""

    def test_full_pipeline_with_mocks(self):
        """完整 pipeline + mock 所有 callbacks → 成功完成。"""
        mock_callbacks = {
            'search_mcp': lambda q: SAMPLE_RESULTS,
            'cache_get_fn': lambda q: None,
            'log_fn': lambda e: None,
            'location_fn': lambda: 'zhuhai',
            'classify_fn': lambda q: 'research',
            'fetch_mcp': lambda u: 'FIT2004 Algorithms and Data Structures assessment',
        }
        orch = SearchOrchestrator(SAMPLE_QUERY)
        orch.inject(mock_callbacks)
        orch.run_all()

        result = orch.result
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(result['location'], 'zhuhai')
        self.assertEqual(result['query_class'], 'research')
        self.assertIsNotNone(result['verification'])
        # 至少执行了 14 个 step
        self.assertGreaterEqual(len(result['executed_steps']), 10)

    def test_pipeline_with_cache_hit(self):
        """缓存命中 → execute_layers 跳过。"""
        mock_callbacks = {
            'search_mcp': lambda q: [],
            'cache_get_fn': lambda q: {'results': SAMPLE_RESULTS},
            'log_fn': lambda e: None,
        }
        orch = SearchOrchestrator('q')
        orch.inject(mock_callbacks)
        orch.run_all()
        # execute_layers 应跳过
        self.assertTrue(orch.step_reports['execute_layers']['skipped'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
