#!/usr/bin/env python3
"""MindSearch-inspired Result Aggregator.

Multiple provider results are synthesized into structured Markdown. Model
policy, redaction, timeout and result semantics are centralized in Model
Gateway; failures retain the existing deterministic Markdown fallback.
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
    def _redact_outbound(text):
        return text, {'redacted_count': 0}

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROMPT_FILE = os.path.join(_PROJECT_ROOT, '.opencode', 'prompts', 'result-aggregator.md')
_FLASH_MODEL = 'deepseek-chat'

MAX_RESULTS_PER_SUBQUERY = 5
MAX_TOTAL_RESULTS = 20
MAX_SUBQUERIES = 5
MAX_SNIPPET_LEN = 200
MAX_TITLE_LEN = 100
MAX_URL_LEN = 200
MAX_OUTPUT_TOKENS = 800


def _load_prompt_template():
    try:
        with open(_PROMPT_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError:
        return """You are a Result Aggregator. Synthesize multiple search results into a structured Markdown answer.

Output sections:
## 综合答案
### 核心发现 (3-5 points, each with source URL)
### 详细分析 (200-500 words, with sources)
### 来源列表 (numbered, max 10)
### 置信度 (高/中/低 + reason)

Rules: cite sources, mark contradictions, no fabrication, match query language."""


def _truncate(text, max_len):
    if not isinstance(text, str):
        return ''
    if max_len <= 0:
        return ''
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[:max_len - 3] + '...'


def _format_results_for_prompt(original_query, sub_queries_with_results):
    lines = [
        f'Original query: {original_query}',
        '',
        'Sub-queries and their results:',
        '',
    ]
    total_shown = 0
    valid_idx = 0
    for sq in sub_queries_with_results[:MAX_SUBQUERIES]:
        if not isinstance(sq, dict):
            continue
        valid_idx += 1
        if total_shown >= MAX_TOTAL_RESULTS:
            lines.append('(more results truncated for cost)')
            break
        sub_q = _truncate(sq.get('sub_query', ''), MAX_TITLE_LEN)
        results = sq.get('results', [])
        if not isinstance(results, list):
            results = []
        lines.extend([f'## Sub-query {valid_idx}: {sub_q}', ''])
        shown = 0
        for result in results[:MAX_RESULTS_PER_SUBQUERY]:
            if not isinstance(result, dict):
                continue
            if total_shown >= MAX_TOTAL_RESULTS:
                lines.append('  (more results truncated for cost)')
                break
            title = _truncate(result.get('title', ''), MAX_TITLE_LEN)
            url = _truncate(result.get('url', ''), MAX_URL_LEN)
            snippet = _truncate(result.get('snippet', ''), MAX_SNIPPET_LEN)
            source = result.get('source', 'unknown')
            lines.extend([
                f'  - Title: {title}',
                f'    URL: {url}',
                f'    Snippet: {snippet}',
                f'    Source: {source}',
                '',
            ])
            shown += 1
            total_shown += 1
        if shown == 0:
            lines.extend(['  (no results for this sub-query)', ''])
    return '\n'.join(lines)


def _build_aggregator_messages(original_query, sub_queries_with_results,
                                prompt_template):
    results_text = _format_results_for_prompt(
        original_query, sub_queries_with_results)
    user_content = f"""Please aggregate the following search results into a structured Markdown answer.

{results_text}

