#!/usr/bin/env python3
"""搜索结果质量评分 — 双轨模式（启发式 + Flash 模型）。

设计原则：
- 单文件独立模块，零外部依赖（仅 Python 标准库）
- 双轨模式：heuristic（默认，免费）| flash（LLM 客观评分）
- Flash 失败时优雅降级到 heuristic
- 评分缓存 7 天 TTL，避免重复调用 LLM
- 所有 IO 异常兜底，绝不阻塞主流程

Scoring Dimensions:
- relevance (0-4): query 关键词在 title/snippet 出现频率
- authoritativeness (0-3): source 字段权威性
- freshness (0-2): 优先 URL/snippet 含 2024-2025-2026
- diversity (0-1): 不同域名数 / 总结果数
- 总分 = sum(dimensions) = 0-10

Usage:
  python quality.py score --query "..." --results-json '[...]'
  python quality.py score --query "..." --results-json '[...]' --mode flash
  python -m modules.search.tests.test_quality
"""
import sys
import os
import json
import time
import re
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from urllib.parse import urlparse

# 导入 privacy 模块做出境 PII 脱敏（quality cache 和 Flash prompt 都不应含 PII）
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    # 兜底：privacy 模块不可用时定义 noop
    def _redact_outbound(query):
        return query, {'redacted_count': 0}

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, '_runtime', 'search')
CACHE_FILE = os.path.join(_CACHE_DIR, 'quality_cache.json')

# 缓存 TTL：7 天（评分不变，比查询缓存长）
CACHE_TTL = 7 * 24 * 3600

# ── 启发式评分常量 ─────────────────────────────────────────────
# v4.4 P4.1.1: Authority tier 分层重构（替代旧 _AUTHORITY_MAP 扁平映射）
# 旧 _AUTHORITY_MAP 把 github 与 official-docs 同列为 3 分，导致 Agent-Forge 错误：
# 学生 GitHub 笔记（L3）被等同于官方 handbook（L1）。
# 新方案：四层 L1/L2/L3/L4，由 _classify_authority(url, source) 统一判定。
# 配置可由 markconfig/authority_whitelist.json 覆盖。
#
# 兼容策略：保留旧 _AUTHORITY_MAP 供 fallback 使用（whitelist 加载失败时）。
_AUTHORITY_MAP = {
    'official-docs': 3,
    'official_docs': 3,
    'official': 3,
    'handbook': 3,        # v4.4 新增
    'moodle': 3,          # v4.4 新增
    'instructor': 2,      # v4.4 新增
    'github': 1,          # v4.4: 从 3 降到 1（学生笔记 ≠ 官方文档）
    'personal': 1,        # v4.4 新增
    'student-notes': 1,   # v4.4 新增
    'gist': 1,            # v4.4 新增
    'mailing-list': 2,    # v4.4 新增
    'docs': 3,
    'reference': 3,
    'blog': 1,            # v4.4: 从 2 降到 1（L4 第三方博客）
    'medium': 1,          # v4.4: 从 2 降到 1
    'dev.to': 1,          # v4.4: 从 2 降到 1
    'forum': 1,
    'stackoverflow': 1,   # v4.4: 从 2 降到 1（L4）
    'stack-overflow': 1,  # v4.4: 从 2 降到 1
    'reddit': 1,
    'news': 1,             # v4.4 新增
    'unknown': 0,
}

