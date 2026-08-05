#!/usr/bin/env python3
"""Search-result quality scoring with heuristic and Gateway model modes.

Heuristic scoring is local and free. Optional model scoring is routed through
Model Gateway and falls back to the heuristic result. The existing seven-day
quality-score cache remains a separate derived-data cache; production Search
result/provider caches are handled by the SQLite Search Cache Repository.
"""
from __future__ import annotations

import sys
import os
import json
import time
import re
import argparse
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

from modules.dispatch.compat import invoke_with_urlopen

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(query):
        return query, {'redacted_count': 0}

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, '_runtime', 'search')
CACHE_FILE = os.path.join(_CACHE_DIR, 'quality_cache.json')
CACHE_TTL = 7 * 24 * 3600

_AUTHORITY_MAP = {
    'official-docs': 3,
    'official_docs': 3,
    'official': 3,
    'handbook': 3,
    'moodle': 3,
    'instructor': 2,
    'github': 1,
    'personal': 1,
    'student-notes': 1,
    'gist': 1,
    'mailing-list': 2,
    'docs': 3,
    'reference': 3,
    'blog': 1,
    'medium': 1,
    'dev.to': 1,
    'forum': 1,
    'stackoverflow': 1,
    'stack-overflow': 1,
    'reddit': 1,
    'news': 1,
    'unknown': 0,
}

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

GITHUB_ORG_WHITELIST = {
    'python', 'microsoft', 'facebook', 'google', 'angular', 'vuejs',
    'rust-lang', 'golang', 'nodejs', 'kubernetes', 'apache',
    'opencv', 'tensorflow', 'pytorch',
}

_WHITELIST_FILE = os.path.join(
    _PROJECT_ROOT, 'markconfig', 'authority_whitelist.json'
)
_WHITELIST_CACHE = {'loaded': False, 'tiers': None, 'github_orgs': None}


def _load_authority_whitelist():
    if _WHITELIST_CACHE['loaded']:
        return _WHITELIST_CACHE['tiers'], _WHITELIST_CACHE['github_orgs']
    if not os.path.exists(_WHITELIST_FILE):
        _WHITELIST_CACHE.update({'loaded': True, 'tiers': None, 'github_orgs': None})
        return None, None
    try:
        with open(_WHITELIST_FILE, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        tiers = {}
        for tier_name in ('L1', 'L2', 'L3', 'L4'):
            if tier_name in data and isinstance(data[tier_name], dict):
                tier_data = data[tier_name]
                tiers[tier_name] = {
                    'domains': [d.lower() for d in tier_data.get('domains', [])
                                if isinstance(d, str)],
                    'source_fields': [s.lower() for s in tier_data.get('source_fields', [])
                                      if isinstance(s, str)],
                    'score': int(tier_data.get(
                        'score', AUTHORITY_TIERS[tier_name]['score'])),
                }
            else:
                tiers[tier_name] = AUTHORITY_TIERS[tier_name]
        github_orgs = set()
        if isinstance(data.get('github_org_whitelist'), list):
            github_orgs = {
                str(value).lower()
                for value in data['github_org_whitelist']
                if isinstance(value, str)
            }
        _WHITELIST_CACHE.update({
            'loaded': True,
            'tiers': tiers,
            'github_orgs': github_orgs,
        })
        return tiers, github_orgs
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f'警告: authority_whitelist.json 加载失败: {exc}', file=sys.stderr)
        _WHITELIST_CACHE.update({'loaded': True, 'tiers': None, 'github_orgs': None})
        return None, None


def _try_promote_github_org(url, github_orgs):
    if not github_orgs:
        return None
    try:
        path = urlparse(url).path.strip('/')
        if not path:
            return None
        org = path.split('/', 1)[0].lower()
        if org in github_orgs:
            return {
                'tier': 'L2',
                'score': 2,
                'reason': f'github org {org} in whitelist → promoted to L2',
            }
    except Exception:
        pass
    return None