Output the aggregated answer following the format in your system prompt."""
    return [
        {'role': 'system', 'content': prompt_template},
        {'role': 'user', 'content': user_content},
    ]


def _redact_all_inputs(original_query, sub_queries_with_results):
    redacted_query, query_meta = _redact_outbound(original_query)
    if query_meta.get('redacted_count', 0) > 0:
        print(f"警告: Aggregator 原始查询 PII 脱敏 {query_meta['redacted_count']} 处",
              file=sys.stderr)

    redacted_sub_queries = []
    for sq in sub_queries_with_results[:MAX_SUBQUERIES]:
        if not isinstance(sq, dict):
            continue
        sq_text = sq.get('sub_query', '')
        if isinstance(sq_text, str):
            sq_text, sq_meta = _redact_outbound(sq_text)
            if sq_meta.get('redacted_count', 0) > 0:
                print(f"警告: Aggregator sub-query PII 脱敏 "
                      f"{sq_meta['redacted_count']} 处", file=sys.stderr)
        redacted_results = []
        for result in sq.get('results', []):
            if not isinstance(result, dict):
                continue
            redacted_result = dict(result)
            for field in ['title', 'snippet', 'url', 'source']:
                if field in redacted_result and isinstance(redacted_result[field], str):
                    redacted_result[field], _ = _redact_outbound(redacted_result[field])
            redacted_results.append(redacted_result)
        redacted_sub_queries.append({
            'sub_query': sq_text,
            'results': redacted_results,
        })
    return redacted_query, redacted_sub_queries


def _gateway_error(error, timeout):
    text = str(error or 'unknown model error')
    match = re.search(r'HTTP Error (\d+):?\s*(.*)', text)
    if match:
        return RuntimeError(
            f"Aggregator API HTTP {match.group(1)}: {match.group(2)}".rstrip()
        )
    lowered = text.lower()
    if 'timed out' in lowered or 'timeout' in lowered:
        return RuntimeError(f'Aggregator API timeout after {timeout}s')
    if 'urlopen error' in lowered or 'url error' in lowered:
        return RuntimeError(f'Aggregator API URL error: {text}')
    if 'no choices' in lowered or 'content is not a string' in lowered:
        return RuntimeError(f'Aggregator API response structure invalid: {text}')
    return RuntimeError(f'Aggregator API failed: {text}')


def _call_aggregator_api(original_query, sub_queries_with_results,
                          api_key=None, timeout=45):
    if api_key is None:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        raise RuntimeError(
            'DEEPSEEK_API_KEY 未设置（请勿使用 ANTHROPIC_AUTH_TOKEN 作为 DeepSeek 凭证）')
    if not isinstance(original_query, str) or not original_query.strip():
        raise ValueError('original_query 不能为空')
    if not isinstance(sub_queries_with_results, list):
        raise ValueError('sub_queries_with_results 必须是 list')

    prompt_template = _load_prompt_template()
    messages = _build_aggregator_messages(
        original_query, sub_queries_with_results, prompt_template)
    response = invoke_with_urlopen(
        task_type='aggregator',
        messages=messages,
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=_FLASH_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.2,
        timeout_seconds=float(timeout),
        max_retries=0,
        metadata={'caller': 'search.aggregator'},
        record_run=False,
    )
    if not response.success:
        raise _gateway_error(response.error, timeout)
    text = response.content.strip()
    if not text:
        raise RuntimeError('Aggregator API returned empty content')
    return text


def _fallback_summary(original_query, sub_queries_with_results):
    lines = [
        '## 综合答案',
        '',
        '### 核心发现',
        '- （聚合服务暂不可用，以下为原始结果列表）',
        '',
        '### 详细分析',
        f'原始查询: {original_query}',
        '',
    ]
    total = 0
    valid_idx = 0
    for sq in sub_queries_with_results[:MAX_SUBQUERIES]:
        if not isinstance(sq, dict):
            continue
        valid_idx += 1
        if total >= MAX_TOTAL_RESULTS:
            lines.append('  (更多结果已截断)')
            break
        sub_q = _truncate(sq.get('sub_query', ''), MAX_TITLE_LEN)
        results = sq.get('results', [])
        if not isinstance(results, list):
            results = []
        lines.extend([f'#### Sub-query {valid_idx}: {sub_q}', ''])
        if not results:
            lines.extend(['  (无结果)', ''])
            continue
        for result in results[:MAX_RESULTS_PER_SUBQUERY]:
            if not isinstance(result, dict):
                continue
            if total >= MAX_TOTAL_RESULTS:
                lines.append('  (更多结果已截断)')
                break
            title = _truncate(result.get('title', ''), MAX_TITLE_LEN)
            url = _truncate(result.get('url', ''), MAX_URL_LEN)
            snippet = _truncate(result.get('snippet', ''), MAX_SNIPPET_LEN)
            lines.append(f'- [{title}]({url})')
            if snippet:
                lines.append(f'  {snippet}')
            total += 1
        lines.append('')
    lines.extend([
        '### 来源列表',
        '（见上方详细分析）',
        '',
        '### 置信度',
        '- 评级：中',
        '- 理由：聚合服务降级，仅展示原始结果，未做语义综合',
    ])
    return '\n'.join(lines)


def aggregate_results(original_query, sub_queries_with_results,
                       api_key=None, timeout=45):
    if not isinstance(original_query, str) or not original_query.strip():
        return {
            'success': False,
            'mode': 'fallback-empty',
            'markdown': '## 综合答案\n\n### 核心发现\n- 原始查询为空，无法聚合\n',
            'original_query': original_query if isinstance(original_query, str) else '',
            'sub_query_count': 0,
            'total_results': 0,
            'error': 'original_query is empty',
        }
    if not isinstance(sub_queries_with_results, list) or not sub_queries_with_results:
        return {
            'success': False,
            'mode': 'fallback-empty',
            'markdown': '## 综合答案\n\n### 核心发现\n- 无 sub-query 结果可聚合\n',
            'original_query': original_query,
            'sub_query_count': 0,
            'total_results': 0,
            'error': 'sub_queries_with_results is empty',
        }

    total_results = 0
    valid_sub_queries = 0
    for sq in sub_queries_with_results:
        if not isinstance(sq, dict):
            continue
        valid_sub_queries += 1
        results = sq.get('results', [])
        if isinstance(results, list):
            total_results += len(results)
    if total_results == 0:
        return {
            'success': False,
            'mode': 'fallback-empty',
            'markdown': '## 综合答案\n\n### 核心发现\n- 所有 sub-query 结果为空，无法聚合\n',
            'original_query': original_query,
            'sub_query_count': valid_sub_queries,
            'total_results': 0,
            'error': 'all sub-queries have empty results',
        }

    redacted_query, redacted_sub_queries = _redact_all_inputs(
        original_query, sub_queries_with_results)
    try:
        markdown = _call_aggregator_api(
            redacted_query, redacted_sub_queries,
            api_key=api_key, timeout=timeout)
        return {
            'success': True,
            'mode': 'aggregator',
            'markdown': markdown,
            'original_query': original_query,
            'sub_query_count': valid_sub_queries,
            'total_results': total_results,
        }
    except Exception as exc:
        print(f'警告: Aggregator 失败，降级到原始结果摘要 ({exc})',
              file=sys.stderr)
        return {
            'success': False,
            'mode': 'fallback-summary',
            'markdown': _fallback_summary(redacted_query, redacted_sub_queries),
            'original_query': original_query,
            'sub_query_count': valid_sub_queries,
            'total_results': total_results,
            'error': str(exc)[:200],
        }


def _cli():
    parser = argparse.ArgumentParser(
        description='MindSearch-inspired Result Aggregator — 多源结果聚合'
    )
    sub = parser.add_subparsers(dest='command', required=True)
    p_agg = sub.add_parser('aggregate', help='聚合多源结果')
    p_agg.add_argument('--original-query', required=True, help='原始用户查询')
    p_agg.add_argument('--sub-queries-file',
                       help='JSON 文件路径（包含 sub_queries_with_results）')
    p_agg.add_argument('--results-file',
                       help='JSON 文件路径（仅 results 列表，无 sub_query 分组）')
    p_agg.add_argument('--json', action='store_true',
                       help='输出 JSON 格式（默认纯 Markdown）')
    p_agg.add_argument('--timeout', type=int, default=45,
                       help='API 超时秒数（默认 45）')
    sub.add_parser('show-prompt', help='打印当前加载的 Aggregator prompt 模板')

    args = parser.parse_args()
    if args.command == 'aggregate':
        if args.sub_queries_file:
            try:
                with open(args.sub_queries_file, 'r', encoding='utf-8') as f:
                    sub_queries_with_results = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                print(f'错误: 无法读取 sub-queries 文件: {exc}', file=sys.stderr)
                return 1
        elif args.results_file:
            try:
                with open(args.results_file, 'r', encoding='utf-8') as f:
                    results = json.load(f)
                sub_queries_with_results = [{
                    'sub_query': args.original_query,
                    'results': results,
                }]
            except (OSError, json.JSONDecodeError) as exc:
                print(f'错误: 无法读取 results 文件: {exc}', file=sys.stderr)
                return 1
        else:
            try:
                data = json.loads(sys.stdin.read())
                if isinstance(data, list):
                    sub_queries_with_results = data
                elif isinstance(data, dict) and 'sub_queries_with_results' in data:
                    sub_queries_with_results = data['sub_queries_with_results']
                else:
                    print('错误: stdin JSON 格式不正确', file=sys.stderr)
                    return 1
            except json.JSONDecodeError as exc:
                print(f'错误: stdin JSON 解析失败: {exc}', file=sys.stderr)
                return 1

        result = aggregate_results(
            args.original_query, sub_queries_with_results,
            timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result['markdown'])
            if not result['success']:
                print(f"\n（降级模式: {result['mode']}, "
                      f"error: {result.get('error', '')})",
                      file=sys.stderr)
        return 0
    if args.command == 'show-prompt':
        print(_load_prompt_template())
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(_cli())
