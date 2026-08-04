#!/usr/bin/env python3
"""orchestrator.py — v4.4 P4.1.3 可执行 Search Pipeline

针对 Agent-Forge 错误根因 A（/search 命令是 Markdown 指令而非可执行 pipeline）的修复：
- 原 search.md 14 个 step 依赖 LLM 按文档执行，存在跳步风险
- Agent-Forge 可能跳过 "回访权威源" 这一步
- 不同模型 / 会话可能跳步

本模块把 14 个 step 封装为可执行 Python pipeline：
- SearchOrchestrator 类：方法链式调用
- 每 step 独立可测试（mock-able）
- 失败兜底：任何 step 失败不阻塞后续 step
- --dry-run 模式：打印 pipeline 计划但不执行
- --legacy 模式：回退到旧 search.md 指令路径（回退策略）

设计原则：
- 单文件聚合（不依赖其他 search 模块的可变状态）
- 零外部依赖（仅标准库）
- 异常兜底（任何步骤失败都不抛异常，记录到 warnings）
- 可观测性（每 step 返回 step_report，最终汇总）

Step 列表（对应 search.md）：
    0.   redact_pii          — Layer 0 PII 脱敏
    0.5.  plan                — MindSearch Planner 分解（可选）
    0.7.  aggregate_pre       — MindSearch Aggregator 预聚合（可选）
    0.8.  parallel_exec       — 并行执行三层 MCP（可选）
    0.9.  stream              — 流式输出 JSON Lines（可选）
    0.10. prewarm             — 缓存预热（可选）
    0.11. i18n                — 跨语言扩展（可选）
    0.12. arxiv               — arXiv 学术搜索（可选）
    0.13. semantic_scholar    — Semantic Scholar（可选）
    1.    cache_lookup        — 缓存查找
    2.    detect_location     — 位置检测（珠海 / 马来西亚）
    3.    classify_query      — 查询分类
    4.    execute_layers      — 三层 MCP 顺序执行
    5.    dedup               — URL 去重
    6.    format              — 格式化输出
    6.5.  verify              — v4.4 P4.1.2 验证回路
    7.    log                 — 历史日志记录
    7.5.  persist_memory      — memory MCP 持久化
"""
import os
import sys
import json
import time
import argparse
from typing import Any, Callable, Optional

# 添加 search 模块目录到 path
_SEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

# v4.4 P4.1.3: 不在 import 时硬依赖（避免循环依赖或缺失模块时崩溃）
# 所有 cross-module 调用通过 inject 注入
try:
    import privacy as _privacy_mod  # noqa: F401
    _HAS_PRIVACY = True
except ImportError:
    _HAS_PRIVACY = False

try:
    import verifier as _verifier_mod  # noqa: F401
    _HAS_VERIFIER = True
except ImportError:
    _HAS_VERIFIER = False


# ── 异常 ───────────────────────────────────────────────────────

class StepExecutionError(Exception):
    """Step 执行错误（不阻塞后续 step）。"""


class PipelineIntegrityError(Exception):
    """Pipeline 完整性错误（致命，应中止）。"""


# ── 默认配置 ────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    'enable_pii_redact': True,
    'enable_planner': False,        # 默认关闭（--deep 才启用）
    'enable_aggregator': False,     # 默认关闭（--aggregate 才启用）
    'enable_parallel': False,       # 默认关闭（--parallel 才启用）
    'enable_stream': False,
    'enable_prewarm': False,
    'enable_i18n': False,
    'enable_arxiv': False,          # 默认关闭（--academic 才启用）
    'enable_s2': False,              # 默认关闭（--s2 才启用）
    'enable_verifier': True,         # v4.4 P4.1.2 默认启用
    'enable_memory': False,          # 默认关闭（--save 才启用）
    'max_subqueries': 5,             # 默认 5（--deep-research 提升到 10）
    'max_output_tokens': 800,        # 默认 800（--full-report 提升到 2000）
    'cache_ttl': 24 * 3600,
    'fetch_timeout': 10,
}

