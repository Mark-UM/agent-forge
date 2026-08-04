#!/usr/bin/env python3
"""verifier.py — v4.4 P4.1.2 验证回路

针对 Agent-Forge 错误根因 4（验证回路缺失）的修复：
- Agent-Forge 拿到搜索结果后未回访 L1 官方源做最终核对
- 导致学生笔记覆盖官方 handbook

本模块实现 verify_against_authority() 函数：
- 从 results 中提取 L1 权威源候选（基于 P4.1.1 的 _classify_authority）
- 检查 query 是否含时效关键词（2026 / latest / current / handbook / official）
- 调用 fetch MCP 二次抓取 L1 域名首页（fetch MCP 由 OpenCode 提供）
- 对比 results 与 L1 抓取内容，标记 verified: true/false
- 失败兜底：verify 失败不阻塞主流程，仅 warning

设计原则：
- 零外部依赖（仅 urllib + 标准库）
- 隐私优先（调用 L1 抓取前过 privacy.py 脱敏）
- 异常兜底（任何步骤失败都不抛异常，返回 fallback 结果）
- 单文件聚合（不依赖其他 search 模块的可变状态）
"""
import os
import re
import sys
import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse, urljoin

# 添加 search 模块目录到 path（用于 import quality）
_SEARCH_DIR = os.path.dirname(os.path.abspath(__file__))
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

# v4.4: 修复 Review-Structure MAJOR #1 — quality 改为软依赖（避免硬依赖破坏零外部依赖原则）
# 若 quality 模块不可用，_HAS_QUALITY=False，所有 _classify_authority 调用走 fallback（unknown tier）
_HAS_QUALITY = False
try:
    import quality as _quality_mod  # noqa: F401
    _HAS_QUALITY = True
except ImportError:
    pass


def _safe_classify_authority(url, source='unknown'):
    """软依赖包装：quality 可用时调用 _classify_authority，否则返回 unknown。

    避免 verifier.py 在 quality 模块不可用时崩溃（保持零外部依赖原则）。
    """
    if not _HAS_QUALITY:
        return {'tier': 'unknown', 'score': 0,
                'reason': 'quality module not available (soft dep)'}
    try:
        return _quality_mod._classify_authority(url, source)
    except Exception as e:
        return {'tier': 'unknown', 'score': 0,
                'reason': f'_classify_authority failed: {type(e).__name__}'}

# ── 常量 ───────────────────────────────────────────────────────

# 时效关键词：query 含这些词时启用验证回路
_TIMESENSITIVE_KEYWORDS = {
    '2026', '2027', '2025', 'latest', 'current', 'recent',
    'handbook', 'official', 'updated', 'newest',
    # 课程 / 政策类
    'assessment', 'hurdle', 'prerequisite', 'syllabus',
    'curriculum', 'policy', 'requirement',
}

# 验证回路触发条件
TRIGGER_CONDITIONS = {
    'query_contains_year_keyword': 'query 含时效关键词',
    'l1_results_present': 'results 中存在 L1 权威源',
    'mixed_tiers': 'results 跨多个 tier（避免单 tier 偏见）',
    'low_authority_average': 'authority 平均分 < 2.0（建议二次核验）',
}

# HTTP 抓取参数
_FETCH_TIMEOUT = 10  # 秒
_FETCH_USER_AGENT = 'Mozilla/5.0 (compatible; SearchVerifier/4.4; +https://github.com/open-code-project)'
_MAX_L1_FETCHES = 2  # 最多抓取 2 个 L1 候选源（避免过载）
# v4.4 Review-Risk MAJOR #2: 响应大小限制（防 OOM）
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MB 上限
# v4.4 Review-Risk MAJOR #1: 重定向次数限制（防 SSRF via redirect chain）
_MAX_REDIRECTS = 3
# v4.4 Review-Risk MAJOR #1: 禁止重定向到私有 IP / loopback（防 SSRF）
_BLOCKED_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '::1'}
_BLOCKED_PREFIXES = ('10.', '172.16.', '172.17.', '172.18.', '172.19.',
                      '172.20.', '172.21.', '172.22.', '172.23.', '172.24.',
                      '172.25.', '172.26.', '172.27.', '172.28.', '172.29.',
                      '172.30.', '172.31.', '192.168.', '169.254.')