def _classify_authority(url, source='unknown'):
    if not isinstance(url, str) or not url:
        return {'tier': 'unknown', 'score': 0, 'reason': 'empty url'}
    source = str(source or 'unknown').lower()
    tiers, github_orgs = _load_authority_whitelist()
    if tiers is None:
        tiers = AUTHORITY_TIERS
        github_orgs = GITHUB_ORG_WHITELIST

    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        netloc = ''
    if not netloc:
        for tier_name in ('L1', 'L2', 'L3', 'L4'):
            if source in tiers[tier_name]['source_fields']:
                return {
                    'tier': tier_name,
                    'score': tiers[tier_name]['score'],
                    'reason': f'source={source} → {tier_name}',
                }
        return {'tier': 'unknown', 'score': 0,
                'reason': 'no netloc, source not in tiers'}

    netloc_core = netloc[4:] if netloc.startswith('www.') else netloc
    best_match = None
    for tier_name in ('L1', 'L2', 'L3', 'L4'):
        for domain in tiers[tier_name]['domains']:
            if netloc == domain or netloc.endswith('.' + domain):
                if best_match is None or len(domain) > len(best_match[1]):
                    best_match = (tier_name, domain, tiers[tier_name]['score'])
    if best_match is not None:
        tier_name, domain, score = best_match
        if tier_name == 'L3' and netloc_core in ('github.com', 'gist.github.com'):
            promoted = _try_promote_github_org(url, github_orgs)
            if promoted:
                return promoted
        return {
            'tier': tier_name,
            'score': score,
            'reason': f'domain {netloc} matches {domain} → {tier_name}',
        }

    for tier_name in ('L1', 'L2', 'L3', 'L4'):
        if source in tiers[tier_name]['source_fields']:
            return {
                'tier': tier_name,
                'score': tiers[tier_name]['score'],
                'reason': f'source={source} → {tier_name}',
            }

    if netloc.endswith('.gov') or netloc.endswith('.edu'):
        return {'tier': 'L1', 'score': 3,
                'reason': f'.gov/.edu TLD → L1 ({netloc})'}
    if netloc.endswith('.org'):
        return {'tier': 'L2', 'score': 2,
                'reason': f'.org TLD → L2 ({netloc})'}
    if netloc.endswith('.dev') or netloc.endswith('.io') or netloc.endswith('.ai'):
        return {'tier': 'L2', 'score': 2,
                'reason': f'.dev/.io/.ai TLD → L2 ({netloc})'}
    if 'docs.' in netloc or 'developer.' in netloc:
        return {'tier': 'L1', 'score': 3,
                'reason': f'docs./developer. subdomain → L1 ({netloc})'}

    legacy_score = _AUTHORITY_MAP.get(source, 0)
    if legacy_score > 0:
        tier = 'L1' if legacy_score >= 3 else 'L2' if legacy_score == 2 else 'L4'
        return {'tier': tier, 'score': legacy_score,
                'reason': f'legacy _AUTHORITY_MAP[{source}]={legacy_score} → {tier}'}
    return {'tier': 'unknown', 'score': 0,
            'reason': f'no match for {netloc}, source={source}'}


_YEAR_PATTERN = re.compile(r'(20\d{2})')
_VALID_YEAR_MIN = 2010
_VALID_YEAR_MAX = datetime.now().year
_STOP_WORDS = {
    'the', 'a', 'an', 'of', 'and', 'or', 'in', 'on', 'to',
    'for', 'is', 'are', 'was', 'were', 'with', 'by', 'at',
    'from', 'as', 'that', 'this', 'these', 'those',
    '的', '了', '在', '是', '和', '与', '或', '对',
}


def _score_relevance(query, results):
    if not query or not results:
        return 0.0
    keywords = [
        word.lower()
        for word in re.split(r'\W+', query.strip())
        if word and len(word) >= 2 and word.lower() not in _STOP_WORDS
    ]
    if not keywords:
        return 2.0
    total_hits = 0
    for result in results:
        if not isinstance(result, dict):
            continue
        text = (
            f"{str(result.get('title', '')).lower()} "
            f"{str(result.get('snippet', '')).lower()}"
        )
        total_hits += sum(1 for keyword in keywords if keyword in text) / len(keywords)
    valid_count = sum(1 for result in results if isinstance(result, dict))
    if valid_count == 0:
        return 0.0
    return min(4.0, (total_hits / valid_count) * 4.0)


