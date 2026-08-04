#!/usr/bin/env python3
"""并行三层 MCP 调用 — 取最快返回的层结果。

设计原则：
- 单文件独立模块，零外部依赖（仅用 stdlib concurrent.futures）
- 与 summarize.py 一致的 ThreadPoolExecutor 模式（MCP 调用为 sync urllib）
- 失败优雅降级：单层失败不影响其他层
- 支持 'first_completed'（最快返回即用）和 'all'（等待全部合并）两种模式
- PII 脱敏由调用方在传入 mcp_callers 前完成（本模块不做脱敏，保持纯并发职责）

降级链：
1. 任一层成功 → 返回最快结果（first_completed）或合并结果（all）
2. 所有层失败 → 返回 winner=None, results=[]
3. 超时 → 取消未完成任务，返回已完成的结果（若有）

Usage:
  python parallel.py run \\
    --query "React useEffect" \\
    --layers duckduckgo,searxng \\
    --mock  # 测试模式，不调用真实 MCP

  python -m modules.search.tests.test_parallel
"""
import sys
import os
import json
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 常量 ────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30          # 默认总超时
MAX_WORKERS = 5               # 最多 5 个并发线程（三层足够）
SUPPORTED_MODES = ('first_completed', 'all')

# ── Mock caller（测试 / CLI 演示用） ────────────────────────────
_MOCK_RESULTS = {
    'duckduckgo': [
        {'title': 'DuckDuckGo Result 1', 'url': 'https://ddg.example.com/1',
         'snippet': 'DDG snippet 1', 'source': 'duckduckgo'},
        {'title': 'DuckDuckGo Result 2', 'url': 'https://ddg.example.com/2',
         'snippet': 'DDG snippet 2', 'source': 'duckduckgo'},
    ],
    'searxng': [
        {'title': 'SearXNG Result 1', 'url': 'https://searxng.example.com/1',
         'snippet': 'SearXNG snippet 1', 'source': 'searxng'},
    ],
    'g-search': [
        {'title': 'Google Result 1', 'url': 'https://google.example.com/1',
         'snippet': 'Google snippet 1', 'source': 'google'},
    ],
}

_MOCK_LATENCIES = {
    'duckduckgo': 0.1,   # 最快
    'searxng': 0.3,
    'g-search': 0.5,
}


def _mock_caller(layer, query):
    """Mock caller：模拟 MCP 调用（带延迟）。"""
    time.sleep(_MOCK_LATENCIES.get(layer, 0.2))
    if layer not in _MOCK_RESULTS:
        raise RuntimeError(f'Unknown layer: {layer}')
    return list(_MOCK_RESULTS[layer])  # 返回副本


def _make_mock_caller(layer):
    """构造 mock caller closure。"""
    def _caller(query):
        return _mock_caller(layer, query)
    return _caller


def _empty_result(mode, errors=None):
    """构造空结果。"""
    return {
        'success': False,
        'mode': mode,
        'winner': None,
        'results': [],
        'all_completed': False,
        'timings': {},
        'errors': errors or {},
        'cancelled': [],
        # S11: 仍在线程池中运行、未完成且未被 cancel 的任务（非强制终止，仅放弃等待）
        'abandoned': [],
    }


def _timed_call(layer_name, caller, query_str):
    """带计时的调用 wrapper。"""
    start = time.monotonic()
    try:
        results = caller(query_str)
        elapsed = round(time.monotonic() - start, 3)
        return {
            'layer': layer_name,
            'success': True,
            'results': results if isinstance(results, list) else [],
            'elapsed': elapsed,
            'error': None,
        }
    except Exception as e:
        elapsed = round(time.monotonic() - start, 3)
        return {
            'layer': layer_name,
            'success': False,
            'results': [],
            'elapsed': elapsed,
            'error': str(e)[:200],
        }