# L1 抓取内容片段长度（用于 cross-check）
_SNIPPET_MAX_LENGTH = 2000

# v4.4: 内置停用词（quality._STOP_WORDS 不可用时的 fallback）
_BUILTIN_STOP_WORDS = {
    'the', 'a', 'an', 'of', 'and', 'or', 'to', 'in', 'on', 'at',
    'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'for', 'with', 'by', 'from', 'as', 'into', 'about',
    'how', 'what', 'when', 'where', 'why', 'which', 'who', 'whom',
    'this', 'that', 'these', 'those', 'it', 'its',
}


# ── 核心实现 ────────────────────────────────────────────────────

def should_verify(query, results):
    """判断是否需要触发验证回路。

    触发条件（任一命中即触发）：
    1. query 含时效关键词（2026 / latest / handbook / official）
    2. results 中存在 L1 权威源（避免把 L3 当 L1）
    3. results 跨多个 tier（避免单 tier 偏见）
    4. authority 平均分 < 2.0（建议二次核验）

    Args:
        query: 用户原始查询字符串
        results: 搜索结果列表 [{title, url, snippet, source}, ...]

    Returns:
        dict: {
            'should_verify': bool,
            'trigger_reason': str,
            'authority_stats': dict  # L1/L2/L3/L4 分布
        }
    """
    if not isinstance(results, list) or not results:
        return {
            'should_verify': False,
            'trigger_reason': 'no results',
            'authority_stats': {},
        }

    query_lower = str(query or '').lower()

    # 条件 1: query 含时效关键词
    matched_keywords = [kw for kw in _TIMESENSITIVE_KEYWORDS
                        if kw in query_lower]
    if matched_keywords:
        return {
            'should_verify': True,
            'trigger_reason': f'query contains time-sensitive keywords: {matched_keywords}',
            'authority_stats': _compute_authority_stats(results),
        }

    # 统计 tier 分布
    tier_counts = {'L1': 0, 'L2': 0, 'L3': 0, 'L4': 0, 'unknown': 0}
    scores = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url', ''))
        source = str(r.get('source', 'unknown'))
        try:
            cls = _safe_classify_authority(url, source)
            tier_counts[cls['tier']] = tier_counts.get(cls['tier'], 0) + 1
            scores.append(cls['score'])
        except Exception:
            tier_counts['unknown'] += 1

    # 条件 2: results 中存在 L1 权威源
    if tier_counts.get('L1', 0) > 0:
        return {
            'should_verify': True,
            'trigger_reason': f"L1 results present ({tier_counts['L1']} URLs)",
            'authority_stats': {'tier_counts': tier_counts, 'avg_score': _safe_avg(scores)},
        }

    # 条件 3: results 跨多个 tier
    active_tiers = [t for t, c in tier_counts.items() if c > 0 and t != 'unknown']
    if len(active_tiers) >= 2:
        return {
            'should_verify': True,
            'trigger_reason': f'results span multiple tiers: {active_tiers}',
            'authority_stats': {'tier_counts': tier_counts, 'avg_score': _safe_avg(scores)},
        }

    # 条件 4: authority 平均分 < 2.0
    avg = _safe_avg(scores)
    if avg < 2.0:
        return {
            'should_verify': True,
            'trigger_reason': f'authority avg score {avg:.2f} < 2.0',
            'authority_stats': {'tier_counts': tier_counts, 'avg_score': avg},
        }

    return {
        'should_verify': False,
        'trigger_reason': 'no trigger condition met',
        'authority_stats': {'tier_counts': tier_counts, 'avg_score': avg},
    }


