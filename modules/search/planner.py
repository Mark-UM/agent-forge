#!/usr/bin/env python3
"""MindSearch-inspired Planner — 复杂查询分解为子查询。

设计原则：
- 单文件独立模块，零外部依赖
- 失败时降级到原始查询（不阻塞主搜索流程）
- 输出严格 JSON，便于 agent 解析
- 成本预算：单次 < $0.001

Design Philosophy (from MindSearch/InternLM):
- Atomic: 子查询聚焦单一知识点
- Searchable: 单次 MCP 调用即可回答
- Independent: 子查询之间无依赖（可并行）
- Non-compound: 比较 A/B/C 拆为多个独立查询

Usage:
  python planner.py plan --query "..."
  python planner.py plan --query "..." --json
  python -m modules.search.tests.test_planner
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
    def _redact_outbound(query):
        return query, {'redacted_count': 0}

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROMPT_FILE = os.path.join(_PROJECT_ROOT, '.opencode', 'prompts', 'web-planner.md')

# DeepSeek API（同 quality.py 配置）
_FLASH_API = 'https://api.deepseek.com/v1/chat/completions'
_FLASH_MODEL = 'deepseek-chat'
_PRO_MODEL = 'deepseek-reasoner'  # Pro 模型，质量更高但成本更高

# Planner 模式 → 模型映射（spec §3.2 --planner-mode 参数）
PLANNER_MODES = {
    'flash': _FLASH_MODEL,
    'pro': _PRO_MODEL,
}
# Phase 2 Decision 1: 默认从 'flash' 迁移到 'pro'
# 原因：planner 是深度搜索的入口点，质量直接影响后续 14 步 pipeline。
# Flash 在 planner 任务上质量塌方（命名漂移、分解不充分），Pro 是正确选择。
# 用户约束：核心/大部分任务用 Pro，剩余用 Flash。Planner 属于核心。
DEFAULT_PLANNER_MODE = 'pro'

# 子查询上限（成本控制）
# MAX_SUB_QUERIES 是默认值（CLI 不传 --max-subqueries 时使用）。
# HARD_MAX_SUB_QUERIES 是绝对安全上限：caller 传入更大的值会被 clamp 到此值并打 warning。
# 这样 --deep-research（max_subqueries=10）能被正确尊重，同时防止恶意/误传超大值。
MAX_SUB_QUERIES = 5
HARD_MAX_SUB_QUERIES = 10
MIN_SUB_QUERIES = 1


def _load_prompt_template():
    """加载 web-planner.md prompt 模板。

    Returns:
        str: prompt 文本（失败返回默认精简版）
    """
    try:
        with open(_PROMPT_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError:
        # 兜底：返回精简版 prompt
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
    """构造 DeepSeek chat messages。

    Args:
        user_query: 用户原始查询
        prompt_template: 从 web-planner.md 加载的 prompt 文本

    Returns:
        list[dict]: OpenAI-compatible messages
    """
    # system message = prompt 模板
    # user message = 实际查询
    return [
        {'role': 'system', 'content': prompt_template},
        {'role': 'user', 'content': f'Decompose this query: {user_query}'},
    ]


def _call_planner_api(user_query, api_key=None, timeout=30,
                      planner_mode=DEFAULT_PLANNER_MODE, max_subqueries=MAX_SUB_QUERIES):
    """调用 DeepSeek V4 Flash/Pro 做查询分解。

    失败抛异常，由调用方降级。

    Args:
        user_query: 用户原始查询
        api_key: DeepSeek API key（None 则从环境变量读取）
        timeout: 超时秒数
        planner_mode: 'flash' (默认，成本低) 或 'pro' (高质量)
        max_subqueries: 子查询上限（1-5，spec §3.2 --max-subqueries 参数）

    Returns:
        dict: 解析后的 JSON {
            intent, complexity, decompose, sub_queries, rationale
        }
    """
    if api_key is None:
        # 仅使用 DEEPSEEK_API_KEY，避免将 Anthropic token 误发给 DeepSeek
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')

    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY 未设置（请勿使用 ANTHROPIC_AUTH_TOKEN 作为 DeepSeek 凭证）')

    if not isinstance(user_query, str) or not user_query.strip():
        raise ValueError('user_query 不能为空')

    # 校验 planner_mode
    if planner_mode not in PLANNER_MODES:
        raise ValueError(f'Invalid planner_mode: {planner_mode}. '
                         f'Must be one of: {list(PLANNER_MODES.keys())}')

    # 校验 max_subqueries
    # S6 fix: 不再用 MAX_SUB_QUERIES(=5) 作为硬 clamp，而是用 HARD_MAX_SUB_QUERIES(=10)。
    # 这样 caller（如 --deep-research 的 max_subqueries=10）能被正确尊重。
    # 仅当超过 HARD_MAX_SUB_QUERIES 时才 clamp 并打印 warning。
    if not isinstance(max_subqueries, int) or max_subqueries < MIN_SUB_QUERIES:
        max_subqueries = MIN_SUB_QUERIES
    elif max_subqueries > HARD_MAX_SUB_QUERIES:
        print(f"警告: max_subqueries={max_subqueries} 超过 HARD_MAX_SUB_QUERIES="
              f"{HARD_MAX_SUB_QUERIES}, 已 clamp 到 {HARD_MAX_SUB_QUERIES}",
              file=sys.stderr)
        max_subqueries = HARD_MAX_SUB_QUERIES

    # 出境 PII 脱敏：Planner prompt 不应含 PII
    redacted_query, redact_meta = _redact_outbound(user_query)
    if redact_meta.get('redacted_count', 0) > 0:
        print(f"警告: Planner 出境前 PII 脱敏 {redact_meta['redacted_count']} 处",
              file=sys.stderr)

    prompt_template = _load_prompt_template()
    # 注入 max_subqueries 到 prompt（动态覆盖默认 5）
    prompt_template = prompt_template.replace(
        'Maximum 5 sub-queries',
        f'Maximum {max_subqueries} sub-queries')
    prompt_template = prompt_template.replace(
        'Max 5 sub-queries',
        f'Max {max_subqueries} sub-queries')

    messages = _build_planner_messages(redacted_query, prompt_template)

    model = PLANNER_MODES[planner_mode]
    # Pro 模型需要更多 token 做推理
    max_tokens = 800 if planner_mode == 'pro' else 500

    payload = {
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Planner API HTTP {e.code}: '
                           f'{e.read().decode("utf-8", errors="replace")[:200]}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Planner API URL error: {e.reason}')
    except TimeoutError:
        raise RuntimeError(f'Planner API timeout after {timeout}s')

    try:
        text = data['choices'][0]['message']['content'].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f'Planner API response structure invalid: {e}')

    # 去除可能的 markdown fences
    text = _strip_markdown_fences(text)

    # S6 fix: 把 max_subqueries 传给 _parse_planner_json，让 caller 的值真正生效。
    # 之前 _parse_planner_json(text) 用默认 5，导致 _call_planner_api 的 clamp 结果被忽略。
    return _parse_planner_json(text, max_subqueries=max_subqueries)


def _strip_markdown_fences(text):
    """去除模型输出可能包裹的 markdown fences。"""
    if not text:
        return text

    text = text.strip()

    # 处理 ```json ... ``` 或 ``` ... ```
    if text.startswith('```'):
        lines = text.split('\n')
        # 去首行
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        # 去末行 ```
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()

    return text


def _parse_planner_json(text, max_subqueries=MAX_SUB_QUERIES):
    """解析 Planner 输出的 JSON，做字段验证。

    Args:
        text: 模型输出的文本（已去除 markdown fences）
        max_subqueries: 子查询上限（动态传入，默认 MAX_SUB_QUERIES=5）。
            S6 fix: 不再硬限制为 5，可接受 caller 传入的更大值（如 10 用于
            --deep-research）。HARD_MAX_SUB_QUERIES=10 由 _call_planner_api 在
            调用前 clamp，本函数信任传入值。

    Returns:
        dict: {
            intent, complexity, decompose, sub_queries, rationale
        }

    Raises:
        RuntimeError: JSON 解析失败或字段缺失
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'Planner JSON parse failed: {e}. '
                           f'Raw text: {text[:200]}')

    if not isinstance(parsed, dict):
        raise RuntimeError(f'Planner JSON is not dict: {type(parsed).__name__}')

    # 字段验证 + 默认值
    sub_queries = parsed.get('sub_queries', [])
    if not isinstance(sub_queries, list):
        raise RuntimeError(f'sub_queries is not list: {type(sub_queries).__name__}')

    # 兼容两种格式（spec §4.4 决策）：
    # - list[str] (实际简化版，当前 prompt 输出)
    # - list[dict] (spec 原始设计，含 query/priority/rationale)
    normalized_queries = []
    for q in sub_queries[:max_subqueries]:
        if isinstance(q, dict):
            # spec 格式：{query, priority, rationale} → 取 query 字段
            query_text = q.get('query', '')
            if query_text:
                normalized_queries.append(str(query_text))
        elif q:
            # 简化格式：直接是 string
            normalized_queries.append(str(q))
    sub_queries = normalized_queries

    # 如果 sub_queries 为空，使用原查询作为兜底
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
    """对用户查询做 Planner 分解。

    降级链：
    1. 调用 Planner API
    2. 失败（网络/JSON/字段错误）→ 返回原始查询作为单一子查询
    3. 始终返回有效结果，绝不抛异常

    Args:
        user_query: 用户原始查询
        api_key: API key（None 从环境变量读）
        timeout: API 超时
        planner_mode: 'flash' (默认) 或 'pro' (spec §3.2 --planner-mode)
        max_subqueries: 子查询上限 1-5 (spec §3.2 --max-subqueries)

    Returns:
        dict: {
            'success': bool,  # Planner 是否成功
            'intent': str,
            'complexity': str,
            'decompose': bool,
            'sub_queries': list[str],  # 始终非空
            'rationale': str,
            'mode': str,  # 'planner' | 'fallback-original'
            'planner_mode': str,  # 使用的模式（flash/pro）
            'max_subqueries': int,  # 使用的上限
            'error': str,  # 仅失败时存在
            'original_query': str,
        }
    """
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
        # 降级：返回原始查询作为单一子查询
        print(f'警告: Planner 失败，降级到原始查询 ({e})', file=sys.stderr)
        return {
            'success': False,
            'intent': 'unknown',
            'complexity': 'unknown',
            'decompose': False,
            'sub_queries': [user_query],  # 原始查询
            'rationale': f'Planner failed, using original query',
            'mode': 'fallback-original',
            'planner_mode': planner_mode,
            'max_subqueries': max_subqueries,
            'error': str(e)[:200],
            'original_query': user_query,
        }


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='MindSearch-inspired Planner — 复杂查询分解为子查询'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # plan 子命令
    p_plan = sub.add_parser('plan', help='分解复杂查询')
    p_plan.add_argument('--query', required=True, help='用户原始查询')
    p_plan.add_argument('--json', action='store_true',
                        help='输出 JSON 格式（默认文本）')
    p_plan.add_argument('--timeout', type=int, default=30,
                        help='API 超时秒数（默认 30）')
    # spec §3.2: --planner-mode 参数
    p_plan.add_argument('--planner-mode', choices=list(PLANNER_MODES.keys()),
                        default=DEFAULT_PLANNER_MODE,
                        help=f'Planner 模型选择（默认 {DEFAULT_PLANNER_MODE}）')
    # spec §3.2: --max-subqueries 参数
    # S6 fix: choices 允许 1-HARD_MAX_SUB_QUERIES(=10)，以支持 --deep-research 场景。
    p_plan.add_argument('--max-subqueries', type=int,
                        default=MAX_SUB_QUERIES,
                        choices=range(MIN_SUB_QUERIES, HARD_MAX_SUB_QUERIES + 1),
                        help=f'子查询上限 {MIN_SUB_QUERIES}-{HARD_MAX_SUB_QUERIES}'
                             f'（默认 {MAX_SUB_QUERIES}）')

    # prompt 子命令：打印加载的 prompt 模板
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
            print(f"## Planner 结果\n")
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
                print(f"已降级到原始查询作为单一子查询")
        return 0

    if args.command == 'show-prompt':
        prompt = _load_prompt_template()
        print(prompt)
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