def _score_authoritativeness(results):
    if not results:
        return 0.0
    scores = []
    for result in results:
        if not isinstance(result, dict):
            continue
        url = str(result.get('url', ''))
        source = str(result.get('source', 'unknown'))
        try:
            score = _classify_authority(url, source)['score']
        except Exception:
            score = _AUTHORITY_MAP.get(source.lower(), 0)
            try:
                netloc = urlparse(url).netloc.lower()
                if netloc.endswith('.gov') or netloc.endswith('.edu'):
                    score = max(score, 3)
                elif netloc.endswith(('.org', '.dev', '.io', '.ai')):
                    score = max(score, 2)
                if 'docs.' in netloc or 'developer.' in netloc:
                    score = max(score, 3)
            except Exception:
                pass
        scores.append(score)
    if not scores:
        return 0.0
    return min(3.0, sum(scores) / len(scores))


def _score_freshness(results):
    if not results:
        return 0.0
    scores = []
    for result in results:
        if not isinstance(result, dict):
            continue
        text = f"{result.get('url', '')} {result.get('snippet', '')}"
        years = []
        for match in _YEAR_PATTERN.findall(text):
            try:
                year = int(match)
                if _VALID_YEAR_MIN <= year <= _VALID_YEAR_MAX:
                    years.append(year)
            except ValueError:
                continue
        if not years:
            scores.append(0.5)
            continue
        age = _VALID_YEAR_MAX - max(years)
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
    if not scores:
        return 0.0
    return min(2.0, sum(scores) / len(scores))


def _score_diversity(results):
    if not results:
        return 0.0
    domains = set()
    for result in results:
        if not isinstance(result, dict):
            continue
        try:
            netloc = urlparse(str(result.get('url', ''))).netloc.lower()
            if netloc:
                domains.add(netloc)
        except Exception:
            continue
    return min(1.0, len(domains) / len(results))


def _heuristic_score(query, results):
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


_FLASH_MODEL = 'deepseek-chat'
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
    redacted_query, _ = _redact_outbound(query)
    lines = []
    for index, result in enumerate(results[:10], 1):
        title = str(result.get('title', ''))[:100]
        snippet = str(result.get('snippet', ''))[:200]
        source = str(result.get('source', 'unknown'))
        url = str(result.get('url', ''))
        lines.append(
            f"{index}. {title} — {snippet} (source: {source}, url: {url})"
        )
    results_block = '\n'.join(lines) if lines else '(no results)'
    return _FLASH_PROMPT_TEMPLATE.format(
        query=redacted_query,
        results_block=results_block,
    )


def _gateway_error(error):
    text = str(error or 'unknown model error')
    match = re.search(r'HTTP Error (\d+):?\s*(.*)', text)
    if match:
        return RuntimeError(
            f"Flash API HTTP {match.group(1)}: {match.group(2)}".rstrip()
        )
    lowered = text.lower()
    if 'urlopen error' in lowered or 'url error' in lowered:
        return RuntimeError(f'Flash API URL error: {text}')
    if 'timeout' in lowered or 'timed out' in lowered:
        return RuntimeError(f'Flash API timeout: {text}')
    if 'no choices' in lowered or 'content is not a string' in lowered:
        return RuntimeError(f'Flash API response parse failed: {text}')
    return RuntimeError(f'Flash API failed: {text}')