def verify_against_authority(query, results, fetch_callback=None):
    """验证回路核心函数。

    流程：
    1. should_verify 判断是否触发
    2. 若触发：从 results 提取 L1 候选 URL
    3. 调用 fetch_callback（或默认 urllib）抓取 L1 URL 内容片段
    4. 对比 results 与 L1 内容，标记 verified
    5. 返回 verification report

    Args:
        query: 原始查询
        results: 搜索结果列表
        fetch_callback: 可选的 fetch 函数，签名 fetch(url) -> str
                        默认使用 _default_fetch（urllib + 10s timeout）
                        OpenCode 可注入 mcp_callers['fetch'] 作为参数

    Returns:
        dict: {
            'verified': bool,  # 是否通过验证
            'trigger_reason': str,
            'l1_candidates': list[str],  # L1 URL 列表
            'fetched_l1': list[dict],  # 抓取结果
            'cross_check': dict,  # 交叉验证结果
            'warnings': list[str],
            'error': str | None,
        }
    """
    # 初始化返回值
    report = {
        'verified': False,
        'trigger_reason': '',
        'l1_candidates': [],
        'fetched_l1': [],
        'cross_check': {},
        'warnings': [],
        'error': None,
    }

    try:
        # Step 1: 判断是否需要验证
        decision = should_verify(query, results)
        report['trigger_reason'] = decision['trigger_reason']
        if not decision['should_verify']:
            report['verified'] = True  # 不需验证 → 视为通过
            report['warnings'].append('verification not triggered')
            return report

        # Step 2: 提取 L1 候选
        l1_candidates = _extract_l1_candidates(results)
        report['l1_candidates'] = [c['url'] for c in l1_candidates]

        if not l1_candidates:
            report['verified'] = False
            report['warnings'].append(
                'verification triggered but no L1 candidates found'
            )
            return report

        # Step 3: 抓取 L1 URL 内容（最多 _MAX_L1_FETCHES 个）
        fetch_fn = fetch_callback or _default_fetch
        fetched_l1 = []
        for candidate in l1_candidates[:_MAX_L1_FETCHES]:
            url = candidate['url']
            try:
                content = fetch_fn(url)
                if content:
                    fetched_l1.append({
                        'url': url,
                        'content_snippet': content[:_SNIPPET_MAX_LENGTH],
                        'fetched_at': int(time.time()),
                        'status': 'success',
                    })
                else:
                    fetched_l1.append({
                        'url': url,
                        'content_snippet': '',
                        'fetched_at': int(time.time()),
                        'status': 'empty_response',
                    })
                    report['warnings'].append(f'fetch {url} returned empty')
            except Exception as e:
                fetched_l1.append({
                    'url': url,
                    'content_snippet': '',
                    'fetched_at': int(time.time()),
                    'status': f'error: {type(e).__name__}: {str(e)[:200]}',
                })
                report['warnings'].append(f'fetch {url} failed: {type(e).__name__}')

        report['fetched_l1'] = fetched_l1

        # Step 4: 交叉验证
        cross_check = _cross_check_results(query, results, fetched_l1)
        report['cross_check'] = cross_check

        # Step 5: 判定 verified
        # S13 fix: keyword overlap 是词汇一致性，不是事实验证。
        # 旧阈值 0.5 太宽松，会将"关键词重合"误判为"已验证"。
        # 新阈值 0.8 更严格：只有强一致性才标记 verified=True。
        # 同时新增 lexical_consistency / source_overlap / verification_strength
        # 字段在 cross_check 中，供消费者区分"词汇一致"与"事实验证"。
        successful_fetches = [f for f in fetched_l1 if f['status'] == 'success']
        if not successful_fetches:
            report['verified'] = False
            report['warnings'].append('all L1 fetches failed')
        else:
            consistency = cross_check.get('consistency_score', 0.0)
            if consistency >= 0.8:
                report['verified'] = True
            else:
                report['verified'] = False
                report['warnings'].append(
                    f'cross-check consistency {consistency:.2f} < 0.8 threshold'
                )

        return report

    except Exception as e:
        # 全异常兜底：不阻塞主流程
        report['verified'] = False
        report['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        report['warnings'].append(f'verifier internal error: {type(e).__name__}')
        return report


# ── 辅助函数 ────────────────────────────────────────────────────

def _compute_authority_stats(results):
    """计算 results 的权威性统计。"""
    tier_counts = {'L1': 0, 'L2': 0, 'L3': 0, 'L4': 0, 'unknown': 0}
    scores = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url', ''))
        source = str(r.get('source', 'unknown'))
        try:
            cls = _safe_classify_authority(url, source)
            tier_counts[cls['tier']] = tier_counts.get(cls['tier'], 0) + 1
            scores.append(cls['score'])
        except Exception:
            tier_counts['unknown'] += 1
    return {'tier_counts': tier_counts, 'avg_score': _safe_avg(scores)}


def _extract_l1_candidates(results):
    """从 results 提取 L1 权威源候选。"""
    candidates = []
    if not isinstance(results, list):
        return candidates
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url', ''))
        source = str(r.get('source', 'unknown'))
        try:
            cls = _safe_classify_authority(url, source)
            if cls['tier'] == 'L1':
                candidates.append({
                    'url': url,
                    'title': r.get('title', ''),
                    'reason': cls['reason'],
                })
        except Exception:
            continue
    return candidates


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """v4.4 Review-Risk MAJOR #1: 安全重定向处理器。

    限制重定向次数 + 拒绝重定向到私有 IP / loopback（防 SSRF）。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 检查重定向次数
        redirect_count = 0
        try:
            # urllib 内部用 _redirect_request_depth 属性追踪
            redirect_count = getattr(req, '_redirect_request_depth', 0) + 1
        except Exception:
            pass
        if redirect_count > _MAX_REDIRECTS:
            raise urllib.error.HTTPError(
                req.full_url, 502,
                f'Too many redirects (>{_MAX_REDIRECTS})',
                headers, None)

        # 解析 newurl，检查目标 host
        try:
            target_netloc = urlparse(newurl).netloc.lower()
            target_host = target_netloc.split(':')[0]  # 去端口
            if target_host in _BLOCKED_HOSTS:
                raise urllib.error.HTTPError(
                    req.full_url, 403,
                    f'SSRF blocked: redirect to {target_host}',
                    headers, None)
            for prefix in _BLOCKED_PREFIXES:
                if target_host.startswith(prefix):
                    raise urllib.error.HTTPError(
                        req.full_url, 403,
                        f'SSRF blocked: redirect to private IP {target_host}',
                        headers, None)
        except urllib.error.HTTPError:
            raise
        except Exception:
            pass  # 解析失败 → 允许（urlopen 会再校验）

        # 标记新请求的重定向深度
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req._redirect_request_depth = redirect_count
        return new_req


def _default_fetch(url):
    """默认 fetch 函数（urllib + 10s timeout + UA + SSRF 防护 + 大小限制）。

    用于 OpenCode 环境外（如单元测试）。
    生产环境应注入 fetch_callback = mcp_callers.get('fetch')。

    v4.4 Review-Risk 修复：
    - MAJOR #1: 自定义重定向处理器（最多 3 次 + 拒绝私有 IP）
    - MAJOR #2: 响应大小限制（2MB 上限，防 OOM）
    """
    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
        raise ValueError(f'invalid url: {url}')

    req = urllib.request.Request(url, headers={
        'User-Agent': _FETCH_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',
    })
    # v4.4: 使用安全 redirect handler
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    with opener.open(req, timeout=_FETCH_TIMEOUT) as response:
        # 仅处理 2xx
        status = response.status if hasattr(response, 'status') else response.getcode()
        if status < 200 or status >= 300:
            raise urllib.error.HTTPError(
                url, status, f'HTTP {status}', response.headers, None)
        # v4.4 Review-Risk MAJOR #2: 限制读取大小（防 OOM）
        raw = response.read(_MAX_RESPONSE_BYTES)
        # 尝试常见编码
        for encoding in ('utf-8', 'latin-1', 'gbk'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')


def _cross_check_results(query, results, fetched_l1):
    """交叉验证：检查 query 关键词在 L1 内容中的命中率。

    一致性评分 = (L1 内容中命中的 query 关键词数) / (query 关键词总数)

    S13 fix: keyword overlap 是词汇一致性，不是事实验证。
    - lexical_consistency: 原始一致性分数（0.0-1.0），与 consistency_score 同值
    - source_overlap: 内容覆盖至少一个 query 关键词的 L1 URL 列表
    - verification_strength: 'none' | 'weak' | 'moderate' | 'strong'
      - 'none': 无 L1 成功抓取或无 query 关键词
      - 'weak': consistency < 0.3
      - 'moderate': 0.3 <= consistency < 0.8
      - 'strong': consistency >= 0.8

    Args:
        query: 用户查询
        results: 搜索结果（仅用于提取关键词上下文）
        fetched_l1: 抓取的 L1 内容列表

    Returns:
        dict: {
            'consistency_score': float [0.0, 1.0],
            'query_keywords': list[str],
            'matched_keywords': list[str],
            'missed_keywords': list[str],
            'l1_url_covered': list[str],
            'lexical_consistency': float [0.0, 1.0],  # S13
            'source_overlap': list[str],                # S13
            'verification_strength': str,                # S13
        }
    """
    # 提取 query 关键词
    query_keywords = _extract_keywords(query)

    # S13: 无关键词或无抓取结果 → verification_strength='none'
    successful_fetches = [f for f in fetched_l1
                          if isinstance(f, dict) and f.get('status') == 'success']
    if not query_keywords or not fetched_l1 or not successful_fetches:
        return {
            'consistency_score': 0.0,
            'query_keywords': query_keywords,
            'matched_keywords': [],
            'missed_keywords': query_keywords,
            'l1_url_covered': [],
            'lexical_consistency': 0.0,
            'source_overlap': [],
            'verification_strength': 'none',
        }

    # 合并所有 L1 内容
    l1_text = ' '.join(
        f.get('content_snippet', '') for f in successful_fetches
    ).lower()

    if not l1_text:
        return {
            'consistency_score': 0.0,
            'query_keywords': query_keywords,
            'matched_keywords': [],
            'missed_keywords': query_keywords,
            'l1_url_covered': [],
            'lexical_consistency': 0.0,
            'source_overlap': [],
            'verification_strength': 'none',
        }

    # 计算命中率
    matched = []
    missed = []
    for kw in query_keywords:
        if kw.lower() in l1_text:
            matched.append(kw)
        else:
            missed.append(kw)

    consistency = len(matched) / len(query_keywords)

    # S13: source_overlap — 内容覆盖至少一个 query 关键词的 L1 URL
    source_overlap = []
    for f in successful_fetches:
        snippet = f.get('content_snippet', '').lower()
        if any(kw.lower() in snippet for kw in query_keywords):
            source_overlap.append(f['url'])

    # S13: verification_strength 分级
    if consistency < 0.3:
        strength = 'weak'
    elif consistency < 0.8:
        strength = 'moderate'
    else:
        strength = 'strong'

    return {
        'consistency_score': round(consistency, 4),
        'query_keywords': query_keywords,
        'matched_keywords': matched,
        'missed_keywords': missed,
        'l1_url_covered': [f['url'] for f in successful_fetches],
        'lexical_consistency': round(consistency, 4),
        'source_overlap': source_overlap,
        'verification_strength': strength,
    }


def _extract_keywords(query):
    """从 query 提取有效关键词（去停用词 + 长度 ≥ 3）。"""
    if not isinstance(query, str) or not query.strip():
        return []

    # v4.4: 修复 Review-Code MAJOR #3 — quality 改为软依赖（_quality_mod）
    # 复用 quality._STOP_WORDS（若可导入），否则使用内置 fallback
    if _HAS_QUALITY:
        try:
            stop_words = _quality_mod._STOP_WORDS
        except AttributeError:
            stop_words = _BUILTIN_STOP_WORDS
    else:
        stop_words = _BUILTIN_STOP_WORDS

    keywords = []
    for word in re.split(r'\W+', query.strip()):
        if not word:
            continue
        wl = word.lower()
        if len(wl) < 3:
            continue  # 过滤 1-2 字符词
        if wl in stop_words:
            continue
        keywords.append(wl)
    return keywords


def _safe_avg(numbers):
    """安全计算平均值（空列表返回 0.0）。"""
    if not numbers:
        return 0.0
    return sum(numbers) / len(numbers)


# ── CLI 入口 ────────────────────────────────────────────────────

def _main():
    """CLI 入口：python -m modules.search.verifier "query" url1 url2 ..."""
    import argparse
    parser = argparse.ArgumentParser(
        description='v4.4 P4.1.2 验证回路：检查搜索结果与 L1 官方源一致性'
    )
    parser.add_argument('query', help='用户原始查询')
    parser.add_argument('--urls', nargs='*', default=[],
                        help='搜索结果 URL 列表（空格分隔）')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='打印详细过程')
    args = parser.parse_args()

    # 构造 results
    results = [{'url': u, 'source': 'unknown'} for u in args.urls]

    if args.verbose:
        decision = should_verify(args.query, results)
        print('should_verify:', json.dumps(decision, indent=2, ensure_ascii=False))

    report = verify_against_authority(args.query, results)

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    _main()