# v4.4 P4.1.1: 权威性分层配置（L1=官方 / L2=教师组织 / L3=学生个人 / L4=博客论坛）
# 与 _AUTHORITY_MAP 并存：_classify_authority 优先，失败 fallback 到 _AUTHORITY_MAP
AUTHORITY_TIERS = {
    'L1': {
        'domains': [
            'handbook.monash.edu', 'moodle.monash.edu', 'monash.edu',
            'policy.monash.edu',
            'docs.python.org', 'developer.mozilla.org', 'react.dev',
            'angular.io', 'vuejs.org', 'kubernetes.io', 'golang.org',
            'rust-lang.org', 'typescriptlang.org', 'nodejs.org',
            'docs.microsoft.com', 'learn.microsoft.com',
            'aws.amazon.com', 'cloud.google.com', 'azure.microsoft.com',
            'w3.org', 'ietf.org', 'ecma-international.org',
            'iso.org', 'ieee.org', 'acm.org',
            'arxiv.org', 'semanticscholar.org', 'scholar.google.com',
            'doi.org',
        ],
        'source_fields': ['official-docs', 'official_docs', 'official',
                           'handbook', 'moodle'],
        'score': 3,
    },
    'L2': {
        'domains': [
            'users.monash.edu', 'mail-archive.com',
            'apache.org', 'fsf.org', 'kernel.org',
            'postgresql.org', 'mysql.com', 'redis.io', 'mongodb.com',
        ],
        'source_fields': ['instructor', 'mailing-list'],
        'score': 2,
    },
    'L3': {
        'domains': [
            'github.com', 'gitlab.com', 'gitee.com', 'bitbucket.org',
            'codeberg.org', 'sourcehut.org', 'gist.github.com',
            'pastebin.com', 'paste.ubuntu.com',
        ],
        'source_fields': ['github', 'personal', 'student-notes', 'gist'],
        'score': 1,
    },
    'L4': {
        'domains': [
            'medium.com', 'dev.to', 'reddit.com', 'stackoverflow.com',
            'stackexchange.com', 'quora.com', 'zhihu.com', 'csdn.net',
            'jianshu.com', 'juejin.cn', 'news.ycombinator.com',
            'freecodecamp.org', 'tutorialspoint.com', 'geeksforgeeks.org',
            'w3schools.com', 'baeldung.com', 'dzone.com',
        ],
        'source_fields': ['blog', 'medium', 'forum', 'stackoverflow',
                           'reddit', 'news'],
        'score': 1,
    },
}

# v4.4 P4.1.1: GitHub 官方组织白名单（命中则从 L3 提升到 L2）
GITHUB_ORG_WHITELIST = {
    'python', 'microsoft', 'facebook', 'google', 'angular', 'vuejs',
    'rust-lang', 'golang', 'nodejs', 'kubernetes', 'apache',
    'opencv', 'tensorflow', 'pytorch',
}

# v4.4 P4.1.1: whitelist 配置文件路径（用户可覆盖默认 AUTHORITY_TIERS）
_WHITELIST_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'markconfig', 'authority_whitelist.json'
)

# 运行时缓存：whitelist 加载结果（避免每次评分都读文件）
_WHITELIST_CACHE = {'loaded': False, 'tiers': None, 'github_orgs': None}


