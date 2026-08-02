#!/usr/bin/env python3
"""搜索统一模块 — 三层搜索编排、历史日志、缓存、PII 脱敏、健康检查。

设计原则：
- 单文件聚合所有核心功能，避免模块膨胀
- 零外部依赖（仅 Python 标准库）
- 所有 IO 异常兜底，绝不阻塞主流程
- 隐私优先：PII 出境前脱敏

Subcommands:
  log            记录一次搜索
  recent         显示最近 N 条搜索
  stats          显示统计信息
  find           按关键词查找历史
  save           标记某条记录为收藏
  cache-get      查询缓存
  cache-clean    清理过期缓存
  health         MCP 健康检查

Files:
  _runtime/search/search_history.YYYY-MM-DD.jsonl   按日切分日志
  _runtime/search/search_cache.json                  查询缓存（24h TTL）
"""
import sys
import os
import json
import re
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_PROJECT_ROOT, '_runtime', 'search')
CACHE_FILE = os.path.join(_LOG_DIR, 'search_cache.json')

# 支持的层名（serper 是 v4.2 P2 引入的 g-search 替代；g-search 保留以兼容旧脚本）
VALID_LAYERS = {'duckduckgo', 'searxng', 'g-search', 'serper'}

# 缓存 TTL（秒）
CACHE_TTL = 24 * 3600  # 24 小时

# MCP 健康检查端点（仅 remote 类型可 ping；local 类型通过启动判断）
MCP_HEALTH_URLS = {
    'context7': 'https://mcp.context7.com/sse',
}

# ── PII 脱敏正则 ────────────────────────────────────────────────
_PII_PATTERNS = [
    # 邮箱
    (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '[REDACTED-EMAIL]'),
    # 中国手机号（11 位，1 开头）
    (re.compile(r'\b1[3-9]\d{9}\b'), '[REDACTED-PHONE]'),
    # 身份证号（18 位，最后一位可能是 X）
    (re.compile(r'\b\d{17}[\dXx]\b'), '[REDACTED-ID]'),
    # 马来西亚手机号（+60 后 9-10 位）
    (re.compile(r'\+60\d{8,10}'), '[REDACTED-MY-PHONE]'),
]


def _redact_pii(text):
    """对字符串做 PII 脱敏。"""
    if not isinstance(text, str):
        return text
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _normalize_url(url):
    """URL 归一化：去除 query string 和 fragment，用于跨层去重。"""
    if not isinstance(url, str) or not url:
        return ''
    try:
        parsed = urlparse(url)
        # 仅保留 scheme + netloc + path
        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        return normalized.lower().rstrip('/')
    except Exception:
        return url


def _ensure_log_dir():
    """确保日志目录存在。"""
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except OSError:
        pass


def _today_log_file():
    """返回当天的日志文件路径（按日切分）。"""
    today = datetime.now().strftime('%Y-%m-%d')
    return os.path.join(_LOG_DIR, f'search_history.{today}.jsonl')


def _all_log_files():
    """返回所有历史日志文件，按日期升序。"""
    if not os.path.exists(_LOG_DIR):
        return []
    files = []
    try:
        for name in os.listdir(_LOG_DIR):
            if name.startswith('search_history.') and name.endswith('.jsonl'):
                files.append(os.path.join(_LOG_DIR, name))
    except OSError:
        pass
    return sorted(files)


def _parse_layers(layers_str):
    """解析层名字符串，验证合法性。"""
    if not layers_str:
        return []
    layers = [layer.strip() for layer in layers_str.split(',') if layer.strip()]
    invalid = [layer for layer in layers if layer not in VALID_LAYERS]
    if invalid:
        print(f"警告: 未识别的层名 {invalid}（合法值: {sorted(VALID_LAYERS)}）", file=sys.stderr)
    return layers