def parallel_search(query, layers, mcp_callers, mode='first_completed',
                    timeout=DEFAULT_TIMEOUT):
    """并行调用多个 MCP 层，返回最快或全部结果。

    Args:
        query: 搜索查询（应已做 PII 脱敏）
        layers: 层名列表，如 ['duckduckgo', 'searxng', 'g-search']
        mcp_callers: dict[layer_name, callable]，callable 签名 (query) -> list[dict]
            每个 callable 应是 sync 函数（urllib 或 subprocess 调用）
        mode: 'first_completed' 或 'all'
            - first_completed: 取第一个成功返回的层结果，取消其他
            - all: 等待所有层完成，合并结果
        timeout: 总超时秒数（<=0 表示无限等待）

    Returns:
        dict: {
            'success': bool,          # 是否至少有一层成功
            'mode': str,                # 'first_completed' | 'all'
            'winner': str | None,      # 首个成功的层名
            'results': list[dict],      # 结果列表
            'all_completed': bool,      # 所有层是否都完成（无超时/取消/放弃）
            'timings': dict[layer, float],  # 各层耗时（秒）
            'errors': dict[layer, str],     # 各层错误信息（失败时）
            'cancelled': list[str],     # 被 cancel 成功的层名列表
            'abandoned': list[str],     # S11: 仍在运行、未完成且无法 cancel 的层名
                                         # （线程未被强制终止，只是不再等待结果）
        }
    """
    # 参数校验
    if not isinstance(query, str) or not query.strip():
        return _empty_result(mode, errors={'_': 'query is empty'})

    if not isinstance(layers, list) or not layers:
        return _empty_result(mode, errors={'_': 'layers is empty'})

    if not isinstance(mcp_callers, dict) or not mcp_callers:
        return _empty_result(mode, errors={'_': 'mcp_callers is empty'})

    if mode not in SUPPORTED_MODES:
        return _empty_result(mode, errors={'_': f'unsupported mode: {mode}'})

    # 过滤出有效 caller
    valid_layers = [l for l in layers if l in mcp_callers]
    if not valid_layers:
        return _empty_result(mode,
                             errors={'_': 'no valid callers for specified layers'})

    timings = {}
    errors = {}
    cancelled = []
    results_by_layer = {}
    completed_order = []

    # 单层情况：直接同步调用，避免 ThreadPool 开销
    if len(valid_layers) == 1:
        layer = valid_layers[0]
        outcome = _timed_call(layer, mcp_callers[layer], query)
        timings[layer] = outcome['elapsed']
        if outcome['success']:
            return {
                'success': True,
                'mode': mode,
                'winner': layer,
                'results': outcome['results'],
                'all_completed': True,
                'timings': timings,
                'errors': {},
                'cancelled': [],
                'abandoned': [],
            }
        else:
            errors[layer] = outcome['error']
            return {
                'success': False,
                'mode': mode,
                'winner': None,
                'results': [],
                'all_completed': True,
                'timings': timings,
                'errors': errors,
                'cancelled': [],
                'abandoned': [],
            }

    # 多层并行
    # S11 fix: 不再用 `with ThreadPoolExecutor(...)` 上下文管理器。
    # 该模式在退出时会等待所有已提交任务完成，导致 timeout 不是硬截止。
    # 改为显式创建 executor，拿到 winner 或 timeout 后立即 shutdown(wait=False, cancel_futures=True)，
    # 仍在运行的任务标记为 'abandoned'（线程未被强制终止，只是不再等待）。
    executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(valid_layers)))
    future_to_layer = {
        executor.submit(_timed_call, layer, mcp_callers[layer], query): layer
        for layer in valid_layers
    }

    winner = None
    winner_results = []
    abandoned = []  # S11: 仍在运行、未被 cancel 成功的任务

    try:
        if mode == 'first_completed':
            try:
                for fut in as_completed(future_to_layer.keys(), timeout=timeout):
                    layer = future_to_layer[fut]
                    outcome = fut.result()
                    timings[layer] = outcome['elapsed']
                    completed_order.append(layer)

                    if outcome['success']:
                        results_by_layer[layer] = outcome['results']
                        if winner is None:
                            winner = layer
                            winner_results = outcome['results']
                            # 拿到 winner，不再等待其他任务
                            break
                    else:
                        errors[layer] = outcome['error']
            except FuturesTimeoutError:
                # 超时：取消所有未完成任务，无法 cancel 的标记为 abandoned
                for fut in future_to_layer:
                    if not fut.done():
                        layer = future_to_layer[fut]
                        if fut.cancel():
                            cancelled.append(layer)
                        else:
                            abandoned.append(layer)

            # S11: 拿到 winner 或 timeout 后，仍可能有任务在运行。
            # 标记这些为 abandoned（不等待完成，也不声称强制终止）。
            for fut in future_to_layer:
                layer = future_to_layer[fut]
                if not fut.done() and layer not in cancelled and layer not in abandoned:
                    if fut.cancel():
                        cancelled.append(layer)
                    else:
                        abandoned.append(layer)

            return {
                'success': winner is not None,
                'mode': mode,
                'winner': winner,
                'results': winner_results,
                'all_completed': len(cancelled) == 0 and not errors and not abandoned,
                'timings': timings,
                'errors': errors,
                'cancelled': cancelled,
                'abandoned': abandoned,
            }

        else:  # mode == 'all'
            try:
                for fut in as_completed(future_to_layer.keys(), timeout=timeout):
                    layer = future_to_layer[fut]
                    outcome = fut.result()
                    timings[layer] = outcome['elapsed']
                    completed_order.append(layer)
                    if outcome['success']:
                        results_by_layer[layer] = outcome['results']
                    else:
                        errors[layer] = outcome['error']
            except FuturesTimeoutError:
                for fut in future_to_layer:
                    if not fut.done():
                        layer = future_to_layer[fut]
                        if fut.cancel():
                            cancelled.append(layer)
                        else:
                            abandoned.append(layer)

            # 合并结果（按 valid_layers 顺序，去重由调用方处理）
            merged = []
            for layer in valid_layers:
                if layer in results_by_layer:
                    merged.extend(results_by_layer[layer])

            return {
                'success': bool(results_by_layer),
                'mode': mode,
                'winner': completed_order[-1] if completed_order else None,
                'results': merged,
                'all_completed': len(cancelled) == 0 and not abandoned,
                'timings': timings,
                'errors': errors,
                'cancelled': cancelled,
                'abandoned': abandoned,
            }
    finally:
        # S11: 立即放弃等待仍运行的任务（非强制终止线程，只是不再阻塞）。
        # cancel_futures=True 尝试 cancel 所有 pending futures；已运行的会继续到完成但被忽略。
        executor.shutdown(wait=False, cancel_futures=True)