def _load_authority_whitelist():
    """加载 markconfig/authority_whitelist.json 覆盖默认 AUTHORITY_TIERS。

    Returns:
        tuple: (tiers: dict, github_orgs: set) — 失败返回 (None, None)
    """
    if _WHITELIST_CACHE['loaded']:
        return _WHITELIST_CACHE['tiers'], _WHITELIST_CACHE['github_orgs']

    if not os.path.exists(_WHITELIST_FILE):
        _WHITELIST_CACHE['loaded'] = True
        _WHITELIST_CACHE['tiers'] = None
        _WHITELIST_CACHE['github_orgs'] = None
        return None, None

    try:
        with open(_WHITELIST_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        tiers = {}
        for tier_name in ('L1', 'L2', 'L3', 'L4'):
            if tier_name in data and isinstance(data[tier_name], dict):
                tier_data = data[tier_name]
                tiers[tier_name] = {
                    'domains': [d.lower() for d in tier_data.get('domains', [])
                                if isinstance(d, str)],
                    'source_fields': [s.lower() for s in tier_data.get('source_fields', [])
                                       if isinstance(s, str)],
                    'score': int(tier_data.get('score',
                                               AUTHORITY_TIERS[tier_name]['score'])),
                }
            else:
                tiers[tier_name] = AUTHORITY_TIERS[tier_name]

        github_orgs = set()
        if 'github_org_whitelist' in data and isinstance(data['github_org_whitelist'], list):
            github_orgs = {str(o).lower() for o in data['github_org_whitelist']
                           if isinstance(o, str)}

        _WHITELIST_CACHE['loaded'] = True
        _WHITELIST_CACHE['tiers'] = tiers
        _WHITELIST_CACHE['github_orgs'] = github_orgs
        return tiers, github_orgs
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
        print(f'警告: authority_whitelist.json 加载失败: {e}', file=sys.stderr)
        _WHITELIST_CACHE['loaded'] = True
        _WHITELIST_CACHE['tiers'] = None
        _WHITELIST_CACHE['github_orgs'] = None
        return None, None


def _classify_authority(url, source='unknown'):
    """v4.4 P4.1.1: 对单个 URL + source 做权威性分层判定。

    分层逻辑：
    1. 先按域名匹配（L1 → L2 → L3 → L4，首个命中返回）
    2. 再按 source 字段匹配（同上顺序）
    3. GitHub 特例：若 path 含 /<org>/<repo> 且 <org> 在 GITHUB_ORG_WHITELIST，
       则从 L3 提升到 L2
    4. 通用 TLD 加权（.gov / .edu → L1，.org → L2）
    5. 全部不命中 → tier=unknown, score=0

    Args:
        url: 结果 URL 字符串
        source: MCP 返回的 source 字段（'official-docs' / 'github' / 等）

    Returns:
        dict: {
            'tier': 'L1' | 'L2' | 'L3' | 'L4' | 'unknown',
            'score': int (0-3),
            'reason': str  # 命中原因（debugging 用）
        }
    """
    if not isinstance(url, str) or not url:
        return {'tier': 'unknown', 'score': 0, 'reason': 'empty url'}

    source = str(source or 'unknown').lower()

    # 加载 whitelist（若已配置）
    tiers, github_orgs = _load_authority_whitelist()
    if tiers is None:
        tiers = AUTHORITY_TIERS
        github_orgs = GITHUB_ORG_WHITELIST

    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        netloc = ''
    if not netloc:
        # 仅 source 字段可判
        for tier_name in ('L1', 'L2', 'L3', 'L4'):
            if source in tiers[tier_name]['source_fields']:
                return {
                    'tier': tier_name,
                    'score': tiers[tier_name]['score'],
                    'reason': f'source={source} → {tier_name}',
                }
        return {'tier': 'unknown', 'score': 0, 'reason': 'no netloc, source not in tiers'}

    # 去除常见前缀（www.）
    if netloc.startswith('www.'):
        netloc_core = netloc[4:]
    else:
        netloc_core = netloc

    # 1. 域名匹配 — v4.4: longest-domain-match 策略
    # 收集所有 tier 中匹配的域名，选择最长（最具体）的，避免宽泛根域名（如 monash.edu）
    # 吞掉具体子域名（如 users.monash.edu，应属 L2 而非 L1）。
    best_match = None  # (tier_name, domain, score)
    for tier_name in ('L1', 'L2', 'L3', 'L4'):
        for domain in tiers[tier_name]['domains']:
            if netloc == domain or netloc.endswith('.' + domain):
                if best_match is None or len(domain) > len(best_match[1]):
                    best_match = (tier_name, domain, tiers[tier_name]['score'])

    if best_match is not None:
        tier_name, domain, score = best_match
        # GitHub 特例：检查 org 白名单
        if tier_name == 'L3' and netloc_core in ('github.com', 'gist.github.com'):
            promoted = _try_promote_github_org(url, github_orgs)
            if promoted:
                return promoted
        return {
            'tier': tier_name,
            'score': score,
            'reason': f'domain {netloc} matches {domain} → {tier_name}',
        }

    # 2. source 字段匹配
    for tier_name in ('L1', 'L2', 'L3', 'L4'):
        if source in tiers[tier_name]['source_fields']:
            return {
                'tier': tier_name,
                'score': tiers[tier_name]['score'],
                'reason': f'source={source} → {tier_name}',
            }

    # 3. 通用 TLD 加权（与旧 _score_authoritativeness fallback 保持一致）
    if netloc.endswith('.gov') or netloc.endswith('.edu'):
        return {'tier': 'L1', 'score': 3,
                'reason': f'.gov/.edu TLD → L1 ({netloc})'}
    if netloc.endswith('.org'):
        return {'tier': 'L2', 'score': 2,
                'reason': f'.org TLD → L2 ({netloc})'}
    # v4.4: 修复 Review-Code MINOR #4 — 补齐 .dev / .io / .ai 处理
    if netloc.endswith('.dev') or netloc.endswith('.io') or netloc.endswith('.ai'):
        return {'tier': 'L2', 'score': 2,
                'reason': f'.dev/.io/.ai TLD → L2 ({netloc})'}

    # 4. 子串识别（docs.* / developer.*）
    if 'docs.' in netloc or 'developer.' in netloc:
        return {'tier': 'L1', 'score': 3,
                'reason': f'docs./developer. subdomain → L1 ({netloc})'}

    # 5. fallback 到旧 _AUTHORITY_MAP（仍兼容旧 source 字段）
    legacy_score = _AUTHORITY_MAP.get(source, 0)
    if legacy_score > 0:
        # 旧 map 命中 → 推断 tier
        if legacy_score >= 3:
            tier = 'L1'
        elif legacy_score == 2:
            tier = 'L2'
        else:
            tier = 'L4'  # 旧 blog / stackoverflow 等
        return {'tier': tier, 'score': legacy_score,
                'reason': f'legacy _AUTHORITY_MAP[{source}]={legacy_score} → {tier}'}

    return {'tier': 'unknown', 'score': 0,
            'reason': f'no match for {netloc}, source={source}'}


def _try_promote_github_org(url, github_orgs):
    """检查 GitHub URL 是否在官方组织白名单内，若是则提升到 L2。

    URL 格式：
        https://github.com/<org>/<repo>
        https://gist.github.com/<user>

    Returns:
        dict or None: 若命中返回 {'tier': 'L2', ...}, 否则 None
    """
    if not github_orgs:
        return None

    try:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        if not path:
            return None
        parts = path.split('/', 1)
        org = parts[0].lower()
        if org in github_orgs:
            return {
                'tier': 'L2',
                'score': 2,
                'reason': f'github org {org} in whitelist → promoted to L2',
            }
    except Exception:
        pass
    return None


# 年份正则
_YEAR_PATTERN = re.compile(r'(20\d{2})')
_VALID_YEAR_MIN = 2010
# 当前年份动态获取，避免硬编码 2026 在 2027+ 失效
_VALID_YEAR_MAX = datetime.now().year


# v4.4 P4.1.1: 提升为模块级常量（修复 Review-Code MAJOR #3：
# verifier.py 引用 quality._STOP_WORDS 但原为局部变量，导致 try/except 总是 fallback）
_STOP_WORDS = {
    'the', 'a', 'an', 'of', 'and', 'or', 'in', 'on', 'to',
    'for', 'is', 'are', 'was', 'were', 'with', 'by', 'at',
    'from', 'as', 'that', 'this', 'these', 'those',
    '的', '了', '在', '是', '和', '与', '或', '对',
}


def _score_relevance(query, results):
    """相关性评分 0-4。

    算法：query 关键词在 results 的 title/snippet 中命中比例。
    """
    if not query or not results:
        return 0.0

    # 提取 query 关键词（去除停用词）— v4.4: 引用模块级 _STOP_WORDS
    keywords = [w.lower() for w in re.split(r'\W+', query.strip())
               if w and len(w) >= 2 and w.lower() not in _STOP_WORDS]
    if not keywords:
        return 2.0  # 无法提取关键词时给中等分

    total_hits = 0
    for r in results:
        if not isinstance(r, dict):
            continue  # 跳过非 dict 元素，保持与 _filter_results 一致
        title = str(r.get('title', '')).lower()
        snippet = str(r.get('snippet', '')).lower()
        text = f"{title} {snippet}"
        hits = sum(1 for kw in keywords if kw in text)
        total_hits += (hits / len(keywords))

    # 仅统计有效 dict 数量做分母
    valid_count = sum(1 for r in results if isinstance(r, dict))
    if valid_count == 0:
        return 0.0
    avg_hit_ratio = total_hits / valid_count
    # 映射到 0-4
    return min(4.0, avg_hit_ratio * 4.0)


def _score_authoritativeness(results):
    """权威性评分 0-3。

    v4.4 P4.1.1: 改用 _classify_authority 做 L1/L2/L3/L4 四层判定，
    替代旧的 _AUTHORITY_MAP 扁平映射 + 域名后缀加权。
    保留旧逻辑作为 fallback（whitelist 加载失败时）。
    """
    if not results:
        return 0.0

    scores = []
    for r in results:
        if not isinstance(r, dict):
            continue  # 跳过非 dict 元素
        url = str(r.get('url', ''))
        source = str(r.get('source', 'unknown'))

        # v4.4 P4.1.1: 优先使用 _classify_authority 四层判定
        try:
            classification = _classify_authority(url, source)
            score = classification['score']
        except Exception:
            # fallback 到旧逻辑（_AUTHORITY_MAP + 域名后缀加权）
            score = _AUTHORITY_MAP.get(source.lower(), 0)
            try:
                netloc = urlparse(url).netloc.lower()
                if netloc.endswith('.gov') or netloc.endswith('.edu'):
                    score = max(score, 3)
                elif netloc.endswith('.org') or netloc.endswith('.dev'):
                    score = max(score, 2)
                elif netloc.endswith('.io') or netloc.endswith('.ai'):
                    score = max(score, 2)
                if 'docs.' in netloc or 'developer.' in netloc:
                    score = max(score, 3)
            except Exception:
                pass

        scores.append(score)

    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)
    return min(3.0, avg)


