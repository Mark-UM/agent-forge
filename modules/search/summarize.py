#!/usr/bin/env python3
"""Fetch top URLs and produce bounded summaries through Model Gateway.

Fetch and model failures degrade to the original snippet. The public CLI and
unit-test seams remain compatible while model routing and redaction are
centralized.
"""
from __future__ import annotations

import sys
import os
import json
import argparse
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from modules.dispatch.compat import invoke_with_urlopen

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(query):
        return query, {'redacted_count': 0}

_FLASH_MODEL = 'deepseek-chat'
FETCH_TIMEOUT = 10
MAX_CONTENT_LENGTH = 5000
MAX_WORKERS = 5
MAX_URLS_TO_FETCH = 3
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
    if not isinstance(url, str) or not url.strip():
        return False, '', 'Empty URL'
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
            status = resp.getcode()
            if status < 200 or status >= 300:
                return False, '', f'HTTP {status}'
            content_type = resp.headers.get('Content-Type', '')
            if 'text/' not in content_type and 'html' not in content_type \
                    and 'xml' not in content_type:
                return False, '', f'Non-text content-type: {content_type}'
            raw = resp.read(MAX_CONTENT_LENGTH + 1)
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
    if not content:
        return ''
    text = re.sub(r'<script[^>]*>.*?</script>', '', content,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    return re.sub(r'\s+', ' ', text).strip()


def _gateway_error(error, timeout):
    text = str(error or 'unknown model error')
    match = re.search(r'HTTP Error (\d+)', text)
    if match:
        return RuntimeError(f'Flash API HTTP {match.group(1)}')
    lowered = text.lower()
    if 'timed out' in lowered or 'timeout' in lowered:
        return RuntimeError(f'Flash API timeout after {timeout}s')
    if 'urlopen error' in lowered or 'url error' in lowered:
        return RuntimeError(f'Flash API URL error: {text}')
    return RuntimeError(f'Flash API failed: {text}')


def _call_flash_for_summary(url, title, content, api_key=None, timeout=30):
    if api_key is None:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        raise RuntimeError('DEEPSEEK_API_KEY 未设置')

    redacted_content, _ = _redact_outbound(content[:2000])
    redacted_url, _ = _redact_outbound(url)
    redacted_title, _ = _redact_outbound(title)
    prompt = _FLASH_PROMPT_TEMPLATE.format(
        url=redacted_url,
        title=redacted_title,
        content=redacted_content,
    )
    response = invoke_with_urlopen(
        task_type='summarize',
        messages=({'role': 'user', 'content': prompt},),
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=_FLASH_MODEL,
        max_tokens=200,
        temperature=0.3,
        timeout_seconds=float(timeout),
        max_retries=0,
        metadata={'caller': 'search.summarize'},
        record_run=False,
    )
    if not response.success:
        raise _gateway_error(response.error, timeout)
    text = response.content.strip()
    return text[:100] if len(text) > 100 else text


def summarize_one(result, mode='flash', api_key=None):
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
    if mode == 'snippet' or mode != 'flash':
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'snippet',
            'fetch_success': False,
        }

    fetch_success, content, fetch_error = _fetch_url(url)
    if not fetch_success:
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'fetch-failed',
            'fetch_success': False,
            'error': fetch_error,
        }

    text_content = _strip_html(content)
    if not text_content.strip():
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'snippet',
            'fetch_success': True,
            'error': 'Empty content after HTML strip',
        }

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
        return {
            **result,
            'summary': snippet[:100],
            'summary_mode': 'flash-failed-snippet',
            'fetch_success': True,
            'error': str(e)[:200],
        }


def summarize_results(results, max_workers=MAX_WORKERS, mode='flash',
                      max_urls=MAX_URLS_TO_FETCH, api_key=None):
    if not isinstance(results, list) or not results:
        return []

    to_process = results[:max_urls]
    remaining = results[max_urls:]
    summaries = [None] * len(to_process)

    def _process_one(idx, result):
        try:
            return idx, summarize_one(result, mode=mode, api_key=api_key)
        except Exception as e:
            return idx, {
                **result,
                'summary': str(result.get('snippet', ''))[:100],
                'summary_mode': 'exception-fallback',
                'fetch_success': False,
                'error': str(e)[:200],
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_process_one, i, r)
            for i, r in enumerate(to_process)
        ]
        for future in as_completed(futures):
            try:
                idx, summary = future.result()
                summaries[idx] = summary
            except Exception:
                pass

    for result in remaining:
        summaries.append({
            **result,
            'summary': str(result.get('snippet', ''))[:100]
            if isinstance(result, dict) else '',
            'summary_mode': 'skipped-limit',
            'fetch_success': False,
        })
    return summaries


def _cli():
    parser = argparse.ArgumentParser(
        description='自动 fetch Top URL + Flash 摘要'
    )
    sub = parser.add_subparsers(dest='command', required=True)
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
