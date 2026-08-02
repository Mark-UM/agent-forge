#!/usr/bin/env python3
"""流式结果输出 — 每层完成即返回结果（不等待全部完成）。

设计原则：
- 单文件独立模块，零外部依赖（stdlib concurrent.futures）
- 与 parallel.py 一致的 ThreadPoolExecutor 模式
- Generator pattern: yield 每层完成事件
- 错误层不影响其他层
- PII 脱敏由调用方在传入 mcp_callers 前完成（同 parallel.py）

事件类型：
- {'type': 'start', 'layer': <name>, 'timestamp': <iso>}
- {'type': 'result', 'layer': <name>, 'results': [...], 'elapsed': <s>}
- {'type': 'error', 'layer': <name>, 'error': <str>, 'elapsed': <s>}
- {'type': 'timeout', 'cancelled': [<layer>, ...]}
- {'type': 'done', 'total_layers': <int>, 'successful': <int>, 'failed': <int>}

Usage:
  python stream.py run \\
    --query "React useEffect" \\
    --layers duckduckgo,searxng \\
    --mock  # 测试模式

  python -m modules.search.tests.test_stream
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 导入 mock caller（复用 parallel.py 的）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from parallel import _mock_caller, _make_mock_caller, _MOCK_RESULTS, _MOCK_LATENCIES
except ImportError:
    _mock_caller = None
    _make_mock_caller = None
    _MOCK_RESULTS = {}
    _MOCK_LATENCIES = {}

# ── 常量 ────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 30
MAX_WORKERS = 5


def _now_iso():
    """返回当前 ISO 8601 时间戳。"""
    return datetime.now().isoformat(timespec='seconds')


def _timed_call(layer_name, caller, query_str):
    """带计时的调用 wrapper（与 parallel.py 一致）。"""
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


def stream_search(query, layers, mcp_callers, timeout=DEFAULT_TIMEOUT):
    """流式并行搜索，yield 每层完成事件。

    与 parallel_search 的区别：
    - parallel_search: 取最快或合并，返回单个结果
    - stream_search: yield 每个完成事件，调用方可实时处理

    Args:
        query: 搜索查询（应已做 PII 脱敏）
        layers: 层名列表
        mcp_callers: dict[layer_name, callable]
        timeout: 总超时秒数

    Yields:
        dict: 事件流（见模块 docstring）
    """
    # 边界输入校验
    if not isinstance(query, str) or not query.strip():
        yield {
            'type': 'error',
            'layer': '_',
            'error': 'query is empty',
            'timestamp': _now_iso(),
        }
        yield {
            'type': 'done',
            'total_layers': 0,
            'successful': 0,
            'failed': 1,
            'timestamp': _now_iso(),
        }
        return

    if not isinstance(layers, list) or not layers:
        yield {
            'type': 'error',
            'layer': '_',
            'error': 'layers is empty',
            'timestamp': _now_iso(),
        }
        yield {
            'type': 'done',
            'total_layers': 0,
            'successful': 0,
            'failed': 1,
            'timestamp': _now_iso(),
        }
        return

    if not isinstance(mcp_callers, dict) or not mcp_callers:
        yield {
            'type': 'error',
            'layer': '_',
            'error': 'mcp_callers is empty',
            'timestamp': _now_iso(),
        }
        yield {
            'type': 'done',
            'total_layers': 0,
            'successful': 0,
            'failed': 1,
            'timestamp': _now_iso(),
        }
        return

    # 过滤出有效 caller
    valid_layers = [l for l in layers if l in mcp_callers]
    if not valid_layers:
        yield {
            'type': 'error',
            'layer': '_',
            'error': 'no valid callers for specified layers',
            'timestamp': _now_iso(),
        }
        yield {
            'type': 'done',
            'total_layers': 0,
            'successful': 0,
            'failed': 1,
            'timestamp': _now_iso(),
        }
        return

    # emit start 事件（按 valid_layers 顺序）
    start_time = time.monotonic()
    for layer in valid_layers:
        yield {
            'type': 'start',
            'layer': layer,
            'timestamp': _now_iso(),
        }

    successful = 0
    failed = 0
    cancelled = []

    # 单层情况：直接同步调用
    if len(valid_layers) == 1:
        layer = valid_layers[0]
        outcome = _timed_call(layer, mcp_callers[layer], query)
        if outcome['success']:
            successful += 1
            yield {
                'type': 'result',
                'layer': layer,
                'results': outcome['results'],
                'elapsed': outcome['elapsed'],
                'timestamp': _now_iso(),
            }
        else:
            failed += 1
            yield {
                'type': 'error',
                'layer': layer,
                'error': outcome['error'],
                'elapsed': outcome['elapsed'],
                'timestamp': _now_iso(),
            }
    else:
        # 多层并行 + 流式 yield
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(valid_layers))) as executor:
            future_to_layer = {
                executor.submit(_timed_call, layer, mcp_callers[layer], query): layer
                for layer in valid_layers
            }

            try:
                for fut in as_completed(future_to_layer.keys(), timeout=timeout):
                    layer = future_to_layer[fut]
                    outcome = fut.result()
                    if outcome['success']:
                        successful += 1
                        yield {
                            'type': 'result',
                            'layer': layer,
                            'results': outcome['results'],
                            'elapsed': outcome['elapsed'],
                            'timestamp': _now_iso(),
                        }
                    else:
                        failed += 1
                        yield {
                            'type': 'error',
                            'layer': layer,
                            'error': outcome['error'],
                            'elapsed': outcome['elapsed'],
                            'timestamp': _now_iso(),
                        }
            except FuturesTimeoutError:
                # 超时，取消未完成的任务（已开始执行的线程无法取消）
                for fut in future_to_layer:
                    if not fut.done():
                        layer = future_to_layer[fut]
                        if fut.cancel():
                            cancelled.append(layer)
                            failed += 1
                            yield {
                                'type': 'error',
                                'layer': layer,
                                'error': 'timeout cancelled',
                                'elapsed': timeout,
                                'timestamp': _now_iso(),
                            }
                        else:
                            # 已开始执行，无法取消，标记为 timed out
                            cancelled.append(layer)
                            failed += 1
                            yield {
                                'type': 'error',
                                'layer': layer,
                                'error': 'timeout (task running, cannot cancel)',
                                'elapsed': timeout,
                                'timestamp': _now_iso(),
                            }

                # 总是 emit timeout 事件（即使 cancelled 已包含在 error 中）
                yield {
                    'type': 'timeout',
                    'cancelled': cancelled,
                    'timestamp': _now_iso(),
                }

    # emit done 事件
    total_elapsed = round(time.monotonic() - start_time, 3)
    yield {
        'type': 'done',
        'total_layers': len(valid_layers),
        'successful': successful,
        'failed': failed,
        'cancelled': cancelled,
        'total_elapsed': total_elapsed,
        'timestamp': _now_iso(),
    }


def collect_stream(query, layers, mcp_callers, timeout=DEFAULT_TIMEOUT):
    """收集流式事件为单一 dict（用于非流式场景）。

    Returns:
        dict: {
            'success': bool,
            'events': list[dict],
            'results_by_layer': dict[layer, list[dict]],
            'errors_by_layer': dict[layer, str],
            'cancelled': list[str],
            'total_elapsed': float,
        }
    """
    events = []
    results_by_layer = {}
    errors_by_layer = {}
    cancelled = []
    total_elapsed = 0
    any_success = False

    for event in stream_search(query, layers, mcp_callers, timeout=timeout):
        events.append(event)
        if event['type'] == 'result':
            results_by_layer[event['layer']] = event['results']
            any_success = True
        elif event['type'] == 'error':
            # 包括 layer='_' 的边界错误事件
            errors_by_layer[event.get('layer', '_')] = event.get('error', 'unknown')
        elif event['type'] == 'timeout':
            cancelled = event.get('cancelled', [])
        elif event['type'] == 'done':
            total_elapsed = event.get('total_elapsed', 0)

    return {
        'success': any_success,
        'events': events,
        'results_by_layer': results_by_layer,
        'errors_by_layer': errors_by_layer,
        'cancelled': cancelled,
        'total_elapsed': total_elapsed,
    }


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='流式搜索结果输出 — v4.2'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # run 子命令
    p_run = sub.add_parser('run', help='流式搜索（mock 模式）')
    p_run.add_argument('--query', required=True, help='搜索查询')
    p_run.add_argument('--layers', required=True,
                       help='层名逗号分隔')
    p_run.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    p_run.add_argument('--mock', action='store_true',
                       help='使用 mock caller（不调用真实 MCP）')
    p_run.add_argument('--json', action='store_true',
                       help='JSON Lines 格式输出（每行一个事件）')

    args = parser.parse_args()

    if args.command == 'run':
        layers = [l.strip() for l in args.layers.split(',') if l.strip()]

        if args.mock:
            if _make_mock_caller is None:
                print('错误: parallel.py 未找到，无法使用 mock 模式',
                      file=sys.stderr)
                return 1
            mcp_callers = {l: _make_mock_caller(l) for l in layers}
        else:
            print('错误: 真实 MCP 调用需要由调用方构造 mcp_callers。'
                  '请使用 --mock 进行演示，或通过 Python API 调用。',
                  file=sys.stderr)
            return 1

        # 流式输出
        for event in stream_search(args.query, layers, mcp_callers,
                                    timeout=args.timeout):
            if args.json:
                # JSON Lines 格式：每行一个事件
                print(json.dumps(event, ensure_ascii=False))
            else:
                # 人类可读格式
                ts = event.get('timestamp', '')
                etype = event['type']
                if etype == 'start':
                    print(f'[{ts}] Layer {event["layer"]} started...')
                elif etype == 'result':
                    print(f'[{ts}] Layer {event["layer"]} returned '
                          f'{len(event["results"])} results '
                          f'({event["elapsed"]}s)')
                elif etype == 'error':
                    print(f'[{ts}] Layer {event["layer"]} failed: '
                          f'{event["error"]} ({event["elapsed"]}s)')
                elif etype == 'timeout':
                    print(f'[{ts}] Timeout: cancelled {event["cancelled"]}')
                elif etype == 'done':
                    print(f'[{ts}] Done: '
                          f'{event["successful"]} successful, '
                          f'{event["failed"]} failed, '
                          f'total {event["total_elapsed"]}s')
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
