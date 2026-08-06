#!/usr/bin/env python3
"""MindSearch-inspired Planner — decompose complex queries into sub-queries.

The public CLI and fallback contract remain compatible, while all model policy,
redaction, timeouts, and result semantics are routed through Model Gateway.
"""
from __future__ import annotations

import sys
import os
import json
import argparse
import re
import urllib.request

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
_PROMPT_FILE = os.path.join(_PROJECT_ROOT, '.opencode', 'prompts', 'web-planner.md')

_FLASH_MODEL = 'deepseek-chat'
_PRO_MODEL = 'deepseek-reasoner'
PLANNER_MODES = {
    'flash': _FLASH_MODEL,
    'pro': _PRO_MODEL,
}
DEFAULT_PLANNER_MODE = 'pro'

MAX_SUB_QUERIES = 5
HARD_MAX_SUB_QUERIES = 10
MIN_SUB_QUERIES = 1


def _load_prompt_template():
    """Load the planner prompt, with a compact built-in fallback."""
    try:
        with open(_PROMPT_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError:
        return """You are a search query planner. Decompose a complex query into ≤5 atomic sub-queries.

Output JSON only (no markdown fences):
{
  "intent": "<factual|comparative|technical|news|academic|opinion>",
  "complexity": "<simple|medium|complex>",
  "decompose": <true|false>,
  "sub_queries": ["<sub-query 1>", ...],
  "rationale": "<one sentence>"
}

If query is atomic, set "decompose": false and "sub_queries": [original_query].
Max 5 sub-queries. Output JSON only."""


def _build_planner_messages(user_query, prompt_template):
    return [
        {'role': 'system', 'content': prompt_template},
        {'role': 'user', 'content': f'Decompose this query: {user_query}'},
    ]


def _gateway_error(error: str | None) -> RuntimeError:
    text = str(error or 'unknown model error')
    match = re.search(r'HTTP Error (\d+):?\s*(.*)', text)
    if match:
        return RuntimeError(
            f"Planner API HTTP {match.group(1)}: {match.group(2)}".rstrip()
        )
    if 'urlopen error' in text.lower() or 'url error' in text.lower():
        return RuntimeError(f'Planner API URL error: {text}')
    if 'timed out' in text.lower() or 'timeout' in text.lower():
        return RuntimeError(f'Planner API timeout: {text}')
    return RuntimeError(f'Planner API failed: {text}')


def _call_planner_api(user_query, api_key=None, timeout=30,
                      planner_mode=DEFAULT_PLANNER_MODE, max_subqueries=MAX_SUB_QUERIES):
    """Use Model Gateway to decompose a query; raise on model failure."""
    if api_key is None:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY 未设置（请勿使用 ANTHROPIC_AUTH_TOKEN 作为 DeepSeek 凭证）')
    if not isinstance(user_query, str) or not user_query.strip():
        raise ValueError('user_query 不能为空')
    if planner_mode not in PLANNER_MODES:
        raise ValueError(f'Invalid planner_mode: {planner_mode}. '
                         f'Must be one of: {list(PLANNER_MODES.keys())}')

    if not isinstance(max_subqueries, int) or max_subqueries < MIN_SUB_QUERIES:
        max_subqueries = MIN_SUB_QUERIES
    elif max_subqueries > HARD_MAX_SUB_QUERIES:
        print(f"警告: max_subqueries={max_subqueries} 超过 HARD_MAX_SUB_QUERIES="
              f"{HARD_MAX_SUB_QUERIES}, 已 clamp 到 {HARD_MAX_SUB_QUERIES}",
              file=sys.stderr)
        max_subqueries = HARD_MAX_SUB_QUERIES

    redacted_query, redact_meta = _redact_outbound(user_query)
    if redact_meta.get('redacted_count', 0) > 0:
        print(f"警告: Planner 出境前 PII 脱敏 {redact_meta['redacted_count']} 处",
              file=sys.stderr)

    prompt_template = _load_prompt_template()
    prompt_template = prompt_template.replace(
        'Maximum 5 sub-queries', f'Maximum {max_subqueries} sub-queries')
    prompt_template = prompt_template.replace(
        'Max 5 sub-queries', f'Max {max_subqueries} sub-queries')
    messages = _build_planner_messages(redacted_query, prompt_template)
    model = PLANNER_MODES[planner_mode]
    max_tokens = 800 if planner_mode == 'pro' else 500

    response = invoke_with_urlopen(
        task_type='planner',
        messages=messages,
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        temperature=0.1,
        timeout_seconds=float(timeout),
        max_retries=0,
        metadata={'caller': 'search.planner', 'max_subqueries': max_subqueries},
        record_run=False,
    )
    if not response.success:
        raise _gateway_error(response.error)

    text = _strip_markdown_fences(response.content)
    return _parse_planner_json(text, max_subqueries=max_subqueries)


def _strip_markdown_fences(text):
    if not text:
        return text
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    return text


def _parse_planner_json(text, max_subqueries=MAX_SUB_QUERIES):
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Planner JSON parse failed: {e}. '
                           f'Raw text: {text[:200]}')

    if not isinstance(parsed, dict):
        raise RuntimeError(f'Planner JSON is not dict: {type(parsed).__name__}')

    sub_queries = parsed.get('sub_queries', [])
    if not isinstance(sub_queries, list):
        raise RuntimeError(f'sub_queries is not list: {type(sub_queries).__name__}')

    normalized_queries = []
    for q in sub_queries[:max_subqueries]:
        if isinstance(q, dict):
            query_text = q.get('query', '')
            if query_text:
                normalized_queries.append(str(query_text))
        elif q:
            normalized_queries.append(str(q))
    sub_queries = normalized_queries
    if not sub_queries:
        raise RuntimeError('sub_queries is empty')

    intent = str(parsed.get('intent', 'unknown')).lower()
    complexity = str(parsed.get('complexity', 'unknown')).lower()
    decompose = bool(parsed.get('decompose', len(sub_queries) > 1))
    rationale = str(parsed.get('rationale', ''))[:200]

    return {
        'intent': intent,
        'complexity': complexity,
        'decompose': decompose,
        'sub_queries': sub_queries,
        'rationale': rationale,
    }