def _parse_bool(value, default=False):
    """解析布尔值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'y', 't')


def _parse_top_results(json_str):
    """解析 top_results JSON 字符串，自动做 PII 脱敏和 URL 归一化。"""
    if not json_str:
        return []
    try:
        results = json.loads(json_str)
        if not isinstance(results, list):
            raise ValueError("top-results 必须是 JSON 数组")
        # 截断到前 10 条 + PII 脱敏
        cleaned = []
        for item in results[:10]:
            if not isinstance(item, dict):
                continue
            cleaned.append({
                'title': _redact_pii(str(item.get('title', ''))),
                'url': item.get('url', ''),
                'snippet': _redact_pii(str(item.get('snippet', ''))),
                'source': item.get('source', 'unknown'),
            })
        return cleaned
    except (json.JSONDecodeError, ValueError) as e:
        print(f"警告: top-results 解析失败: {e}", file=sys.stderr)
        return []


# ── 缓存层 ──────────────────────────────────────────────────────
def _load_cache():
    """加载缓存文件。失败返回空 dict。"""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache):
    """保存缓存到文件。失败不抛异常。

    B2 fix: 使用 atomic write（write to temp + os.replace）防止
    写入中途崩溃导致 cache 文件被截断 / 损坏。
    """
    _ensure_log_dir()
    tmp_path = CACHE_FILE + '.tmp'
    try:
        # 1. 先写到临时文件
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        # 2. 原子替换（os.replace 在 Windows/POSIX 均为原子操作）
        os.replace(tmp_path, CACHE_FILE)
    except OSError as e:
        # 清理可能的残留临时文件
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        print(f"警告: 缓存写入失败: {e}", file=sys.stderr)


def _cache_key(query, location, layer_hint=''):
    """生成缓存键。"""
    key = f"{query.strip().lower()}|{location}|{layer_hint}"
    return key


def cache_get(args):
    """查询缓存。命中返回 JSON 字符串，未命中返回空字符串。"""
    cache = _load_cache()
    key = _cache_key(args.query, args.location or 'unknown', args.layer_hint or '')
    entry = cache.get(key)
    if not entry:
        return 0  # 未命中

    # 检查 TTL
    cached_at = entry.get('cached_at', 0)
    if time.time() - cached_at > CACHE_TTL:
        # 过期，从缓存中删除
        cache.pop(key, None)
        _save_cache(cache)
        return 0

    # 命中，输出缓存内容
    print(json.dumps({
        'hit': True,
        'query': entry.get('query', ''),
        'layers_used': entry.get('layers_used', []),
        'results_count': entry.get('results_count', 0),
        'top_results': entry.get('top_results', [])[:10],
        'score': entry.get('score', 0),
        'cached_at': datetime.fromtimestamp(cached_at).isoformat(),
    }, ensure_ascii=False, indent=2))
    return 0


def cache_clean(args):
    """清理过期缓存条目。"""
    cache = _load_cache()
    if not cache:
        print("缓存为空，无需清理")
        return 0

    now = time.time()
    expired_keys = [k for k, v in cache.items() if now - v.get('cached_at', 0) > CACHE_TTL]
    for key in expired_keys:
        cache.pop(key, None)

    _save_cache(cache)
    print(f"已清理 {len(expired_keys)} 条过期缓存，剩余 {len(cache)} 条")
    return 0


def cache_store(query, location, layer_hint, layers_used, results_count, top_results, score):
    """写入缓存（内部调用）。"""
    cache = _load_cache()
    key = _cache_key(query, location, layer_hint)
    cache[key] = {
        'query': query,
        'layers_used': layers_used,
        'results_count': results_count,
        'top_results': top_results[:10],
        'score': score,
        'cached_at': time.time(),
    }
    # 简单容量限制：超过 500 条删除最早的
    if len(cache) > 500:
        sorted_items = sorted(cache.items(), key=lambda x: x[1].get('cached_at', 0))
        cache = dict(sorted_items[-400:])
    _save_cache(cache)


# ── 日志层 ──────────────────────────────────────────────────────
def log_search(args):
    """记录一次搜索到日志文件。"""
    # 准备 top_results，自动 URL 去重
    top_results = _parse_top_results(args.top_results)
    seen_urls = set()
    deduped_results = []
    for item in top_results:
        norm_url = _normalize_url(item.get('url', ''))
        if norm_url and norm_url in seen_urls:
            continue
        if norm_url:
            seen_urls.add(norm_url)
        deduped_results.append(item)

    entry = {
        'query': _redact_pii(args.query or ''),
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'location': args.location or 'unknown',
        'layers_used': _parse_layers(args.layers),
        'results_count': max(0, args.results_count or 0),
        'score': max(0.0, min(10.0, float(args.score or 0))),
        'satisfied': _parse_bool(args.satisfied),
        'fallback_triggered': len(_parse_layers(args.layers)) > 1,
        'top_results': deduped_results,
        'saved': _parse_bool(args.saved),
    }

    # MindSearch spec §8.3 字段（仅当触发 --deep/--aggregate 时才记录）
    if _parse_bool(args.deep_search):
        entry['deep_search'] = True
        # planner_success: 空字符串 → None, 否则解析为 bool
        if args.planner_success:
            entry['planner_success'] = _parse_bool(args.planner_success)
        if args.degradation:
            entry['degradation'] = args.degradation[:100]  # 截断防超长
        entry['sub_queries_planned'] = max(0, args.sub_queries_planned or 0)
        entry['sub_queries_succeeded'] = max(0, args.sub_queries_succeeded or 0)
        entry['sub_queries_failed'] = max(0, args.sub_queries_failed or 0)

    if _parse_bool(args.aggregated):
        entry['aggregated'] = True
        # 如果 aggregated 但未设置 deep_search 字段，至少标记 aggregated=true
        if 'deep_search' not in entry:
            entry['aggregated'] = True  # already set above, but be explicit

    if not entry['query']:
        print("错误: --query 不能为空", file=sys.stderr)
        return 1

    _ensure_log_dir()
    log_file = _today_log_file()
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except OSError as e:
        print(f"错误: 写入日志失败: {e}", file=sys.stderr)
        return 1

    # 同步写入缓存（使用脱敏后的 query 避免缓存文件泄露 PII）
    try:
        cache_store(
            query=entry['query'],  # 使用 _redact_pii 处理后的 query
            location=args.location or 'unknown',
            layer_hint=args.layer_hint or '',
            layers_used=entry['layers_used'],
            results_count=entry['results_count'],
            top_results=deduped_results,
            score=entry['score'],
        )
    except Exception as e:
        print(f'警告: 缓存写入失败: {e}', file=sys.stderr)

    print(f"已记录搜索: query='{entry['query']}', layers={entry['layers_used']}, "
          f"score={entry['score']}, satisfied={entry['satisfied']}, saved={entry['saved']}")
    return 0


def _load_entries_from_files(files, days_limit=None):
    """从多个日志文件加载条目，可按天数过滤。"""
    if not files:
        return []

    cutoff = None
    if days_limit is not None:
        cutoff = datetime.now() - timedelta(days=days_limit)

    entries = []
    for log_file in files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        # 时间过滤
                        if cutoff is not None:
                            ts_str = entry.get('timestamp', '')
                            try:
                                ts = datetime.fromisoformat(ts_str)
                                if ts < cutoff:
                                    continue
                            except (ValueError, TypeError):
                                pass  # 时间戳无效时不过滤
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue  # 跳过无效行，不崩溃
        except OSError:
            continue  # 文件读取失败跳过，不崩溃
    return entries


# ── v4.0 时效性与语言过滤辅助函数 ─────────────────────────────
_YEAR_RE = re.compile(r'(20\d{2})')
_VALID_YEAR_MIN = 2010
# 当前年份动态获取，避免硬编码 2026 在 2027+ 失效
_VALID_YEAR_MAX = datetime.now().year

# 中文字符范围（含简繁体）
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')


def _extract_year(url, snippet):
    """从 URL 或 snippet 提取最新有效年份。

    用于 filter --recent 判断结果时效性。

    Args:
        url: 结果 URL
        snippet: 结果摘要

    Returns:
        int or None: 最新有效年份（2010-2026），无匹配返回 None
    """
    text = f"{url or ''} {snippet or ''}"
    years = []
    for m in _YEAR_RE.findall(text):
        try:
            y = int(m)
            if _VALID_YEAR_MIN <= y <= _VALID_YEAR_MAX:
                years.append(y)
        except ValueError:
            continue
    return max(years) if years else None


def _detect_lang(text):
    """检测文本语言。

    算法：中文字符占比 > 30% 判定为 zh，否则 en。
    用于 filter --lang 过滤。

    Args:
        text: 待检测文本

    Returns:
        str: 'zh' | 'en' | 'unknown'
    """
    if not isinstance(text, str) or not text:
        return 'unknown'

    try:
        cjk_count = len(_CJK_RE.findall(text))
        # 仅统计字母和 CJK 字符为有效字符
        alpha_count = sum(1 for c in text if c.isalpha())
        if alpha_count == 0:
            return 'unknown'
        ratio = cjk_count / alpha_count
        return 'zh' if ratio > 0.3 else 'en'
    except Exception:
        return 'unknown'


def _filter_results(results, recent_days=None, lang=None):
    """过滤搜索结果（按时效性 + 语言）。

    Args:
        results: 搜索结果列表 [{'url', 'snippet', 'title', ...}, ...]
        recent_days: 仅保留最近 N 天内的结果（按年份近似）
                     None = 不限
                     int N = 仅保留 year >= (current_year - N)
        lang: 'zh' | 'en' | 'all'，None 视为 'all'

    Returns:
        list: 过滤后的结果
    """
    if not isinstance(results, list):
        return []

    if not results:
        return []

    # 即使无过滤条件也需剔除非 dict 元素，保证返回值一致性
    if recent_days is None and (lang is None or lang == 'all'):
        return [r for r in results if isinstance(r, dict)]

    current_year = _VALID_YEAR_MAX  # 2026
    year_threshold = (current_year - recent_days) if recent_days is not None else None
    lang_filter = lang if lang and lang != 'all' else None

    filtered = []
    for r in results:
        if not isinstance(r, dict):
            continue

        # 时效性过滤
        if year_threshold is not None:
            url = str(r.get('url', ''))
            snippet = str(r.get('snippet', ''))
            year = _extract_year(url, snippet)
            # 无年份信息的结果默认保留（避免误删）
            if year is not None and year < year_threshold:
                continue

        # 语言过滤
        if lang_filter is not None:
            # 拼接 title + snippet 做语言检测
            title = str(r.get('title', ''))
            snippet = str(r.get('snippet', ''))
            text = f"{title} {snippet}"
            detected = _detect_lang(text)
            # 无明确语言判定时保留（避免误删）
            if detected != 'unknown' and detected != lang_filter:
                continue

        filtered.append(r)

    return filtered


def show_recent(args):
    """显示最近 N 条搜索记录。"""
    files = _all_log_files()
    if not files:
        print("暂无搜索记录")
        return 0

    days = args.days if hasattr(args, 'days') and args.days else None
    entries = _load_entries_from_files(files, days_limit=days)
    if not entries:
        print("暂无搜索记录")
        return 0

    limit = max(1, min(100, args.limit or 20))
    recent = entries[-limit:]
    recent.reverse()

    print(f"## 最近 {len(recent)} 条搜索记录（共 {len(entries)} 条）\n")
    for i, entry in enumerate(recent, 1):
        saved_mark = " ★" if entry.get('saved') else ""
        print(f"{i}. [{entry.get('timestamp', '?')}] {entry.get('query', '?')}{saved_mark}")
        print(f"   layers: {entry.get('layers_used', [])}")
        print(f"   results: {entry.get('results_count', 0)}, "
              f"score: {entry.get('score', 0)}/10, "
              f"satisfied: {entry.get('satisfied', False)}")
    return 0


def show_stats(args):
    """显示搜索统计信息，支持时间窗口。"""
    files = _all_log_files()
    if not files:
        print("暂无搜索记录")
        return 0

    days = args.days if hasattr(args, 'days') and args.days else None
    entries = _load_entries_from_files(files, days_limit=days)
    if not entries:
        print("暂无搜索记录")
        return 0

    total = len(entries)
    layer_counts = {}
    satisfied_count = 0
    score_sum = 0.0
    fallback_count = 0
    saved_count = 0

    for entry in entries:
        for layer in entry.get('layers_used', []):
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
        if entry.get('satisfied'):
            satisfied_count += 1
        score_sum += float(entry.get('score', 0))
        if entry.get('fallback_triggered'):
            fallback_count += 1
        if entry.get('saved'):
            saved_count += 1

    avg_score = score_sum / total if total else 0
    satisfaction_rate = (satisfied_count / total * 100) if total else 0
    fallback_rate = (fallback_count / total * 100) if total else 0
    saved_rate = (saved_count / total * 100) if total else 0

    window_desc = f"（最近 {days} 天）" if days else "（全部历史）"
    print(f"## 搜索统计{window_desc}\n")
    print(f"总搜索次数: {total}")
    print(f"平均质量分: {avg_score:.2f}/10")
    print(f"满意度: {satisfied_count}/{total} ({satisfaction_rate:.1f}%)")
    print(f"回退触发率: {fallback_count}/{total} ({fallback_rate:.1f}%)")
    print(f"收藏数: {saved_count}/{total} ({saved_rate:.1f}%)")
    print(f"\n各层使用次数:")
    for layer in sorted(VALID_LAYERS):
        count = layer_counts.get(layer, 0)
        pct = (count / total * 100) if total else 0
        print(f"  {layer}: {count} ({pct:.1f}%)")
    return 0


def find_searches(args):
    """按关键词查找历史搜索，支持时间窗口和收藏过滤。"""
    files = _all_log_files()
    if not files:
        print("暂无搜索记录")
        return 0

    days = args.days if hasattr(args, 'days') and args.days else None
    entries = _load_entries_from_files(files, days_limit=days)
    if not entries:
        print("暂无搜索记录")
        return 0

    keyword = (args.query or '').lower()
    matches = [e for e in entries if keyword in e.get('query', '').lower()]

    if args.saved_only:
        matches = [e for e in matches if e.get('saved')]

    if not matches:
        print(f"未找到匹配 '{keyword}' 的搜索记录")
        return 0

    print(f"## 找到 {len(matches)} 条匹配 '{keyword}' 的记录\n")
    for i, entry in enumerate(matches, 1):
        saved_mark = " ★" if entry.get('saved') else ""
        print(f"{i}. [{entry.get('timestamp', '?')}] {entry.get('query', '?')}{saved_mark}")
        print(f"   layers: {entry.get('layers_used', [])}, "
              f"score: {entry.get('score', 0)}/10")
    return 0


def save_search(args):
    """将最近一条匹配的记录标记为收藏。"""
    files = _all_log_files()
    if not files:
        print("暂无搜索记录")
        return 0

    entries = _load_entries_from_files(files)
    if not entries:
        print("暂无搜索记录")
        return 0

    keyword = (args.query or '').lower()
    # 找最近一条匹配且未收藏的
    target_idx = None
    for i in range(len(entries) - 1, -1, -1):
        entry = entries[i]
        if keyword and keyword not in entry.get('query', '').lower():
            continue
        if not entry.get('saved'):
            target_idx = i
            break

    if target_idx is None:
        print(f"未找到可收藏的匹配记录（可能已收藏）")
        return 0

    # 需要找到对应的日志文件并修改该行
    # 使用原子写：先写临时文件，再 os.replace 覆盖，避免中断导致数据丢失
    target_entry = entries[target_idx]
    target_ts = target_entry.get('timestamp')

    # 找到该条目所在的文件
    for log_file in files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            modified_lines = []
            modified = False
            for line in lines:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                try:
                    entry = json.loads(line_stripped)
                    if (entry.get('timestamp') == target_ts
                            and keyword.lower() in entry.get('query', '').lower()):
                        entry['saved'] = True
                        modified_lines.append(
                            json.dumps(entry, ensure_ascii=False) + '\n')
                        modified = True
                    else:
                        modified_lines.append(line)
                except json.JSONDecodeError:
                    modified_lines.append(line)

            if modified:
                # 原子写：临时文件 + os.replace
                tmp_file = log_file + '.tmp'
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    f.writelines(modified_lines)
                os.replace(tmp_file, log_file)
                print(f"已收藏: {target_entry.get('query', '?')}")
                return 0
        except OSError as e:
            print(f"警告: 修改文件 {log_file} 失败: {e}", file=sys.stderr)
            continue

    print("未找到可收藏的记录")
    return 1


# ── v4.0 过滤子命令 ─────────────────────────────────────────────
def filter_history_results(args):
    """对历史搜索的 top_results 做时效性 + 语言过滤。

    数据源：从 _runtime/search/search_history.YYYY-MM-DD.jsonl 加载条目，
    对每条记录的 top_results 字段应用 _filter_results。

    输出：每条记录打印过滤前后的结果数 + 过滤后的结果列表。
    """
    files = _all_log_files()
    if not files:
        print("暂无搜索记录")
        return 0

    days = args.days if hasattr(args, 'days') and args.days else None
    entries = _load_entries_from_files(files, days_limit=days)
    if not entries:
        print("暂无搜索记录")
        return 0

    # 关键词过滤
    query_kw = (args.query or '').lower()
    if query_kw:
        entries = [e for e in entries if query_kw in e.get('query', '').lower()]
        if not entries:
            print(f"未找到匹配 '{args.query}' 的历史记录")
            return 0

    # 解析 --recent 参数
    recent_days = None
    if args.recent is not None:
        try:
            recent_days = int(args.recent)
            if recent_days < 0:
                print("错误: --recent 不能为负数", file=sys.stderr)
                return 1
        except ValueError:
            print(f"错误: --recent 必须是整数（收到 '{args.recent}'）",
                  file=sys.stderr)
            return 1

    lang = args.lang or 'all'
    limit = max(1, min(500, args.limit or 50))

    print(f"## 过滤历史搜索结果（{len(entries)} 条记录）\n")
    print(f"   过滤条件: --recent={recent_days}, --lang={lang}\n")

    total_before = 0
    total_after = 0

    for entry in entries:
        top_results = entry.get('top_results', [])
        if not isinstance(top_results, list):
            top_results = []

        before = len(top_results)
        filtered = _filter_results(top_results,
                                   recent_days=recent_days,
                                   lang=lang)
        filtered = filtered[:limit]
        after = len(filtered)

        total_before += before
        total_after += after

        saved_mark = " ★" if entry.get('saved') else ""
        print(f"- [{entry.get('timestamp', '?')}] "
              f"{entry.get('query', '?')}{saved_mark}")
        print(f"  过滤前: {before} 条 → 过滤后: {after} 条")

        for i, r in enumerate(filtered, 1):
            title = str(r.get('title', ''))[:80]
            url = str(r.get('url', ''))
            snippet = str(r.get('snippet', ''))[:120]

            # 提取年份用于显示
            year = _extract_year(url, snippet)
            year_str = f" ({year})" if year else ""

            # 语言检测
            text = f"{title} {snippet}"
            lang_str = _detect_lang(text)

            print(f"  {i}. {title}{year_str} [{lang_str}]")
            print(f"     {url}")
            if snippet:
                print(f"     {snippet}")
        print()  # 空行分隔

    print(f"## 汇总: {total_before} 条 → {total_after} 条 "
          f"(保留率 {total_after / total_before * 100:.1f}%)"
          if total_before > 0 else "## 汇总: 无结果可过滤")
    return 0


# ── 健康检查层 ──────────────────────────────────────────────────
def health_check(args):
    """快速 MCP 健康检查（仅 remote 类型可 ping）。"""
    print("## MCP 健康检查\n")

    # remote MCP 检查
    for name, url in MCP_HEALTH_URLS.items():
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                print(f"  {name}: ✓ 可用 (HTTP {status})")
        except urllib.error.HTTPError as e:
            print(f"  {name}: ✗ HTTP 错误 ({e.code})")
        except urllib.error.URLError as e:
            print(f"  {name}: ✗ 无法连接 ({e.reason})")
        except Exception as e:
            print(f"  {name}: ✗ 异常 ({type(e).__name__}: {e})")

    # local MCP 仅检查命令是否在 PATH 中
    local_mcps = {
        'duckduckgo': ('C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe', ['-m', 'duckduckgo_mcp_server', '--help']),
        'git': ('C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe', ['-m', 'mcp_server_git', '--help']),
    }

    for name, (cmd, flags) in local_mcps.items():
        try:
            import subprocess
            result = subprocess.run([cmd] + flags, capture_output=True, timeout=3)
            if result.returncode == 0 or result.stderr:
                # --help 通常返回非 0 但有 stderr 输出，说明模块存在
                print(f"  {name}: ✓ 模块可加载")
            else:
                print(f"  {name}: ? 返回码 {result.returncode}")
        except subprocess.TimeoutExpired:
            print(f"  {name}: ? 响应超时（可能正常等待 stdio）")
        except FileNotFoundError:
            print(f"  {name}: ✗ Python 解释器不存在")
        except Exception as e:
            print(f"  {name}: ✗ 异常 ({type(e).__name__})")

    # npx-based MCPs: 检查 npx 是否可用
    try:
        import subprocess
        result = subprocess.run(['npx', '--version'], capture_output=True, timeout=5)
        if result.returncode == 0:
            print(f"  npx (用于 filesystem/github/playwright/fetch/sqlite/time/searxng): ✓ 可用")
        else:
            print(f"  npx: ✗ 返回码 {result.returncode}")
    except FileNotFoundError:
        print(f"  npx: ✗ 未安装")
    except Exception as e:
        print(f"  npx: ✗ 异常 ({type(e).__name__})")

    return 0


# ── 主入口 ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='搜索统一模块 — 三层搜索编排、历史日志、缓存、PII 脱敏、健康检查',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # log 子命令
    p_log = sub.add_parser('log', help='记录一次搜索')
    p_log.add_argument('--query', required=True, help='搜索查询（将做 PII 脱敏）')
    p_log.add_argument('--layers', default='', help='使用的层名，逗号分隔')
    p_log.add_argument('--results-count', type=int, default=0, help='结果数量')
    p_log.add_argument('--score', type=float, default=0.0, help='质量评分 0-10')
    p_log.add_argument('--satisfied', default='false', help='是否满足需求')
    p_log.add_argument('--location', default='unknown', help='用户当前位置')
    p_log.add_argument('--top-results', default='', help='Top 结果 JSON 数组（将自动 PII 脱敏 + URL 去重）')
    p_log.add_argument('--saved', default='false', help='是否标记为收藏')
    p_log.add_argument('--layer-hint', default='', help='缓存键提示（如显式 --layer 参数）')
    # MindSearch spec §8.3 字段：deep_search / aggregated / planner_success / degradation / sub_queries_*
    p_log.add_argument('--deep-search', default='false',
                       help='是否触发 --deep Planner 分解 (spec §8.3)')
    p_log.add_argument('--aggregated', default='false',
                       help='是否触发 --aggregate Aggregator 聚合 (spec §8.3)')
    p_log.add_argument('--planner-success', default='',
                       help='Planner 是否成功 (true/false；空表示非 deep_search)')
    p_log.add_argument('--degradation', default='',
                       help='降级原因 (如 planner_timeout/aggregator_failed；空表示未降级)')
    p_log.add_argument('--sub-queries-planned', type=int, default=0,
                       help='Planner 规划的子查询数 (spec §8.3)')
    p_log.add_argument('--sub-queries-succeeded', type=int, default=0,
                       help='成功执行的子查询数 (spec §8.3)')
    p_log.add_argument('--sub-queries-failed', type=int, default=0,
                       help='失败的子查询数 (spec §8.3)')

    # recent 子命令
    p_recent = sub.add_parser('recent', help='显示最近的搜索记录')
    p_recent.add_argument('--limit', type=int, default=20, help='显示条数（最大 100）')
    p_recent.add_argument('--days', type=int, default=None, help='仅显示最近 N 天的记录')

    # stats 子命令
    p_stats = sub.add_parser('stats', help='显示搜索统计信息')
    p_stats.add_argument('--days', type=int, default=None, help='统计窗口（天）')

    # find 子命令
    p_find = sub.add_parser('find', help='按关键词查找历史搜索')
    p_find.add_argument('--query', required=True, help='查找关键词')
    p_find.add_argument('--days', type=int, default=None, help='时间窗口（天）')
    p_find.add_argument('--saved-only', action='store_true', help='仅显示收藏记录')

    # save 子命令
    p_save = sub.add_parser('save', help='将最近一条匹配记录标记为收藏')
    p_save.add_argument('--query', required=True, help='匹配关键词')

    # cache-get 子命令
    p_cg = sub.add_parser('cache-get', help='查询缓存')
    p_cg.add_argument('--query', required=True, help='搜索查询')
    p_cg.add_argument('--location', default='unknown', help='用户位置')
    p_cg.add_argument('--layer-hint', default='', help='层提示')

    # cache-clean 子命令
    sub.add_parser('cache-clean', help='清理过期缓存')

    # health 子命令
    sub.add_parser('health', help='MCP 健康检查')

    # v4.0 filter 子命令：对历史 top_results 做时效性 + 语言过滤
    p_filter = sub.add_parser('filter',
                               help='对历史搜索的 top_results 做时效性 + 语言过滤')
    p_filter.add_argument('--query', default='',
                          help='查询关键词（匹配历史记录的 query 字段）')
    p_filter.add_argument('--days', type=int, default=None,
                          help='历史记录时间窗口（天）')
    p_filter.add_argument('--recent', default=None,
                          help='仅保留最近 N 年的结果（如 2 = 保留 2024+）')
    p_filter.add_argument('--lang', default='all',
                          choices=['zh', 'en', 'all'],
                          help='语言过滤（默认 all）')
    p_filter.add_argument('--limit', type=int, default=50,
                          help='每条历史记录最多输出的结果数（默认 50）')

    args = parser.parse_args()

    try:
        if args.command == 'log':
            return log_search(args)
        elif args.command == 'recent':
            return show_recent(args)
        elif args.command == 'stats':
            return show_stats(args)
        elif args.command == 'find':
            return find_searches(args)
        elif args.command == 'save':
            return save_search(args)
        elif args.command == 'cache-get':
            return cache_get(args)
        elif args.command == 'cache-clean':
            return cache_clean(args)
        elif args.command == 'health':
            return health_check(args)
        elif args.command == 'filter':
            return filter_history_results(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"错误: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    return 1


if __name__ == '__main__':
    sys.exit(main())