# ── CLI 接口 ────────────────────────────────────────────────────
def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='并行三层 MCP 调用 — 性能优化 v4.2'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # run 子命令
    p_run = sub.add_parser('run', help='并行调用（mock 模式用于演示）')
    p_run.add_argument('--query', required=True, help='搜索查询')
    p_run.add_argument('--layers', required=True,
                       help='层名逗号分隔（duckduckgo,searxng,g-search）')
    p_run.add_argument('--mode', choices=SUPPORTED_MODES,
                       default='first_completed')
    p_run.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_run.add_argument('--mock', action='store_true',
                       help='使用 mock caller（不调用真实 MCP）')
    p_run.add_argument('--json', action='store_true',
                       help='JSON 格式输出')

    # supported-layers 子命令
    sub.add_parser('supported-layers', help='列出支持的层名（mock）')

    args = parser.parse_args()

    if args.command == 'supported-layers':
        print('Supported layers (mock):')
        for layer in _MOCK_RESULTS:
            print(f'  - {layer} (latency: {_MOCK_LATENCIES.get(layer, 0.2)}s)')
        return 0

    if args.command == 'run':
        layers = [l.strip() for l in args.layers.split(',') if l.strip()]

        if args.mock:
            mcp_callers = {l: _make_mock_caller(l) for l in layers}
        else:
            print('错误: 真实 MCP 调用需要由调用方构造 mcp_callers。'
                  '请使用 --mock 进行演示，或通过 Python API 调用。',
                  file=sys.stderr)
            return 1

        result = parallel_search(
            args.query, layers, mcp_callers,
            mode=args.mode, timeout=args.timeout)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Mode: {result['mode']}")
            print(f"Success: {result['success']}")
            print(f"Winner: {result['winner']}")
            print(f"Results count: {len(result['results'])}")
            print(f"All completed: {result['all_completed']}")
            print()
            print('Timings:')
            for layer, t in result['timings'].items():
                print(f'  {layer}: {t}s')
            if result['errors']:
                print('Errors:')
                for layer, e in result['errors'].items():
                    print(f'  {layer}: {e}')
            if result['cancelled']:
                print(f'Cancelled: {result["cancelled"]}')
            if result['results']:
                print()
                print('Results:')
                for i, r in enumerate(result['results'][:5], 1):
                    print(f'  {i}. {r.get("title", "?")}')
                    print(f'     {r.get("url", "?")}')
        return 0 if result['success'] else 1

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
