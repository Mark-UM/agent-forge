#!/usr/bin/env python3
"""自动 fetch Top URL + Flash 摘要。

设计原则：
- 并发 fetch（urllib + threading，标准库）
- Flash 模型生成 < 100 字摘要
- 失败降级到 snippet
- 出境前 PII 脱敏（URL 和内容）

降级链：
1. fetch URL 失败 → 跳过，summary = snippet
2. Flash 摘要失败 → summary = snippet（截断 100 字）
3. 所有 IO 异常兜底，绝不阻塞主流程

Usage:
  python summarize.py fetch --results-json '[...]'
  python summarize.py fetch --results-json '[...]' --mode flash
  python -m modules.search.tests.test_summarize
"""
import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 导入 privacy 模块做出境 PII 脱敏
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(query):
        return query, {'redacted_count': 0}

# ── Flash API 配置（同 quality.py / planner.py）──────────────
_FLASH_API = 'https://api.deepseek.com/v1/chat/completions'
_FLASH_MODEL = 'deepseek-chat'

# Phase 2: import flash_guard for model resolution
# summarize is in the Flash ALLOWED task list (utility, has fallback)
try:
    import sys as _sys
    _PROJECT_ROOT_FOR_GUARD = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PROJECT_ROOT_FOR_GUARD not in _sys.path:
        _sys.path.insert(0, _PROJECT_ROOT_FOR_GUARD)
    from modules.dispatch import guard as _flash_guard
    _HAS_FLASH_GUARD = True
except ImportError:
    _HAS_FLASH_GUARD = False

# fetch 配置
FETCH_TIMEOUT = 10  # 单 URL fetch 超时
MAX_CONTENT_LENGTH = 5000  # 单 URL 内容截断长度（字符）
MAX_WORKERS = 5  # 并发 fetch 线程数
MAX_URLS_TO_FETCH = 3  # 单次最多 fetch 的 URL 数（成本控制）

# User-Agent 避免被一些站点拒绝
_USER_AGENT = ('Mozilla/5.0 (compatible; OpenCodeSearchBot/4.1; '
               '+https://github.com/opencode-ai)')

_FLASH_PROMPT_TEMPLATE = """Summarize the following web page content in <100 Chinese characters.

URL: {url}
Title: {title}
Content (first 5000 chars):
{content}

Output: A single paragraph summary in Chinese, <100 characters. No markdown, no headers, just plain text.
"""


def _fetch_url(url, timeout=FETCH_TIMEOUT):
    """fetch 单个 URL 的内容。

    Args:
        url: 要 fetch 的 URL
        timeout: 超时秒数

    Returns:
        tuple: (success: bool, content: str, error: str or None)
            content: HTML 内容（截断到 MAX_CONTENT_LENGTH），失败时为空串
    """
    if not isinstance(url, str) or not url.strip():
        return False, '', 'Empty URL'

    # 简单 URL 校验
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, '', f'Invalid URL: {url[:50]}'
        if parsed.scheme not in ('http', 'https'):
            return False, '', f'Unsupported scheme: {parsed.scheme}'
    except Exception as e:
        return False, '', f'URL parse error: {e}'

    req = urllib.request.Request(
        url,
        headers={'User-Agent': _USER_AGENT, 'Accept': 'text/html,*/*'},
        method='GET',
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # 仅处理 2xx
            status = resp.getcode()
            if status < 200 or status >= 300:
                return False, '', f'HTTP {status}'

            content_type = resp.headers.get('Content-Type', '')
            if 'text/' not in content_type and 'html' not in content_type \
                    and 'xml' not in content_type:
                return False, '', f'Non-text content-type: {content_type}'

            raw = resp.read(MAX_CONTENT_LENGTH + 1)
            # 截断到 MAX_CONTENT_LENGTH
            if len(raw) > MAX_CONTENT_LENGTH:
                raw = raw[:MAX_CONTENT_LENGTH]

            try:
                content = raw.decode('utf-8', errors='replace')
            except UnicodeDecodeError:
                content = raw.decode('latin-1', errors='replace')

            return True, content, None
    except urllib.error.HTTPError as e:
        return False, '', f'HTTP {e.code}: {e.reason}'
    except urllib.error.URLError as e:
        return False, '', f'URL error: {e.reason}'
    except TimeoutError:
        return False, '', f'Timeout after {timeout}s'
    except Exception as e:
        return False, '', f'{type(e).__name__}: {e}'


def _strip_html(content):
    """粗略去除 HTML 标签，提取文本内容。

    简单实现，避免引入 BeautifulSoup 依赖。
    """
    if not content:
        return ''

    import re
    # 移除 script 和 style 块（含内容）
    text = re.sub(r'<script[^>]*>.*?</script>', '', content,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text,
                  flags=re.DOTALL | re.IGNORECASE)
    # 移除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # 移除标签
    text = re.sub(r'<[^>]+>', '', text)
    # 处理常见 HTML 实体
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    # 压缩空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _call_flash_for_summary(url, title, content, api_key=None, timeout=30):
    """调用 Flash API 生成摘要。失败抛异常。"""
    if api_key is None:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY 未设置')

    # 出境 PII 脱敏：content 中可能含 PII
    redacted_content, _ = _redact_outbound(content[:2000])
    redacted_url, _ = _redact_outbound(url)
    redacted_title, _ = _redact_outbound(title)

    prompt = _FLASH_PROMPT_TEMPLATE.format(
        url=redacted_url,
        title=redacted_title,
        content=redacted_content,
    )

    # Phase 2: resolve model via guard (summarize is allowlisted → Flash)
    if _HAS_FLASH_GUARD:
        model = _flash_guard.resolve_model(
            "summarize", caller="summarize._call_summarize_api")
    else:
        model = _FLASH_MODEL
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 200,
        'temperature': 0.3,
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
        raise RuntimeError(f'Flash API HTTP {e.code}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'Flash API URL error: {e.reason}')
    except TimeoutError:
        raise RuntimeError(f'Flash API timeout after {timeout}s')

    try:
        text = data['choices'][0]['message']['content'].strip()
        # 截断到 100 字
        if len(text) > 100:
            text = text[:100]
        return text
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise RuntimeError(f'Flash API response parse failed: {e}')


def summarize_one(result, mode='flash', api_key=None):
    """对单条搜索结果生成摘要。

    Args:
        result: dict with 'url', 'title', 'snippet'
        mode: 'flash' | 'snippet'（snippet 模式不调用 Flash）
        api_key: DeepSeek API key

    Returns:
        dict: 原始 result 加上：
            - summary: str, 摘要文本
            - summary_mode: str, 'flash' | 'snippet' | 'fetch-failed'
            - fetch_success: bool
            - error: str (仅失败时存在)
    """
    if not isinstance(result, dict):
        return {
            'summary': '',
            'summary_mode': 'invalid',
            'fetch_success': False,
            'error': 'Invalid result type',
        }

    url = str(result.get('url', ''))
    title = str(result.get('title', ''))
    snippet = str(result.get('snippet', ''))

    # snippet 模式：直接返回 snippet（截断 100 字）
    if mode == 'snippet':
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'snippet',
            'fetch_success': False,
        }

    # 未知模式 → 用 snippet 兜底，不 fetch
    if mode != 'flash':
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'snippet',
            'fetch_success': False,
        }

    # flash 模式：fetch + Flash 摘要
    fetch_success, content, fetch_error = _fetch_url(url)

    if not fetch_success:
        # fetch 失败 → 降级到 snippet
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'fetch-failed',
            'fetch_success': False,
            'error': fetch_error,
        }

    # 提取纯文本
    text_content = _strip_html(content)
    if not text_content.strip():
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'snippet',
            'fetch_success': True,
            'error': 'Empty content after HTML strip',
        }

    # 调用 Flash 生成摘要
    try:
        summary = _call_flash_for_summary(url, title, text_content,
                                          api_key=api_key)
        return {
            **result,
            'summary': summary,
            'summary_mode': 'flash',
            'fetch_success': True,
        }
    except Exception as e:
        # Flash 失败 → 降级到 snippet
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'flash-failed-snippet',
            'fetch_success': True,
            'error': str(e)[:200],
        }