# Step 执行顺序（按 search.md）
# S4/S5 fix: 'stream' and 'prewarm' removed from STEP_ORDER.
#   They are external capabilities, not pipeline steps that execute per-query.
#   stream_events() and prewarm_cache() are standalone functions.
STEP_ORDER = [
    'redact_pii',         # 0
    'plan',                # 0.5 (optional)
    'aggregate_pre',       # 0.7 (optional)
    'parallel_exec',       # 0.8 (optional)
    'i18n',                 # 0.11 (optional)
    'arxiv',                # 0.12 (optional)
    'semantic_scholar',     # 0.13 (optional)
    'cache_lookup',         # 1
    'detect_location',      # 2
    'classify_query',       # 3
    'execute_layers',       # 4
    'dedup',                # 5
    'cache_store',          # 5.5 (S3 fix: write to cache after dedup)
    'aggregate',            # 5.7 (S2 fix: actual aggregation, not just a marker)
    'verify',               # 5.8 (S10 fix: moved before format so output includes verification)
    'format',               # 6 (S10 fix: includes verification status in output)
    'log',                  # 7
    'persist_memory',       # 7.5 (optional)
]

# S7 fix: explicit mapping from step_name to config key.
# Both real execution and dry_run use this mapping via is_step_enabled().
STEP_CONFIG_KEYS = {
    'redact_pii': 'enable_pii_redact',
    'plan': 'enable_planner',
    'aggregate_pre': 'enable_aggregator',
    'parallel_exec': 'enable_parallel',
    'i18n': 'enable_i18n',
    'arxiv': 'enable_arxiv',
    'semantic_scholar': 'enable_s2',
    'cache_lookup': None,        # always enabled (no config key)
    'detect_location': None,     # always enabled
    'classify_query': None,      # always enabled
    'execute_layers': None,      # always enabled
    'dedup': None,               # always enabled
    'cache_store': None,         # always enabled (S3)
    'aggregate': 'enable_aggregator',  # S2: uses same flag as aggregate_pre
    'format': None,              # always enabled
    'verify': 'enable_verifier',
    'log': None,                 # always enabled
    'persist_memory': 'enable_memory',
}

# Steps that are optional (default disabled unless flag/config enables them)
OPTIONAL_STEPS = frozenset({
    'plan', 'aggregate_pre', 'parallel_exec',
    'i18n', 'arxiv', 'semantic_scholar', 'aggregate', 'persist_memory',
})


# ── SearchOrchestrator 类 ───────────────────────────────────────