def plan_query(user_query, api_key=None, timeout=30,
               planner_mode=DEFAULT_PLANNER_MODE, max_subqueries=MAX_SUB_QUERIES):
    """Plan a query and always return a usable fallback result."""
    if not isinstance(user_query, str) or not user_query.strip():
        return {
            'success': False,
            'intent': 'unknown',
            'complexity': 'unknown',
            'decompose': False,
            'sub_queries': [],
            'rationale': 'Empty query',
            'mode': 'fallback-empty',
            'planner_mode': planner_mode,
            'max_subqueries': max_subqueries,
            'error': 'user_query is empty',
            'original_query': user_query if isinstance(user_query, str) else '',
        }

    try:
        result = _call_planner_api(
            user_query, api_key=api_key, timeout=timeout,
            planner_mode=planner_mode, max_subqueries=max_subqueries)
        return {
            'success': True,
            'intent': result['intent'],
            'complexity': result['complexity'],
            'decompose': result['decompose'],
            'sub_queries': result['sub_queries'],
            'rationale': result['rationale'],
            'mode': 'planner',
            'planner_mode': planner_mode,
            'max_subqueries': max_subqueries,
            'original_query': user_query,
        }
    except Exception as e:
        print(f'警告: Planner 失败，降级到原始查询 ({e})', file=sys.stderr)
        return {
            'success': False,
            'intent': 'unknown',
            'complexity': 'unknown',
            'decompose': False,
            'sub_queries': [user_query],
            'rationale': 'Planner failed, using original query',
            'mode': 'fallback-original',
            'planner_mode': planner_mode,
            'max_subqueries': max_subqueries,
            'error': str(e)[:200],
            'original_query': user_query,
        }


def _cli():
    parser = argparse.ArgumentParser(
        description='MindSearch-inspired Planner — 复杂查询分解为子查询'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_plan = sub.add_parser('plan', help='分解复杂查询')
    p_plan.add_argument('--query', required=True, help='用户原始查询')
    p_plan.add_argument('--json', action='store_true', help='输出 JSON 格式（默认文本）')
    p_plan.add_argument('--timeout', type=int, default=30, help='API 超时秒数（默认 30）')
    p_plan.add_argument('--planner-mode', choices=list(PLANNER_MODES.keys()),
                        default=DEFAULT_PLANNER_MODE,
                        help=f'Planner 模型选择（默认 {DEFAULT_PLANNER_MODE}）')
    p_plan.add_argument('--max-subqueries', type=int,
                        default=MAX_SUB_QUERIES,
                        choices=range(MIN_SUB_QUERIES, HARD_MAX_SUB_QUERIES + 1),
                        help=f'子查询上限 {MIN_SUB_QUERIES}-{HARD_MAX_SUB_QUERIES}'
                             f'（默认 {MAX_SUB_QUERIES}）')
    sub.add_parser('show-prompt', help='打印当前加载的 Planner prompt 模板')

    args = parser.parse_args()
    if args.command == 'plan':
        result = plan_query(args.query,
                            timeout=args.timeout,
                            planner_mode=args.planner_mode,
                            max_subqueries=args.max_subqueries)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("## Planner 结果\n")
            print(f"原始查询: {result['original_query']}")
            print(f"模式: {result['mode']}")
            print(f"Planner 模型: {result['planner_mode']}")
            print(f"子查询上限: {result['max_subqueries']}")
            print(f"意图: {result['intent']}")
            print(f"复杂度: {result['complexity']}")
            print(f"是否分解: {result['decompose']}")
            print(f"理由: {result['rationale']}")
            print(f"\n## 子查询列表（{len(result['sub_queries'])} 个）")
            for i, sq in enumerate(result['sub_queries'], 1):
                print(f"  {i}. {sq}")
            if not result['success']:
                print(f"\n警告: Planner 失败 ({result.get('error', '')})")
                print("已降级到原始查询作为单一子查询")
        return 0

    if args.command == 'show-prompt':
        print(_load_prompt_template())
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(_cli())
