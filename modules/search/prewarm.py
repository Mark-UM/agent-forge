#!/usr/bin/env python3
"""缓存预热 — 从历史日志加载高频查询到缓存。

设计原则：
- 单文件独立模块，零外部依赖
- 从 _runtime/search/search_history.*.jsonl 加载历史查询
- 统计 Top N 高频查询
- 复用历史 top_results 作为缓存条目（不重新调用 MCP）
- 原子写入缓存（tmp + os.replace）
- PII 不再脱敏（历史日志已脱敏，详见 SKILL.md）

降级链：
1. 无历史日志 → 报告 0 查询
2. 缓存文件不存在 → 创建新缓存
3. 缓存已包含某查询 → 跳过
4. 历史条目无 top_results → 跳过（无法预热）
5. 写入失败 → 报告错误，不阻塞

Usage:
  python prewarm.py run --top 20
  python prewarm.py stats
  python -m modules.search.tests.test_prewarm
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime, timedelta
from collections import Counter

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_PROJECT_ROOT, '_runtime', 'search')
CACHE_FILE = os.path.join(_LOG_DIR, 'search_cache.json')

# ── 常量 ────────────────────────────────────────────────────────
DEFAULT_TOP_N = 20             # 默认预热 Top 20 查询
DEFAULT_DAYS_WINDOW = 30       # 默认统计最近 30 天的查询
CACHE_TTL = 7 * 24 * 3600     # 7 天 TTL（prewarm 写入的历史快照，比 search.py 的 24h 长）
CACHE_MAX_ENTRIES = 500       # 容量上限，与 search.py 一致
CACHE_KEEP_AFTER_TRIM = 400   # 超出上限时保留最新 400 条，与 search.py 一致


def _ensure_log_dir():
    """确保日志目录存在。"""
    if not os.path.exists(_LOG_DIR):
        try:
            os.makedirs(_LOG_DIR, exist_ok=True)
        except OSError as e:
            print(f"警告: 无法创建日志目录 {_LOG_DIR}: {e}", file=sys.stderr)


def _all_log_files():
    """列出所有历史日志文件，按日期倒序。"""
    if not os.path.exists(_LOG_DIR):
        return []
    files = []
    try:
        for name in os.listdir(_LOG_DIR):
            if name.startswith('search_history.') and name.endswith('.jsonl'):
                files.append(os.path.join(_LOG_DIR, name))
    except OSError:
        return []
    return sorted(files, reverse=True)


def _load_history_entries(files, days_limit=None):
    """从历史日志文件加载查询条目。

    Args:
        files: 日志文件列表
        days_limit: 仅加载最近 N 天的条目（None 表示全部）

    Returns:
        list[dict]: 历史条目列表（每个 dict 至少含 query, timestamp, top_results）
    """
    if days_limit is not None and days_limit > 0:
        cutoff = datetime.now() - timedelta(days=days_limit)
    else:
        cutoff = None

    entries = []
    for log_file in files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if not isinstance(entry, dict):
                            continue
                        # 时间过滤
                        if cutoff is not None:
                            ts_str = entry.get('timestamp', '')
                            try:
                                ts = datetime.fromisoformat(ts_str)
                                if ts < cutoff:
                                    continue
                            except (ValueError, TypeError):
                                pass  # 时间戳无效时保留（不做时间过滤）
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except (OSError, UnicodeDecodeError):
            continue
    return entries


def _extract_query_stats(entries):
    """统计查询频率 + 提取每个查询的最新有效结果。

    Args:
        entries: 历史条目列表

    Returns:
        dict: {
            query: {
                'count': int,
                'latest_entry': dict,  # 最新的有效条目（含 top_results）
                'first_seen': str,       # ISO 时间戳
                'last_seen': str,        # ISO 时间戳
            }
        }
    """
    stats = {}
    for entry in entries:
        query = entry.get('query', '').strip()
        if not query:
            continue

        # 跳过无 top_results 的条目（无法预热）
        top_results = entry.get('top_results', [])
        if not isinstance(top_results, list) or not top_results:
            # 仍计入频率，但不作为候选
            if query not in stats:
                stats[query] = {
                    'count': 0,
                    'latest_entry': None,
                    'first_seen': entry.get('timestamp', ''),
                    'last_seen': entry.get('timestamp', ''),
                }
            stats[query]['count'] += 1
            # 更新时间戳
            ts = entry.get('timestamp', '')
            if ts and ts > stats[query]['last_seen']:
                stats[query]['last_seen'] = ts
            if ts and (not stats[query]['first_seen'] or ts < stats[query]['first_seen']):
                stats[query]['first_seen'] = ts
            continue

        # 有 top_results 的条目
        if query not in stats:
            stats[query] = {
                'count': 0,
                'latest_entry': None,
                'first_seen': entry.get('timestamp', ''),
                'last_seen': entry.get('timestamp', ''),
            }
        stats[query]['count'] += 1

        # 更新最新条目（按 timestamp 比较）
        ts = entry.get('timestamp', '')
        if stats[query]['latest_entry'] is None or \
           ts > stats[query]['latest_entry'].get('timestamp', ''):
            stats[query]['latest_entry'] = entry

        # 更新时间戳
        if ts and ts > stats[query]['last_seen']:
            stats[query]['last_seen'] = ts
        if ts and (not stats[query]['first_seen'] or ts < stats[query]['first_seen']):
            stats[query]['first_seen'] = ts

    return stats


def _load_cache():
    """加载现有缓存。失败返回空 dict。"""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache_atomic(cache):
    """原子写入缓存文件（tmp + os.replace）。失败返回 False。"""
    _ensure_log_dir()
    tmp_file = CACHE_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, CACHE_FILE)
        return True
    except Exception as e:
        # OSError: 文件系统错误；TypeError/ValueError: JSON 序列化失败
        # 任意异常都不能阻塞主流程，且必须清理 tmp 文件
        print(f"警告: 缓存写入失败: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)
        except OSError:
            pass
        return False


def _cache_key(query, location, layer_hint=''):
    """生成缓存键（与 search.py 一致）。"""
    return f"{query.strip().lower()}|{location}|{layer_hint}"


def prewarm_cache(top_n=DEFAULT_TOP_N, days_window=DEFAULT_DAYS_WINDOW,
                   dry_run=False):
    """预热缓存：从历史日志加载高频查询到缓存。

    Args:
        top_n: 预热 Top N 高频查询
        days_window: 统计最近 N 天的查询（None=全部）
        dry_run: True 仅报告，不写入缓存

    Returns:
        dict: {
            'success': bool,
            'top_n': int,
            'days_window': int | None,
            'total_queries_seen': int,    # 历史中独立查询数
            'prewarm_candidates': int,    # 有 top_results 的查询数
            'prewarmed_count': int,        # 实际写入缓存的查询数
            'skipped_already_cached': int, # 已在缓存中跳过
            'skipped_no_results': int,    # 无 top_results 跳过
            'cache_total_after': int,     # 预热后缓存总条目数
            'top_queries': list[dict],    # Top N 查询详情
            'error': str,                  # 仅失败时存在
        }
    """
    # 加载历史日志
    files = _all_log_files()
    if not files:
        return {
            'success': True,  # 无日志不算失败
            'top_n': top_n,
            'days_window': days_window,
            'total_queries_seen': 0,
            'prewarm_candidates': 0,
            'prewarmed_count': 0,
            'skipped_already_cached': 0,
            'skipped_no_results': 0,
            'cache_total_after': 0,
            'top_queries': [],
        }

    entries = _load_history_entries(files, days_limit=days_window)
    if not entries:
        return {
            'success': True,
            'top_n': top_n,
            'days_window': days_window,
            'total_queries_seen': 0,
            'prewarm_candidates': 0,
            'prewarmed_count': 0,
            'skipped_already_cached': 0,
            'skipped_no_results': 0,
            'cache_total_after': 0,
            'top_queries': [],
        }

    # 统计查询频率
    stats = _extract_query_stats(entries)

    # 排序：按 count 降序，仅考虑有 latest_entry 的查询
    candidates = [
        (query, info) for query, info in stats.items()
        if info['latest_entry'] is not None
    ]
    candidates.sort(key=lambda x: x[1]['count'], reverse=True)

    # 取 Top N
    top_candidates = candidates[:top_n]

    # 加载现有缓存
    cache = _load_cache()
    cache_total_before = len(cache)

    prewarmed = 0
    skipped_cached = 0
    skipped_no_results = 0
    top_queries_detail = []

    for query, info in top_candidates:
        entry = info['latest_entry']
        location = entry.get('location', 'unknown')
        layer_hint = entry.get('layer_hint', '')
        key = _cache_key(query, location, layer_hint)

        if key in cache:
            skipped_cached += 1
            top_queries_detail.append({
                'query': query,
                'count': info['count'],
                'status': 'already_cached',
                'location': location,
            })
            continue

        top_results = entry.get('top_results', [])
        if not isinstance(top_results, list) or not top_results:
            skipped_no_results += 1
            top_queries_detail.append({
                'query': query,
                'count': info['count'],
                'status': 'no_results',
                'location': location,
            })
            continue

        # 构造缓存条目（与 search.py cache_store 一致）
        # S12 fix: 区分三个时间戳，避免 prewarm 给旧数据"洗白"TTL：
        # - retrieved_at: 原始条目的 fetch 时间（结果实际被抓取的时间）
        # - cached_at: 缓存条目写入时间（TTL 计算基准，保持 time.time() 不变）
        # - warmed_at: 标记本条目由 prewarm 写入（非实时搜索结果）
        # - cache_schema_version: 迁移追踪（v2 = S12 引入三时间戳）
        cache_entry = {
            'query': query,
            'layers_used': entry.get('layers_used', []),
            'results_count': entry.get('results_count', len(top_results)),
            'top_results': top_results[:10],
            'score': entry.get('score', 0),
            'cached_at': time.time(),  # 缓存写入时间（TTL 基准，语义正确）
            'retrieved_at': entry.get('timestamp', ''),  # S12: 原始 fetch 时间
            'warmed_at': time.time(),  # S12: 标记为 prewarm 写入
            'cache_schema_version': 2,  # S12: schema 迁移追踪
        }
        cache[key] = cache_entry
        prewarmed += 1
        top_queries_detail.append({
            'query': query,
            'count': info['count'],
            'status': 'prewarmed',
            'location': location,
            'results_count': cache_entry['results_count'],
        })

    # 容量限制：与 search.py 一致（500 条上限，超出时保留最新 400 条）
    if len(cache) > CACHE_MAX_ENTRIES:
        sorted_items = sorted(cache.items(),
                              key=lambda x: x[1].get('cached_at', 0))
        cache = dict(sorted_items[-CACHE_KEEP_AFTER_TRIM:])

    # 写入缓存（dry_run 模式跳过）
    if dry_run:
        return {
            'success': True,
            'top_n': top_n,
            'days_window': days_window,
            'total_queries_seen': len(stats),
            'prewarm_candidates': len(candidates),
            'prewarmed_count': prewarmed,
            'skipped_already_cached': skipped_cached,
            'skipped_no_results': skipped_no_results,
            'cache_total_after': cache_total_before,  # dry_run 不写入
            'top_queries': top_queries_detail,
            'dry_run': True,
        }

    write_ok = _save_cache_atomic(cache)
    if not write_ok:
        return {
            'success': False,
            'top_n': top_n,
            'days_window': days_window,
            'total_queries_seen': len(stats),
            'prewarm_candidates': len(candidates),
            'prewarmed_count': 0,
            'skipped_already_cached': skipped_cached,
            'skipped_no_results': skipped_no_results,
            'cache_total_after': cache_total_before,
            'top_queries': top_queries_detail,
            'error': 'cache write failed',
        }

    return {
        'success': True,
        'top_n': top_n,
        'days_window': days_window,
        'total_queries_seen': len(stats),
        'prewarm_candidates': len(candidates),
        'prewarmed_count': prewarmed,
        'skipped_already_cached': skipped_cached,
        'skipped_no_results': skipped_no_results,
        'cache_total_after': len(cache),
        'top_queries': top_queries_detail,
    }


def show_stats(days_window=DEFAULT_DAYS_WINDOW):
    """显示历史查询统计（不修改缓存）。"""
    files = _all_log_files()
    if not files:
        print(json.dumps({
            'success': True,
            'total_files': 0,
            'total_entries': 0,
            'unique_queries': 0,
            'top_queries': [],
        }, ensure_ascii=False, indent=2))
        return 0

    entries = _load_history_entries(files, days_limit=days_window)
    stats = _extract_query_stats(entries)

    # Top 10 查询
    sorted_stats = sorted(stats.items(), key=lambda x: x[1]['count'],
                           reverse=True)
    top = []
    for query, info in sorted_stats[:10]:
        top.append({
            'query': query,
            'count': info['count'],
            'first_seen': info['first_seen'],
            'last_seen': info['last_seen'],
            'has_results': info['latest_entry'] is not None,
        })

    cache = _load_cache()
    result = {
        'success': True,
        'total_files': len(files),
        'total_entries': len(entries),
        'unique_queries': len(stats),
        'days_window': days_window,
        'cache_entries': len(cache),
        'top_queries': top,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='缓存预热 — 从历史日志加载高频查询到缓存（v4.2 P2）'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # run 子命令
    p_run = sub.add_parser('run', help='执行缓存预热')
    p_run.add_argument('--top', type=int, default=DEFAULT_TOP_N,
                       help=f'预热 Top N 高频查询（默认 {DEFAULT_TOP_N}）')
    p_run.add_argument('--days', type=int, default=DEFAULT_DAYS_WINDOW,
                       help=f'统计最近 N 天的查询（默认 {DEFAULT_DAYS_WINDOW}，0=全部）')
    p_run.add_argument('--dry-run', action='store_true',
                       help='仅报告，不写入缓存')
    p_run.add_argument('--json', action='store_true',
                       help='JSON 格式输出（默认人类可读）')

    # stats 子命令
    p_stats = sub.add_parser('stats', help='显示历史查询统计')
    p_stats.add_argument('--days', type=int, default=DEFAULT_DAYS_WINDOW,
                          help=f'统计最近 N 天（默认 {DEFAULT_DAYS_WINDOW}）')

    args = parser.parse_args()

    if args.command == 'run':
        days_window = args.days if args.days > 0 else None
        result = prewarm_cache(
            top_n=args.top, days_window=days_window, dry_run=args.dry_run)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Prewarm {'(dry-run)' if args.dry_run else ''}:")
            print(f"  Top N: {result['top_n']}")
            print(f"  Days window: {result['days_window']}")
            print(f"  Total queries seen: {result['total_queries_seen']}")
            print(f"  Prewarm candidates: {result['prewarm_candidates']}")
            print(f"  Prewarmed: {result['prewarmed_count']}")
            print(f"  Skipped (already cached): {result['skipped_already_cached']}")
            print(f"  Skipped (no results): {result['skipped_no_results']}")
            print(f"  Cache total after: {result['cache_total_after']}")
            if result['top_queries']:
                print()
                print('Top queries:')
                for q in result['top_queries'][:10]:
                    print(f"  [{q['status']:15s}] ({q['count']:3d}x) "
                          f"{q['query']}")
        return 0 if result['success'] else 1

    if args.command == 'stats':
        return show_stats(days_window=args.days)

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