def _score_freshness(results):
    """时效性评分 0-2。

    算法：从 URL/snippet 提取最新年份，越新分数越高。
    """
    if not results:
        return 0.0

    scores = []
    for r in results:
        url = str(r.get('url', ''))
        snippet = str(r.get('snippet', ''))
        text = f"{url} {snippet}"

        years = []
        for m in _YEAR_PATTERN.findall(text):
            try:
                y = int(m)
                if _VALID_YEAR_MIN <= y <= _VALID_YEAR_MAX:
                    years.append(y)
            except ValueError:
                continue

        if not years:
            scores.append(0.5)  # 无年份信息给基础分
            continue

        latest = max(years)
        # 越接近当前年份分数越高
        age = _VALID_YEAR_MAX - latest
        if age <= 0:
            scores.append(2.0)
        elif age <= 1:
            scores.append(1.5)
        elif age <= 2:
            scores.append(1.0)
        elif age <= 3:
            scores.append(0.5)
        else:
            scores.append(0.0)

    avg = sum(scores) / len(scores)
    return min(2.0, avg)


def _score_diversity(results):
    """多样性评分 0-1。

    算法：不同域名数 / 总结果数。
    """
    if not results:
        return 0.0

    domains = set()
    for r in results:
        url = str(r.get('url', ''))
        try:
            netloc = urlparse(url).netloc.lower()
            if netloc:
                domains.add(netloc)
        except Exception:
            continue

    ratio = len(domains) / len(results)
    # ratio=1.0 (全部不同域名) → 1.0 分
    # ratio=0.1 (10 条同域名) → 0.1 分
    return min(1.0, ratio)