def summarize_results(results, max_workers=MAX_WORKERS, mode='flash',
                      max_urls=MAX_URLS_TO_FETCH, api_key=None):
    """对搜索结果列表生成摘要。

    Args:
        results: 搜索结果列表 [{'url', 'title', 'snippet'}, ...]
        max_workers: 并发 fetch 线程数
        mode: 'flash' | 'snippet'
        max_urls: 最多处理 URL 数（成本控制）
        api_key: DeepSeek API key

    Returns:
        list[dict]: 每条加 summary / summary_mode / fetch_success 字段
    """
    if not isinstance(results, list) or not results:
        return []

    # 限制并发处理的 URL 数
    to_process = results[:max_urls]
    remaining = results[max_urls:]

    summaries = [None] * len(to_process)

    def _process_one(idx, result):
        try:
            return idx, summarize_one(result, mode=mode, api_key=api_key)
        except Exception as e:
            # 终极兜底：返回 snippet 作为摘要
            return idx, {
                **result,
                'summary': str(result.get('snippet', ''))[:100],
                'summary_mode': 'exception-fallback',
                'fetch_success': False,
                'error': str(e)[:200],
            }

    # 并发处理
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_process_one, i, r)
            for i, r in enumerate(to_process)
        ]
        for future in as_completed(futures):
            try:
                idx, summary = future.result()
                summaries[idx] = summary
            except Exception as e:
                # 不应发生，因为 _process_one 已兜底
                pass

    # 剩余的 URL 直接用 snippet（不 fetch）
    for r in remaining:
        summaries.append({
            **r,
            'summary': str(r.get('snippet', ''))[:100] if isinstance(r, dict) else '',
            'summary_mode': 'skipped-limit',
            'fetch_success': False,
        })

    return summaries


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='自动 fetch Top URL + Flash 摘要'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # fetch 子命令
    p_fetch = sub.add_parser('fetch', help='并发 fetch URL + 生成摘要')
    p_fetch.add_argument('--results-json', required=True,
                         help='搜索结果 JSON 数组')
    p_fetch.add_argument('--mode', default='flash',
                         choices=['flash', 'snippet'],
                         help='摘要模式（默认 flash）')
    p_fetch.add_argument('--max-workers', type=int, default=MAX_WORKERS,
                         help=f'并发线程数（默认 {MAX_WORKERS}）')
    p_fetch.add_argument('--max-urls', type=int, default=MAX_URLS_TO_FETCH,
                         help=f'最多 fetch URL 数（默认 {MAX_URLS_TO_FETCH}）')

    args = parser.parse_args()

    if args.command == 'fetch':
        try:
            results = json.loads(args.results_json)
            if not isinstance(results, list):
                print('错误: --results-json 必须是 JSON 数组', file=sys.stderr)
                return 1
        except json.JSONDecodeError as e:
            print(f'错误: JSON 解析失败: {e}', file=sys.stderr)
            return 1

        summaries = summarize_results(
            results,
            max_workers=args.max_workers,
            mode=args.mode,
            max_urls=args.max_urls,
        )
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