class SearchOrchestrator:
    """可执行 Search Pipeline 编排器。

    使用方式：
        orchestrator = SearchOrchestrator(query, flags)
        orchestrator.redact_pii()
        orchestrator.cache_lookup()
        orchestrator.execute_layers()
        ...
        result = orchestrator.result

    或链式调用：
        result = (SearchOrchestrator(query)
                  .redact_pii()
                  .cache_lookup()
                  .execute_layers()
                  .result)

    通过 inject(callbacks) 注入外部 MCP 调用：
        orchestrator.inject({
            'search_mcp': mcp_callers.get('duckduckgo'),
            'fetch_mcp': mcp_callers.get('fetch'),
            ...
        })
    """

    def __init__(self, query, flags=None, config=None):
        """初始化 orchestrator。

        Args:
            query: 用户原始查询
            flags: 命令行 flags 对象（argparse Namespace）
            config: 覆盖默认配置的 dict
        """
        self.original_query = str(query or '')
        self.query = self.original_query  # 可被 redact_pii / i18n 修改
        self.flags = flags or argparse.Namespace()
        self.config = {**DEFAULT_CONFIG, **(config or {})}

        # 从 flags 更新 config（flags 优先）
        self._update_config_from_flags()

        # Pipeline 状态
        self.results = []                # 搜索结果列表
        self.aggregated = None            # aggregator 输出
        self.subqueries = []              # planner 输出
        self.cache_hit = False            # 缓存命中
        self.location = None              # 'zhuhai' | 'malaysia' | 'unknown'
        self.query_class = None           # 'factual' | 'research' | 'academic' | ...
        self.verification_report = None  # v4.4 P4.1.2 验证报告
        self.formatted_output = None     # 最终输出文本
        self.memory_persisted = False

        # 执行追踪
        self.step_reports = {}            # step_name -> step_report
        self.warnings = []                # 全 pipeline 警告
        self.errors = []                 # 全 pipeline 错误（不阻塞）
        self.executed_steps = []          # 已执行的 step 列表（顺序）
        self.start_time = None
        self.end_time = None

        # 外部回调（通过 inject 注入）
        self._callbacks = {}

        # 最终结果
        self._result = None

    # ── 配置 / 注入 ────────────────────────────────────────────

    def _update_config_from_flags(self):
        """从 flags 更新 config（flags 优先级高于默认）。"""
        flag_attrs = {
            'deep': 'enable_planner',
            'aggregate': 'enable_aggregator',
            'parallel': 'enable_parallel',
            'stream': 'enable_stream',
            'i18n': 'enable_i18n',
            'academic': 'enable_arxiv',
            's2': 'enable_s2',
            'save': 'enable_memory',
            'deep_research': 'max_subqueries',  # 需特殊处理
            'full_report': 'max_output_tokens',
        }

        for flag_attr, config_key in flag_attrs.items():
            if hasattr(self.flags, flag_attr):
                flag_value = getattr(self.flags, flag_attr)
                if flag_value:
                    if config_key == 'max_subqueries':
                        self.config['max_subqueries'] = 10  # --deep-research
                    elif config_key == 'max_output_tokens':
                        self.config['max_output_tokens'] = 2000  # --full-report
                    else:
                        self.config[config_key] = True

    def inject(self, callbacks):
        """注入外部 MCP 调用回调。

        Args:
            callbacks: dict，可包含：
                - 'search_mcp': (query) -> list[dict]  搜索 MCP（duckduckgo/searxng/serper）
                - 'fetch_mcp': (url) -> str           fetch MCP（用于 verifier）
                - 'planner_fn': (query) -> dict       planner 模块
                - 'aggregator_fn': (query, results) -> str   # S2: actually called now
                - 'cache_get_fn': (query) -> dict|None
                - 'cache_store_fn': (query, results) -> None  # S3: actually called now
                - 'log_fn': (entry) -> None
                - 'memory_fn': (key, value) -> None
        """
        if isinstance(callbacks, dict):
            self._callbacks.update(callbacks)
        return self

    # ── 辅助方法 ────────────────────────────────────────────────

    def is_step_enabled(self, step_name):
        """S7 fix: unified step-enable check used by both execution and dry_run.

        Args:
            step_name: step name from STEP_ORDER

        Returns:
            bool: True if the step should execute
        """
        config_key = STEP_CONFIG_KEYS.get(step_name)
        if config_key is None:
            # Mandatory step (no config key) → always enabled
            return True
        return self.config.get(config_key, False) if step_name in OPTIONAL_STEPS \
            else self.config.get(config_key, True)

    def _invalidate_result(self):
        """S9 fix: invalidate cached _result on any state change."""
        self._result = None

    # ── Step 0: redact_pii ──────────────────────────────────────

    def redact_pii(self):
        """Step 0: Layer 0 出境 PII 脱敏。"""
        return self._execute_step('redact_pii', self._redact_pii_impl)

    def _redact_pii_impl(self):
        if not self.config['enable_pii_redact']:
            self.query = self.original_query
            return {'skipped': True, 'reason': 'disabled by config'}

        if not _HAS_PRIVACY:
            # privacy 模块未加载 → 不脱敏，仅 warning
            self.warnings.append('privacy module not available, skipping PII redact')
            self.query = self.original_query
            return {'skipped': True, 'reason': 'privacy module not loaded'}

        try:
            from privacy import redact_outbound  # 延迟 import
            # B1 fix: redact_outbound returns (redacted_query, metadata) tuple,
            # NOT a dict. Previous code called .get() on the tuple → AttributeError
            # → fallback to original_query → PII bypassed (CRITICAL security bug).
            redacted_query, metadata = redact_outbound(self.original_query)
            self.query = redacted_query
            return {
                'original_query': self.original_query,
                'redacted_query': self.query,
                'patterns_matched': metadata.get('patterns_matched', []),
                'pii_found': metadata.get('redacted_count', 0) > 0,
                'redacted_count': metadata.get('redacted_count', 0),
            }
        except Exception as e:
            # 兜底：脱敏失败 → 用原 query，仅 warning
            self.warnings.append(f'PII redact failed: {type(e).__name__}')
            self.query = self.original_query
            return {'error': str(e)[:200], 'fallback': 'use original query'}

    # ── Step 0.5: plan ───────────────────────────────────────────

    def plan(self):
        """Step 0.5: MindSearch Planner 分解。"""
        return self._execute_step('plan', self._plan_impl)

    def _plan_impl(self):
        if not self.config['enable_planner']:
            return {'skipped': True}

        planner_fn = self._callbacks.get('planner_fn')
        if planner_fn is None:
            self.warnings.append('planner_fn not injected, skipping plan')
            return {'skipped': True, 'reason': 'planner_fn not injected'}

        try:
            result = planner_fn(self.query)
            if isinstance(result, dict):
                # S1 fix: canonical field is 'sub_queries' (matches planner.py output).
                # Backward compat: also read deprecated 'subqueries' with warning.
                self.subqueries = result.get('sub_queries', [])
                if not self.subqueries and 'subqueries' in result:
                    import warnings
                    warnings.warn(
                        "planner_fn returned 'subqueries' (deprecated); "
                        "use 'sub_queries' instead",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                    self.subqueries = result.get('subqueries', [])
            elif isinstance(result, list):
                self.subqueries = result
            return {
                'sub_queries': self.subqueries,
                'count': len(self.subqueries),
                'max': self.config['max_subqueries'],
            }
        except Exception as e:
            self.warnings.append(f'planner failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 0.7: aggregate_pre ─────────────────────────────────

    def aggregate_pre(self):
        """Step 0.7: Aggregator 预聚合（在搜索前准备 aggregator 模板）。"""
        return self._execute_step('aggregate_pre', self._aggregate_pre_impl)

    def _aggregate_pre_impl(self):
        if not self.config['enable_aggregator']:
            return {'skipped': True}
        # 仅标记需要聚合，实际聚合在 format 之后
        return {'aggregation_enabled': True}

    # ── Step 0.8: parallel_exec ────────────────────────────────

    def parallel_exec(self):
        """Step 0.8: 并行执行三层 MCP（可选，与 execute_layers 互斥）。"""
        return self._execute_step('parallel_exec', self._parallel_exec_impl)

    def _parallel_exec_impl(self):
        if not self.config['enable_parallel']:
            return {'skipped': True}

        parallel_fn = self._callbacks.get('parallel_fn')
        if parallel_fn is None:
            self.warnings.append('parallel_fn not injected, falling back to sequential')
            return {'skipped': True, 'reason': 'parallel_fn not injected'}

        try:
            results = parallel_fn(self.query)
            if isinstance(results, list):
                self.results = results
            return {'count': len(self.results)}
        except Exception as e:
            self.warnings.append(f'parallel_exec failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── External capabilities (not in STEP_ORDER) ─────────────
    # S4/S5 fix: stream and prewarm are external capabilities,
    # not pipeline steps. They must not return 'success' without doing work.

    def stream(self):
        """S4 fix: Streaming is an external capability, not a pipeline step.

        Returns 'unsupported' when called as a step.
        Use stream_events() for actual streaming.
        """
        return {'unsupported': True, 'reason': 'streaming is an external capability, not a pipeline step'}

    def prewarm(self):
        """S5 fix: Prewarm is an external capability, not a pipeline step.

        Returns 'unsupported' when called as a step.
        Use prewarm_cache() from prewarm.py for actual cache warming.
        """
        return {'unsupported': True, 'reason': 'prewarm is an external capability, not a pipeline step'}

    # ── Step 0.11: i18n ────────────────────────────────────────

    def i18n(self):
        """Step 0.11: 跨语言扩展。"""
        return self._execute_step('i18n', self._i18n_impl)

    def _i18n_impl(self):
        if not self.config['enable_i18n']:
            return {'skipped': True}

        i18n_fn = self._callbacks.get('i18n_fn')
        if i18n_fn is None:
            self.warnings.append('i18n_fn not injected, skipping i18n')
            return {'skipped': True, 'reason': 'i18n_fn not injected'}

        try:
            result = i18n_fn(self.query)
            if isinstance(result, dict):
                # i18n 返回多个语言版本的 query
                self.subqueries = result.get('expanded_queries', [self.query])
            return {'expanded_queries': self.subqueries}
        except Exception as e:
            self.warnings.append(f'i18n failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 0.12: arxiv ───────────────────────────────────────

    def arxiv(self):
        """Step 0.12: arXiv 学术搜索。"""
        return self._execute_step('arxiv', self._arxiv_impl)

    def _arxiv_impl(self):
        if not self.config['enable_arxiv']:
            return {'skipped': True}

        arxiv_fn = self._callbacks.get('arxiv_fn')
        if arxiv_fn is None:
            self.warnings.append('arxiv_fn not injected, skipping arxiv')
            return {'skipped': True, 'reason': 'arxiv_fn not injected'}

        try:
            arxiv_results = arxiv_fn(self.query)
            if isinstance(arxiv_results, list):
                # 合并到 results（标记 source=arxiv）
                for r in arxiv_results:
                    if isinstance(r, dict):
                        r.setdefault('source', 'arxiv')
                self.results.extend(arxiv_results)
            return {'count': len(arxiv_results) if isinstance(arxiv_results, list) else 0}
        except Exception as e:
            self.warnings.append(f'arxiv failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 0.13: semantic_scholar ───────────────────────────

    def semantic_scholar(self):
        """Step 0.13: Semantic Scholar 搜索。"""
        return self._execute_step('semantic_scholar', self._s2_impl)

    def _s2_impl(self):
        if not self.config['enable_s2']:
            return {'skipped': True}

        s2_fn = self._callbacks.get('s2_fn')
        if s2_fn is None:
            self.warnings.append('s2_fn not injected, skipping S2')
            return {'skipped': True, 'reason': 's2_fn not injected'}

        try:
            s2_results = s2_fn(self.query)
            if isinstance(s2_results, list):
                for r in s2_results:
                    if isinstance(r, dict):
                        r.setdefault('source', 'semantic_scholar')
                self.results.extend(s2_results)
            return {'count': len(s2_results) if isinstance(s2_results, list) else 0}
        except Exception as e:
            self.warnings.append(f's2 failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 1: cache_lookup ───────────────────────────────────

    def cache_lookup(self):
        """Step 1: 缓存查找。"""
        return self._execute_step('cache_lookup', self._cache_lookup_impl)

    def _cache_lookup_impl(self):
        cache_get_fn = self._callbacks.get('cache_get_fn')
        if cache_get_fn is None:
            return {'skipped': True, 'reason': 'cache_get_fn not injected'}

        try:
            cached = cache_get_fn(self.query)
            if cached:
                self.cache_hit = True
                if isinstance(cached, dict):
                    self.results = cached.get('results', self.results)
                elif isinstance(cached, list):
                    self.results = cached
                return {'hit': True, 'count': len(self.results)}
            return {'hit': False}
        except Exception as e:
            self.warnings.append(f'cache_lookup failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 2: detect_location ───────────────────────────────

    def detect_location(self):
        """Step 2: 位置检测（珠海 / 马来西亚）。"""
        return self._execute_step('detect_location', self._detect_location_impl)

    def _detect_location_impl(self):
        location_fn = self._callbacks.get('location_fn')
        if location_fn is not None:
            try:
                self.location = location_fn()
                return {'location': self.location}
            except Exception as e:
                self.warnings.append(f'location detect failed: {type(e).__name__}')

        # fallback: 默认 unknown
        self.location = 'unknown'
        return {'location': self.location, 'fallback': True}

    # ── Step 3: classify_query ────────────────────────────────

    def classify_query(self):
        """Step 3: 查询分类。"""
        return self._execute_step('classify_query', self._classify_query_impl)

    def _classify_query_impl(self):
        classify_fn = self._callbacks.get('classify_fn')
        if classify_fn is not None:
            try:
                self.query_class = classify_fn(self.query)
                return {'class': self.query_class}
            except Exception as e:
                self.warnings.append(f'classify failed: {type(e).__name__}')

        # fallback: 简单规则
        query_lower = self.query.lower()
        if any(kw in query_lower for kw in ('arxiv', 'paper', 'research', '论文')):
            self.query_class = 'academic'
        elif any(kw in query_lower for kw in ('compare', 'vs', 'difference')):
            self.query_class = 'research'
        else:
            self.query_class = 'factual'
        return {'class': self.query_class, 'fallback': True}

    # ── Step 4: execute_layers ────────────────────────────────

    def execute_layers(self):
        """Step 4: 三层 MCP 顺序执行。"""
        return self._execute_step('execute_layers', self._execute_layers_impl)

    def _execute_layers_impl(self):
        # 若缓存命中且非并行模式 → 跳过执行
        if self.cache_hit and not self.config['enable_parallel']:
            return {'skipped': True, 'reason': 'cache hit'}

        # 若并行模式已执行 → 跳过
        if self.config['enable_parallel'] and self.results:
            return {'skipped': True, 'reason': 'parallel already executed'}

        search_mcp = self._callbacks.get('search_mcp')
        if search_mcp is None:
            self.warnings.append('search_mcp not injected')
            return {'skipped': True, 'reason': 'search_mcp not injected'}

        try:
            results = search_mcp(self.query)
            if isinstance(results, list):
                self.results = results
            return {'count': len(self.results)}
        except Exception as e:
            self.warnings.append(f'execute_layers failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 5: dedup ─────────────────────────────────────────

    def dedup(self):
        """Step 5: URL 去重。"""
        return self._execute_step('dedup', self._dedup_impl)

    def _dedup_impl(self):
        if not self.results:
            return {'count': 0}

        # S8 fix: URL-less results use stable fingerprint instead of empty string.
        # Fingerprint = source + normalized_title + normalized_snippet prefix.
        seen_keys = set()
        deduped = []
        for r in self.results:
            if not isinstance(r, dict):
                continue
            url = r.get('url', '')
            if url:
                # Normalize URL: lowercase host, strip fragment
                from urllib.parse import urlparse, urlunparse
                try:
                    parsed = urlparse(url)
                    normalized = urlunparse((
                        parsed.scheme.lower(),
                        parsed.netloc.lower(),
                        parsed.path,
                        parsed.params, '', ''  # drop query/fragment for dedup key
                    ))
                except Exception:
                    normalized = url
                key = ('url', normalized)
            else:
                # URL-less: use content fingerprint
                source = str(r.get('source', 'unknown'))
                title = str(r.get('title', '')).strip().lower()[:200]
                snippet = str(r.get('snippet', '')).strip().lower()[:100]
                key = ('fingerprint', source, title, snippet)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(r)
        original_count = len(self.results)
        self.results = deduped
        return {
            'original': original_count,
            'deduped': len(self.results),
            'removed': original_count - len(self.results),
        }

    # ── Step 5.5: cache_store (S3 fix) ────────────────────────

    def cache_store(self):
        """Step 5.5: S3 fix — write deduped results to cache.

        Placed after dedup (clean results) and before format/verify
        (which add derived content that should NOT overwrite raw results).
        """
        return self._execute_step('cache_store', self._cache_store_impl)

    def _cache_store_impl(self):
        # Don't cache if cache was the source (avoid circular overwrite)
        if self.cache_hit:
            return {'skipped': True, 'reason': 'cache hit (no overwrite)'}

        cache_store_fn = self._callbacks.get('cache_store_fn')
        if cache_store_fn is None:
            return {'skipped': True, 'reason': 'cache_store_fn not injected'}

        if not self.results:
            return {'skipped': True, 'reason': 'no results to cache'}

        try:
            cache_entry = {
                'query': self.query,
                'results': self.results,
                'results_count': len(self.results),
                'cached_at': time.time(),
                'retrieved_at': time.time(),
                'cache_schema_version': 2,  # S3: schema version for migration
                'verified': False,  # verification hasn't run yet at this point
            }
            cache_store_fn(self.query, cache_entry)
            return {
                'stored': True,
                'results_count': len(self.results),
                'cached_at': cache_entry['cached_at'],
            }
        except Exception as e:
            self.warnings.append(f'cache_store failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 5.7: aggregate (S2 fix) ──────────────────────────

    def aggregate(self):
        """Step 5.7: S2 fix — actually call aggregator_fn if injected.

        This replaces the old aggregate_pre which only returned a marker.
        Aggregation runs after dedup (clean results) and before format.
        """
        return self._execute_step('aggregate', self._aggregate_impl)

    def _aggregate_impl(self):
        if not self.is_step_enabled('aggregate'):
            return {'skipped': True}

        aggregator_fn = self._callbacks.get('aggregator_fn')
        if aggregator_fn is None:
            return {'skipped': True, 'reason': 'aggregator_fn not injected'}

        if not self.results:
            return {'skipped': True, 'reason': 'no results to aggregate'}

        try:
            aggregated = aggregator_fn(self.query, self.results)
            self.aggregated = aggregated
            return {
                'invoked': True,
                'input_count': len(self.results),
                'output_type': type(aggregated).__name__,
                'output_length': len(str(aggregated)) if aggregated else 0,
            }
        except Exception as e:
            self.warnings.append(f'aggregator failed: {type(e).__name__}')
            # S2: preserve un-aggregated results on failure
            return {
                'invoked': False,
                'error': str(e)[:200],
                'fallback': 'results preserved un-aggregated',
            }

    # ── Step 6: format ────────────────────────────────────────

    def format(self):
        """Step 6: 格式化输出。"""
        return self._execute_step('format', self._format_impl)

    def _format_impl(self):
        if not self.results:
            self.formatted_output = 'No results found.'
            return {'output_length': 0}

        # 简单 markdown 格式化
        lines = [f'# Search Results: {self.query}', '']
        for i, r in enumerate(self.results[:10], 1):
            if not isinstance(r, dict):
                continue
            title = r.get('title', 'Untitled')
            url = r.get('url', '')
            snippet = r.get('snippet', '')[:200]
            source = r.get('source', 'unknown')
            lines.append(f'## {i}. {title}')
            lines.append(f'- URL: {url}')
            lines.append(f'- Source: {source}')
            if snippet:
                lines.append(f'- Snippet: {snippet}')
            lines.append('')

        # S10 fix: include verification status in formatted output
        if self.verification_report is not None:
            lines.append('---')
            lines.append('')
            lines.append('## Verification Status')
            verified = self.verification_report.get('verified', False)
            # S13: use more accurate naming for weak verification
            if verified:
                consistency = self.verification_report.get('cross_check', {}).get('consistency_score', 0)
                if consistency >= 0.8:
                    lines.append(f'- Status: **verified** (lexical_consistency={consistency:.2f})')
                else:
                    lines.append(f'- Status: **weak_support** (lexical_consistency={consistency:.2f})')
            else:
                lines.append(f'- Status: **unverified**')
            trigger = self.verification_report.get('trigger_reason', '')
            if trigger:
                lines.append(f'- Trigger: {trigger}')
            warnings = self.verification_report.get('warnings', [])
            if warnings:
                lines.append(f'- Warnings: {"; ".join(warnings[:3])}')
            lines.append('')

        # S2: include aggregated summary if available
        if self.aggregated:
            lines.append('---')
            lines.append('')
            lines.append('## Aggregated Summary')
            lines.append(str(self.aggregated)[:2000])
            lines.append('')

        self.formatted_output = '\n'.join(lines)
        return {'output_length': len(self.formatted_output)}

    # ── Step 6.5: verify (v4.4 P4.1.2) ───────────────────────

    def verify(self):
        """Step 6.5: v4.4 验证回路。"""
        return self._execute_step('verify', self._verify_impl)

    def _verify_impl(self):
        if not self.config['enable_verifier']:
            return {'skipped': True}

        if not _HAS_VERIFIER:
            self.warnings.append('verifier module not available, skipping verify')
            return {'skipped': True, 'reason': 'verifier module not loaded'}

        try:
            from verifier import verify_against_authority
            fetch_mcp = self._callbacks.get('fetch_mcp')
            report = verify_against_authority(
                self.query,
                self.results,
                fetch_callback=fetch_mcp,
            )
            self.verification_report = report
            if not report.get('verified', False):
                self.warnings.append(
                    f'verification failed: {report.get("trigger_reason", "unknown")}'
                )
            return report
        except Exception as e:
            self.warnings.append(f'verifier failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 7: log ───────────────────────────────────────────

    def log(self):
        """Step 7: 历史日志记录。"""
        return self._execute_step('log', self._log_impl)

    def _log_impl(self):
        log_fn = self._callbacks.get('log_fn')
        if log_fn is None:
            return {'skipped': True, 'reason': 'log_fn not injected'}

        try:
            entry = {
                'query': self.query,  # v4.4: 仅记录脱敏后 query（修复 Review-Risk CRITICAL）
                'timestamp': int(time.time()),
                'results_count': len(self.results),
                'top_results': self.results[:5],
                'location': self.location,
                'query_class': self.query_class,
                'verification': self.verification_report,
                'cache_hit': self.cache_hit,
            }
            # v4.4 Review-Risk CRITICAL: 不记录 original_query，避免 PII 泄露到 log
            log_fn(entry)
            return {'logged': True}
        except Exception as e:
            self.warnings.append(f'log failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── Step 7.5: persist_memory ──────────────────────────────

    def persist_memory(self):
        """Step 7.5: memory MCP 持久化。"""
        return self._execute_step('persist_memory', self._persist_memory_impl)

    def _persist_memory_impl(self):
        if not self.config['enable_memory']:
            return {'skipped': True}

        memory_fn = self._callbacks.get('memory_fn')
        if memory_fn is None:
            self.warnings.append('memory_fn not injected, skipping memory')
            return {'skipped': True, 'reason': 'memory_fn not injected'}

        try:
            key = f'search_{int(time.time())}'
            value = {
                'query': self.query,
                'results': self.results[:5],
                'verification': self.verification_report,
            }
            memory_fn(key, value)
            self.memory_persisted = True
            return {'persisted': True, 'key': key}
        except Exception as e:
            self.warnings.append(f'memory persist failed: {type(e).__name__}')
            return {'error': str(e)[:200]}

    # ── 执行框架 ──────────────────────────────────────────────

    def _execute_step(self, step_name, impl_func):
        """通用 step 执行器（异常兜底 + 时间记录）。

        Args:
            step_name: step 名称（用于追踪）
            impl_func: 实际执行的函数（无参数，返回 dict）

        Returns:
            self（支持链式调用）
        """
        if self.start_time is None:
            self.start_time = time.time()

        step_start = time.time()
        try:
            report = impl_func() or {}
        except Exception as e:
            # 异常兜底：不阻塞后续 step
            report = {
                'error': f'{type(e).__name__}: {str(e)[:200]}',
                'step_failed': True,
            }
            self.warnings.append(f'step {step_name} failed: {type(e).__name__}')

        step_end = time.time()
        report['duration_ms'] = int((step_end - step_start) * 1000)
        report['step'] = step_name

        self.step_reports[step_name] = report
        self.executed_steps.append(step_name)
        self.end_time = time.time()

        # S9 fix: invalidate cached result after every step execution
        self._invalidate_result()

        return self  # 链式调用

    def run_all(self):
        """执行所有 step（按 STEP_ORDER 顺序）。

        Returns:
            self（支持 result = SearchOrchestrator(...).run_all().result）
        """
        for step_name in STEP_ORDER:
            method = getattr(self, step_name, None)
            if method is None:
                continue
            try:
                method()
            except Exception as e:
                # 异常兜底：记录但不中止
                self.warnings.append(f'step {step_name} raised: {type(e).__name__}')
        return self

    # ── 结果 ──────────────────────────────────────────────────

    @property
    def result(self):
        """获取最终 pipeline 结果。

        S9 fix: result is always computed from current state, never cached.
        The old _result cache could return stale data after direct state mutation.
        """
        return {
            'query': self.query,  # v4.4 Review-Risk: 仅返回脱敏后 query
            # v4.4 Review-Risk CRITICAL: original_query 不暴露在 result（PII 防护）
            'results': self.results,
            'results_count': len(self.results),
            'formatted_output': self.formatted_output,
            'verification': self.verification_report,
            'cache_hit': self.cache_hit,
            'location': self.location,
            'query_class': self.query_class,
            'sub_queries': self.subqueries,
            'aggregated': self.aggregated,
            'step_reports': self.step_reports,
            'executed_steps': self.executed_steps,
            'warnings': self.warnings,
            'errors': self.errors,
            'memory_persisted': self.memory_persisted,
            'duration_ms': int(((self.end_time or time.time()) -
                                (self.start_time or time.time())) * 1000),
        }

    def dry_run(self):
        """打印 pipeline 执行计划但不实际执行。"""
        plan = {
            'query': self.original_query,
            'config': self.config,
            'steps': [],
        }
        for step_name in STEP_ORDER:
            method = getattr(self, step_name, None)
            # S7 fix: use unified is_step_enabled() instead of guessing config keys
            enabled = self.is_step_enabled(step_name)
            optional = step_name in OPTIONAL_STEPS
            plan['steps'].append({
                'step': step_name,
                'method': method.__name__ if method else None,
                'enabled': enabled,
                'optional': optional,
                'config_key': STEP_CONFIG_KEYS.get(step_name),
            })
        return plan


# ── CLI 入口 ────────────────────────────────────────────────────

def _main():
    """CLI 入口：python -m modules.search.orchestrator "query" [flags]"""
    parser = argparse.ArgumentParser(
        description='v4.4 P4.1.3 Search Orchestrator — 可执行 Pipeline'
    )
    parser.add_argument('query', help='搜索查询')
    parser.add_argument('--deep', action='store_true', help='启用 MindSearch Planner')
    parser.add_argument('--aggregate', action='store_true', help='启用 Aggregator')
    parser.add_argument('--parallel', action='store_true', help='并行执行三层 MCP')
    parser.add_argument('--i18n', action='store_true', help='跨语言扩展')
    parser.add_argument('--academic', action='store_true', help='arXiv 学术搜索')
    parser.add_argument('--s2', action='store_true', help='Semantic Scholar')
    parser.add_argument('--save', action='store_true', help='memory MCP 持久化')
    parser.add_argument('--deep-research', action='store_true',
                        help='复杂研究模式（max_subqueries=10）')
    parser.add_argument('--full-report', action='store_true',
                        help='完整报告模式（max_output_tokens=2000）')
    parser.add_argument('--dry-run', action='store_true',
                        help='仅打印 pipeline 计划，不执行')
    parser.add_argument('--legacy', action='store_true',
                        help='回退到旧 search.md 指令路径')
    args = parser.parse_args()

    if args.dry_run:
        orchestrator = SearchOrchestrator(args.query, args)
        plan = orchestrator.dry_run()
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return

    if args.legacy:
        print('Legacy mode: please use /search command via search.md', file=sys.stderr)
        sys.exit(1)

    # 实际执行（不注入 callbacks 时，所有 step 都会跳过 / fallback）
    orchestrator = SearchOrchestrator(args.query, args)
    orchestrator.run_all()
    result = orchestrator.result
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == '__main__':
    _main()