def _heuristic_score(query, results):
    """启发式评分 — 4 维度加权。"""
    relevance = _score_relevance(query, results)
    authoritativeness = _score_authoritativeness(results)
    freshness = _score_freshness(results)
    diversity = _score_diversity(results)

    total = relevance + authoritativeness + freshness + diversity
    return {
        'score': round(total, 2),
        'dimensions': {
            'relevance': round(relevance, 2),
            'authoritativeness': round(authoritativeness, 2),
            'freshness': round(freshness, 2),
            'diversity': round(diversity, 2),
        },
        'mode': 'heuristic',
        'rationale': 'Keyword matching + source mapping + year extraction + domain diversity',
    }


# ── Flash 模型评分 ──────────────────────────────────────────────
_FLASH_API = 'https://api.deepseek.com/v1/chat/completions'
_FLASH_MODEL = 'deepseek-chat'  # DeepSeek V4 Flash 对应 endpoint

# Phase 2: import flash_guard for model resolution
# quality_scoring is in the Flash ALLOWED task list (utility, has fallback to heuristic)
try:
    import sys as _sys
    _PROJECT_ROOT_FOR_GUARD = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PROJECT_ROOT_FOR_GUARD not in _sys.path:
        _sys.path.insert(0, _PROJECT_ROOT_FOR_GUARD)
    from modules.dispatch import guard as _flash_guard
    _HAS_FLASH_GUARD = True
except ImportError:
    _HAS_FLASH_GUARD = False

_FLASH_PROMPT_TEMPLATE = """You are a search result quality evaluator. Score the following results 0-10.

Query: {query}

Results:
{results_block}

Output JSON only (no markdown fences, no explanation outside JSON):
{{
  "score": <0-10 float>,
  "dimensions": {{
    "relevance": <0-4>,
    "authoritativeness": <0-3>,
    "freshness": <0-2>,
    "diversity": <0-1>
  }},
  "rationale": "<one short sentence explaining the score>"
}}
"""


