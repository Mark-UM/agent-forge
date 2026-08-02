#!/usr/bin/env python3
"""MindSearch-inspired Result Aggregator — 多源结果聚合为结构化答案。

设计原则：
- 单文件独立模块，零外部依赖
- 失败时降级到原始结果列表的简单拼接（不阻塞主搜索流程）
- 输出 Markdown 格式（人类可读 + agent 可解析）
- 成本预算：单次 < $0.002（输入 ~1.5K tokens，输出 ~600 tokens）

Design Philosophy (from MindSearch Searcher):
- Synthesize: 多源结果语义聚合，不是机械合并
- Cite: 每个事实标注来源 URL
- Distinguish: 矛盾信息明确指出
- Confidence: 显式标注置信度

Usage:
  python aggregator.py aggregate \
    --original-query "React RSC vs SSR" \
    --sub-queries-file sub_queries.json \
    --results-file results.json

  python aggregator.py aggregate --json < input.json

  python -m modules.search.tests.test_aggregator
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error

# 导入 privacy 模块做出境 PII 脱敏
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

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROMPT_FILE = os.path.join(_PROJECT_ROOT, '.opencode', 'prompts', 'result-aggregator.md')

# DeepSeek API（同 planner.py / quality.py 配置）
_FLASH_API = 'https://api.deepseek.com/v1/chat/completions'
_FLASH_MODEL = 'deepseek-chat'

# Phase 2: import flash_guard for model resolution
# aggregator is in the Flash ALLOWED task list (utility, has mechanical fallback)
try:
    import sys as _sys
    _PROJECT_ROOT_FOR_GUARD = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PROJECT_ROOT_FOR_GUARD not in _sys.path:
        _sys.path.insert(0, _PROJECT_ROOT_FOR_GUARD)
    from modules.dispatch import guard as _flash_guard
    _HAS_FLASH_GUARD = True
except ImportError:
    _HAS_FLASH_GUARD = False

# 成本与限制
MAX_RESULTS_PER_SUBQUERY = 5     # 每个 sub-query 最多 5 条结果
MAX_TOTAL_RESULTS = 20           # 总结果上限（控制 token）
MAX_SUBQUERIES = 5               # sub-query 数量上限
MAX_SNIPPET_LEN = 200            # snippet 截断长度
MAX_TITLE_LEN = 100              # title 截断长度
MAX_URL_LEN = 200                # url 截断长度（防止超长 URL 撑爆 token 预算）
MAX_OUTPUT_TOKENS = 800          # 输出 token 上限


def _load_prompt_template():
    """加载 result-aggregator.md prompt 模板。

    Returns:
        str: prompt 文本（失败返回精简默认版）
    """
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
    """截断文本到 max_len，超长加省略号。"""
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
    """格式化 sub-queries + results 为 prompt 输入文本。

    Args:
        original_query: 原始查询（已脱敏）
        sub_queries_with_results: list[dict]，每项 {
            'sub_query': str,
            'results': list[dict]  # 每个 result: {title, url, snippet, source}
        }

    Returns:
        str: 格式化后的文本
    """
    lines = []
    lines.append(f'Original query: {original_query}')
    lines.append('')
    lines.append('Sub-queries and their results:')
    lines.append('')

    total_shown = 0
    valid_idx = 0  # 仅对有效 sub-query 递增，便于显示编号
    for sq in sub_queries_with_results[:MAX_SUBQUERIES]:
        if not isinstance(sq, dict):
            continue
        valid_idx += 1

        # 总结果数超限 → 截断并退出
        if total_shown >= MAX_TOTAL_RESULTS:
            lines.append('(more results truncated for cost)')
            break

        sub_q = _truncate(sq.get('sub_query', ''), MAX_TITLE_LEN)
        results = sq.get('results', [])
        if not isinstance(results, list):
            results = []

        lines.append(f'## Sub-query {valid_idx}: {sub_q}')
        lines.append('')

        shown = 0
        for r in results[:MAX_RESULTS_PER_SUBQUERY]:
            if not isinstance(r, dict):
                continue
            if total_shown >= MAX_TOTAL_RESULTS:
                lines.append('  (more results truncated for cost)')
                break

            title = _truncate(r.get('title', ''), MAX_TITLE_LEN)
            url = _truncate(r.get('url', ''), MAX_URL_LEN)
            snippet = _truncate(r.get('snippet', ''), MAX_SNIPPET_LEN)
            source = r.get('source', 'unknown')

            lines.append(f'  - Title: {title}')
            lines.append(f'    URL: {url}')
            lines.append(f'    Snippet: {snippet}')
            lines.append(f'    Source: {source}')
            lines.append('')
            shown += 1
            total_shown += 1

        if shown == 0:
            lines.append('  (no results for this sub-query)')
            lines.append('')

    return '\n'.join(lines)


def _build_aggregator_messages(original_query, sub_queries_with_results,
                                prompt_template):
    """构造 DeepSeek chat messages。

    Args:
        original_query: 原始查询（已脱敏）
        sub_queries_with_results: list[dict]
        prompt_template: prompt 文本

    Returns:
        list[dict]: OpenAI-compatible messages
    """
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
    """对所有输入做 Layer 0 PII 脱敏（统一入口，供 API + fallback 共享）。

    Args:
        original_query: 原始查询
        sub_queries_with_results: list[dict]

    Returns:
        tuple: (redacted_query: str, redacted_sub_queries: list[dict])
    """
    # 出境 PII 脱敏：原始查询和所有结果字段都做脱敏
    redacted_query, query_meta = _redact_outbound(original_query)
    if query_meta.get('redacted_count', 0) > 0:
        print(f"警告: Aggregator 原始查询 PII 脱敏 {query_meta['redacted_count']} 处",
              file=sys.stderr)

    # 对 sub_queries 和 results 也做 PII 脱敏（仅处理 MAX_SUBQUERIES 范围内）
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
        for r in sq.get('results', []):
            if not isinstance(r, dict):
                continue
            redacted_r = dict(r)
            for field in ['title', 'snippet', 'url', 'source']:
                if field in redacted_r and isinstance(redacted_r[field], str):
                    redacted_r[field], _ = _redact_outbound(redacted_r[field])
            redacted_results.append(redacted_r)

        redacted_sub_queries.append({
            'sub_query': sq_text,
            'results': redacted_results,
        })

    return redacted_query, redacted_sub_queries


def _call_aggregator_api(original_query, sub_queries_with_results,
                          api_key=None, timeout=45):
    """调用 DeepSeek V4 Flash 做结果聚合。

    失败抛异常，由调用方降级。

    Args:
        original_query: 原始查询（应已 PII 脱敏）
        sub_queries_with_results: list[dict]（应已 PII 脱敏）
        api_key: DeepSeek API key（None 从环境变量读）
        timeout: 超时秒数

    Returns:
        str: 聚合后的 Markdown 文本
    """
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

    # Phase 2: resolve model via guard (aggregator is allowlisted → Flash)
    if _HAS_FLASH_GUARD:
        model = _flash_guard.resolve_model(
            "aggregator", caller="aggregator._call_aggregator_api")
    else:
        model = _FLASH_MODEL
    payload = {
        'model': model,
        'messages': messages,
        'max_tokens': MAX_OUTPUT_TOKENS,
        'temperature': 0.2,  # 略高温度以获得更自然的综合
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')[:200]
        # 防御性：去除可能的 Bearer token 泄露
        import re
        body = re.sub(r'Bearer\s+[\w.-]+', 'Bearer [REDACTED]', body)
        raise RuntimeError(f'Aggregator API HTTP {e.code}: {body}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Aggregator API URL error: {e.reason}')
    except TimeoutError:
        raise RuntimeError(f'Aggregator API timeout after {timeout}s')

    try:
        text = data['choices'][0]['message']['content'].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise RuntimeError(f'Aggregator API response structure invalid: {e}')

    if not text:
        raise RuntimeError('Aggregator API returned empty content')

    return text


def _fallback_summary(original_query, sub_queries_with_results):
    """降级摘要：原始结果列表的简单拼接。

    Args:
        original_query: 原始查询
        sub_queries_with_results: list[dict]

    Returns:
        str: Markdown 格式的简单摘要
    """
    lines = []
    lines.append('## 综合答案')
    lines.append('')
    lines.append('### 核心发现')
    lines.append('- （聚合服务暂不可用，以下为原始结果列表）')
    lines.append('')
    lines.append('### 详细分析')
    lines.append(f'原始查询: {original_query}')
    lines.append('')

    total = 0
    valid_idx = 0
    for sq in sub_queries_with_results[:MAX_SUBQUERIES]:
        if not isinstance(sq, dict):
            continue
        valid_idx += 1

        # 总结果数超限 → 截断并退出
        if total >= MAX_TOTAL_RESULTS:
            lines.append('  (更多结果已截断)')
            break

        sub_q = _truncate(sq.get('sub_query', ''), MAX_TITLE_LEN)
        results = sq.get('results', [])
        if not isinstance(results, list):
            results = []

        lines.append(f'#### Sub-query {valid_idx}: {sub_q}')
        lines.append('')

        if not results:
            lines.append('  (无结果)')
            lines.append('')
            continue

        for r in results[:MAX_RESULTS_PER_SUBQUERY]:
            if not isinstance(r, dict):
                continue
            if total >= MAX_TOTAL_RESULTS:
                lines.append('  (更多结果已截断)')
                break
            title = _truncate(r.get('title', ''), MAX_TITLE_LEN)
            url = _truncate(r.get('url', ''), MAX_URL_LEN)
            snippet = _truncate(r.get('snippet', ''), MAX_SNIPPET_LEN)
            lines.append(f'- [{title}]({url})')
            if snippet:
                lines.append(f'  {snippet}')
            total += 1

        lines.append('')

    lines.append('### 来源列表')
    lines.append('（见上方详细分析）')
    lines.append('')
    lines.append('### 置信度')
    lines.append('- 评级：中')
    lines.append('- 理由：聚合服务降级，仅展示原始结果，未做语义综合')

    return '\n'.join(lines)


def aggregate_results(original_query, sub_queries_with_results,
                       api_key=None, timeout=45):
    """对多源搜索结果做聚合，生成结构化 Markdown 答案。

    降级链：
    1. 调用 Aggregator API（DeepSeek V4 Flash）
    2. 失败（网络/JSON/字段错误）→ 返回简单拼接的降级摘要
    3. 始终返回有效 Markdown，绝不抛异常

    Args:
        original_query: 用户原始查询
        sub_queries_with_results: list[dict] {
            'sub_query': str,
            'results': list[{title, url, snippet, source}]
        }
        api_key: API key（None 从环境变量读）
        timeout: API 超时

    Returns:
        dict: {
            'success': bool,         # Aggregator 是否成功
            'mode': str,              # 'aggregator' | 'fallback-summary' | 'fallback-empty'
            'markdown': str,          # 聚合后的 Markdown 文本
            'original_query': str,
            'sub_query_count': int,
            'total_results': int,
            'error': str,             # 仅失败时存在
        }
    """
    # 边界输入
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

    # 统计总结果数
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

    # 出境 PII 脱敏（统一入口，API + fallback 共享已脱敏数据）
    redacted_query, redacted_sub_queries = _redact_all_inputs(
        original_query, sub_queries_with_results)

    # 调用 Aggregator API（使用已脱敏数据）
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
    except Exception as e:
        # 降级到简单摘要（使用已脱敏数据，避免 PII 泄露到 fallback 路径）
        print(f'警告: Aggregator 失败，降级到原始结果摘要 ({e})',
              file=sys.stderr)
        fallback_md = _fallback_summary(redacted_query, redacted_sub_queries)
        return {
            'success': False,
            'mode': 'fallback-summary',
            'markdown': fallback_md,
            'original_query': original_query,
            'sub_query_count': valid_sub_queries,
            'total_results': total_results,
            'error': str(e)[:200],
        }


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='MindSearch-inspired Result Aggregator — 多源结果聚合'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # aggregate 子命令
    p_agg = sub.add_parser('aggregate', help='聚合多源结果')
    p_agg.add_argument('--original-query', required=True,
                       help='原始用户查询')
    p_agg.add_argument('--sub-queries-file',
                       help='JSON 文件路径（包含 sub_queries_with_results）')
    p_agg.add_argument('--results-file',
                       help='JSON 文件路径（仅 results 列表，无 sub_query 分组）')
    p_agg.add_argument('--json', action='store_true',
                       help='输出 JSON 格式（默认纯 Markdown）')
    p_agg.add_argument('--timeout', type=int, default=45,
                       help='API 超时秒数（默认 45）')

    # show-prompt 子命令
    sub.add_parser('show-prompt', help='打印当前加载的 Aggregator prompt 模板')

    args = parser.parse_args()

    if args.command == 'aggregate':
        # 构造 sub_queries_with_results
        if args.sub_queries_file:
            try:
                with open(args.sub_queries_file, 'r', encoding='utf-8') as f:
                    sub_queries_with_results = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(f'错误: 无法读取 sub-queries 文件: {e}', file=sys.stderr)
                return 1
        elif args.results_file:
            try:
                with open(args.results_file, 'r', encoding='utf-8') as f:
                    results = json.load(f)
                # 包装为单个 sub-query
                sub_queries_with_results = [{
                    'sub_query': args.original_query,
                    'results': results,
                }]
            except (OSError, json.JSONDecodeError) as e:
                print(f'错误: 无法读取 results 文件: {e}', file=sys.stderr)
                return 1
        else:
            # 从 stdin 读取 JSON
            try:
                stdin_data = sys.stdin.read()
                data = json.loads(stdin_data)
                if isinstance(data, list):
                    # 假设是 sub_queries_with_results
                    sub_queries_with_results = data
                elif isinstance(data, dict) and 'sub_queries_with_results' in data:
                    sub_queries_with_results = data['sub_queries_with_results']
                else:
                    print('错误: stdin JSON 格式不正确', file=sys.stderr)
                    return 1
            except json.JSONDecodeError as e:
                print(f'错误: stdin JSON 解析失败: {e}', file=sys.stderr)
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
        prompt = _load_prompt_template()
        print(prompt)
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