def _call_flash_api(query, results, api_key=None):
    if api_key is None:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            'DEEPSEEK_API_KEY 未设置（请勿使用 ANTHROPIC_AUTH_TOKEN 作为 DeepSeek 凭证）'
        )

    response = invoke_with_urlopen(
        task_type='quality_scoring',
        messages=({'role': 'user', 'content': _build_flash_prompt(query, results)},),
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=_FLASH_MODEL,
        max_tokens=300,
        temperature=0.1,
        timeout_seconds=30.0,
        max_retries=0,
        metadata={'caller': 'search.quality'},
        record_run=False,
    )
    if not response.success:
        raise _gateway_error(response.error)

    try:
        text = response.content.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            if lines and lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            text = '\n'.join(lines).strip()
        parsed = json.loads(text)
        score = max(0.0, min(10.0, float(parsed.get('score', 0))))
        dimensions_raw = parsed.get('dimensions', {})
        dimensions = {
            'relevance': max(0.0, min(4.0, float(dimensions_raw.get('relevance', 0)))),
            'authoritativeness': max(
                0.0, min(3.0, float(dimensions_raw.get('authoritativeness', 0)))
            ),
            'freshness': max(0.0, min(2.0, float(dimensions_raw.get('freshness', 0)))),
            'diversity': max(0.0, min(1.0, float(dimensions_raw.get('diversity', 0)))),
        }
        return {
            'score': round(score, 2),
            'dimensions': dimensions,
            'mode': 'flash',
            'rationale': str(parsed.get('rationale', ''))[:200],
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError(f'Flash API response parse failed: {exc}') from exc


def _cache_key(query, results):
    import hashlib
    signature = [query.strip().lower()]
    for result in results[:10]:
        signature.append(str(result.get('url', '')))
        signature.append(str(result.get('title', ''))[:50])
    return hashlib.md5('|'.join(signature).encode('utf-8')).hexdigest()


def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f'警告: quality_cache 写入失败: {exc}', file=sys.stderr)


def _cache_get(query, results):
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
    result = dict(entry.get('result', {}))
    result['cached'] = True
    result['cached_at'] = datetime.fromtimestamp(cached_at).isoformat()
    return result


def _cache_store(query, results, score_result):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        redacted_query, _ = _redact_outbound(query)
        cache = _load_cache()
        key = _cache_key(query, results)
        cache[key] = {
            'query': redacted_query,
            'result': score_result,
            'cached_at': time.time(),
        }
        if len(cache) > 500:
            sorted_items = sorted(
                cache.items(), key=lambda item: item[1].get('cached_at', 0)
            )
            cache = dict(sorted_items[-400:])
        _save_cache(cache)
    except OSError as exc:
        print(f'警告: quality_cache 写入失败: {exc}', file=sys.stderr)


def score_results(query, results, mode='heuristic', use_cache=True, api_key=None):
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

    if use_cache and mode == 'flash':
        cached = _cache_get(query, results)
        if cached is not None:
            return cached
    if mode == 'heuristic':
        return _heuristic_score(query, results)
    if mode == 'flash':
        try:
            result = _call_flash_api(query, results, api_key=api_key)
            if use_cache:
                try:
                    _cache_store(query, results, result)
                except Exception:
                    pass
            return result
        except Exception as exc:
            print(f'警告: Flash 评分失败，降级到 heuristic ({exc})',
                  file=sys.stderr)
            fallback = _heuristic_score(query, results)
            fallback['mode'] = 'flash-fallback-heuristic'
            fallback['error'] = str(exc)[:200]
            return fallback
    return _heuristic_score(query, results)


def _cli():
    parser = argparse.ArgumentParser(
        description='搜索结果质量评分 — 双轨模式（heuristic + flash）'
    )
    sub = parser.add_subparsers(dest='command', required=True)
    p_score = sub.add_parser('score', help='评分搜索结果')
    p_score.add_argument('--query', required=True, help='原始查询')
    p_score.add_argument('--results-json', required=True,
                         help='搜索结果 JSON 数组')
    p_score.add_argument('--mode', default='heuristic',
                         choices=['heuristic', 'flash'],
                         help='评分模式（默认 heuristic）')
    p_score.add_argument('--no-cache', action='store_true', help='不使用缓存')
    sub.add_parser('cache-clean', help='清理过期评分缓存')
    args = parser.parse_args()

    if args.command == 'score':
        try:
            results = json.loads(args.results_json)
            if not isinstance(results, list):
                print('错误: --results-json 必须是 JSON 数组', file=sys.stderr)
                return 1
        except json.JSONDecodeError as exc:
            print(f'错误: JSON 解析失败: {exc}', file=sys.stderr)
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
        expired = [
            key for key, value in cache.items()
            if now - value.get('cached_at', 0) > CACHE_TTL
        ]
        for key in expired:
            cache.pop(key, None)
        _save_cache(cache)
        print(f'已清理 {len(expired)} 条过期评分缓存，剩余 {len(cache)} 条')
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(_cli())