def _build_flash_prompt(query, results):
    """构造 Flash 评分 prompt。

    出境前对 query 做 PII 脱敏，避免 PII 上传到 DeepSeek。
    """
    # 出境 PII 脱敏：Flash prompt 不应含 PII
    redacted_query, _ = _redact_outbound(query)
    lines = []
    for i, r in enumerate(results[:10], 1):
        title = str(r.get('title', ''))[:100]
        snippet = str(r.get('snippet', ''))[:200]
        source = str(r.get('source', 'unknown'))
        url = str(r.get('url', ''))
        lines.append(f"{i}. {title} — {snippet} (source: {source}, url: {url})")
    results_block = '\n'.join(lines) if lines else '(no results)'

    return _FLASH_PROMPT_TEMPLATE.format(query=redacted_query, results_block=results_block)


def _call_flash_api(query, results, api_key=None):
    """调用 DeepSeek Flash API 做评分。

    复用 recognize.py 的 urllib 模式。失败抛异常，由调用方降级。
    """
    if api_key is None:
        # 仅使用 DEEPSEEK_API_KEY，避免将 Anthropic token 误发给 DeepSeek
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')

    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY 未设置（请勿使用 ANTHROPIC_AUTH_TOKEN 作为 DeepSeek 凭证）')

    prompt = _build_flash_prompt(query, results)
    # Phase 2: resolve model via guard (quality_scoring is allowlisted → Flash)
    if _HAS_FLASH_GUARD:
        model = _flash_guard.resolve_model(
            "quality_scoring", caller="quality._call_flash_api")
    else:
        model = _FLASH_MODEL
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 300,
        'temperature': 0.1,
    }

    req = urllib.request.Request(
        _FLASH_API,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Flash API HTTP {e.code}: {e.read().decode("utf-8", errors="replace")[:200]}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Flash API URL error: {e.reason}')

    try:
        text = data['choices'][0]['message']['content'].strip()
        # 兼容模型可能输出的 markdown fences
        if text.startswith('```'):
            # 去除首行 ```json 或 ``` 和末行 ```
            lines = text.split('\n')
            if lines and lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            text = '\n'.join(lines).strip()

        parsed = json.loads(text)
        # 字段验证
        score = float(parsed.get('score', 0))
        score = max(0.0, min(10.0, score))

        dims = parsed.get('dimensions', {})
        dimensions = {
            'relevance': max(0.0, min(4.0, float(dims.get('relevance', 0)))),
            'authoritativeness': max(0.0, min(3.0, float(dims.get('authoritativeness', 0)))),
            'freshness': max(0.0, min(2.0, float(dims.get('freshness', 0)))),
            'diversity': max(0.0, min(1.0, float(dims.get('diversity', 0)))),
        }

        return {
            'score': round(score, 2),
            'dimensions': dimensions,
            'mode': 'flash',
            'rationale': str(parsed.get('rationale', ''))[:200],
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as e:
        raise RuntimeError(f'Flash API response parse failed: {e}')


# ── 缓存层 ──────────────────────────────────────────────────────
def _cache_key(query, results):
    """生成评分缓存键（基于 query + results 内容 hash）。"""
    import hashlib
    # 提取关键字段做 hash
    sig_parts = [query.strip().lower()]
    for r in results[:10]:
        sig_parts.append(str(r.get('url', '')))
        sig_parts.append(str(r.get('title', ''))[:50])
    sig = '|'.join(sig_parts)
    return hashlib.md5(sig.encode('utf-8')).hexdigest()


def _load_cache():
    """加载评分缓存。"""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache):
    """保存评分缓存。失败不抛异常。"""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f'警告: quality_cache 写入失败: {e}', file=sys.stderr)


def _cache_get(query, results):
    """查询缓存。命中返回评分 dict，未命中返回 None。"""
    cache = _load_cache()
    key = _cache_key(query, results)
    entry = cache.get(key)
    if not entry:
        return None

    cached_at = entry.get('cached_at', 0)
    if time.time() - cached_at > CACHE_TTL:
        cache.pop(key, None)
        _save_cache(cache)
        return None

    # 返回 shallow copy 避免污染内存中的 cache dict
    result = dict(entry.get('result', {}))
    result['cached'] = True
    result['cached_at'] = datetime.fromtimestamp(cached_at).isoformat()
    return result


def _cache_store(query, results, score_result):
    """写入评分缓存。失败不抛异常。"""
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        # 出境 PII 脱敏：缓存文件不应含 PII（与 search.py cache_store 保持一致）
        redacted_query, _ = _redact_outbound(query)
        cache = _load_cache()
        key = _cache_key(query, results)  # key 基于原 query 计算，保证缓存命中
        cache[key] = {
            'query': redacted_query,  # 仅存储脱敏后的 query
            'result': score_result,
            'cached_at': time.time(),
        }
        # 容量限制：超 500 条删最早的
        if len(cache) > 500:
            sorted_items = sorted(cache.items(),
                                  key=lambda x: x[1].get('cached_at', 0))
            cache = dict(sorted_items[-400:])
        _save_cache(cache)
    except OSError as e:
        print(f'警告: quality_cache 写入失败: {e}', file=sys.stderr)


def score_results(query, results, mode='heuristic', use_cache=True, api_key=None):
    """评分搜索结果。

    Args:
        query: 原始查询字符串
        results: 搜索结果列表 [{'title', 'url', 'snippet', 'source'}, ...]
        mode: 'heuristic' | 'flash'
        use_cache: 是否使用缓存（默认 True）
        api_key: DeepSeek API key（None 则从环境变量读取）

    Returns:
        {
            'score': float,  # 0-10
            'dimensions': {
                'relevance': float,
                'authoritativeness': float,
                'freshness': float,
                'diversity': float,
            },
            'mode': str,  # 'heuristic' | 'flash' | 'flash-fallback-heuristic'
            'rationale': str,
            'cached': bool,  # 仅缓存命中时存在
            'cached_at': str,  # 仅缓存命中时存在
            'error': str,  # 仅失败降级时存在
        }
    """
    if not isinstance(query, str) or not query:
        return {
            'score': 0.0,
            'dimensions': {'relevance': 0, 'authoritativeness': 0,
                           'freshness': 0, 'diversity': 0},
            'mode': mode,
            'rationale': 'Empty query',
        }

    if not isinstance(results, list) or not results:
        return {
            'score': 0.0,
            'dimensions': {'relevance': 0, 'authoritativeness': 0,
                           'freshness': 0, 'diversity': 0},
            'mode': mode,
            'rationale': 'No results to score',
        }

    # 缓存查询（flash 模式才缓存，heuristic 太快不值得缓存）
    if use_cache and mode == 'flash':
        cached = _cache_get(query, results)
        if cached is not None:
            return cached

    # heuristic 模式直接评分
    if mode == 'heuristic':
        return _heuristic_score(query, results)

    # flash 模式：尝试 LLM，失败降级到 heuristic
    if mode == 'flash':
        try:
            result = _call_flash_api(query, results, api_key=api_key)
            if use_cache:
                try:
                    _cache_store(query, results, result)
                except Exception:
                    pass  # 缓存失败不影响返回
            return result
        except Exception as e:
            # 降级到 heuristic
            print(f'警告: Flash 评分失败，降级到 heuristic ({e})',
                  file=sys.stderr)
            fallback = _heuristic_score(query, results)
            fallback['mode'] = 'flash-fallback-heuristic'
            fallback['error'] = str(e)[:200]
            return fallback

    # 未知模式
    return _heuristic_score(query, results)


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='搜索结果质量评分 — 双轨模式（heuristic + flash）'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # score 子命令
    p_score = sub.add_parser('score', help='评分搜索结果')
    p_score.add_argument('--query', required=True, help='原始查询')
    p_score.add_argument('--results-json', required=True,
                         help='搜索结果 JSON 数组')
    p_score.add_argument('--mode', default='heuristic',
                         choices=['heuristic', 'flash'],
                         help='评分模式（默认 heuristic）')
    p_score.add_argument('--no-cache', action='store_true',
                         help='不使用缓存')

    # cache-clean 子命令
    sub.add_parser('cache-clean', help='清理过期评分缓存')

    args = parser.parse_args()

    if args.command == 'score':
        try:
            results = json.loads(args.results_json)
            if not isinstance(results, list):
                print('错误: --results-json 必须是 JSON 数组', file=sys.stderr)
                return 1
        except json.JSONDecodeError as e:
            print(f'错误: JSON 解析失败: {e}', file=sys.stderr)
            return 1

        result = score_results(
            query=args.query,
            results=results,
            mode=args.mode,
            use_cache=not args.no_cache,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == 'cache-clean':
        cache = _load_cache()
        if not cache:
            print('评分缓存为空')
            return 0
        now = time.time()
        expired = [k for k, v in cache.items()
                   if now - v.get('cached_at', 0) > CACHE_TTL]
        for k in expired:
            cache.pop(k, None)
        _save_cache(cache)
        print(f'已清理 {len(expired)} 条过期评分缓存，剩余 {len(cache)} 条')
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
