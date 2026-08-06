#!/usr/bin/env python3
"""Chinese/English query expansion through the unified Model Gateway.

Language detection remains local and deterministic. Translation is optional,
PII-redacted before outbound use, and always falls back to the original query.
"""
from __future__ import annotations

import sys
import os
import json
import re
import argparse
import urllib.request

from modules.dispatch.compat import invoke_with_urlopen
from modules.dispatch.gateway import DEFAULT_BASE_URL

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(text):
        return text, {'redacted_count': 0}

# Historical read-only compatibility constant. Request construction is owned by
# Model Gateway; this value is retained only for callers/tests that inspect the
# legacy module surface.
_FLASH_API = DEFAULT_BASE_URL
_FLASH_MODEL = 'deepseek-chat'
_FLASH_TIMEOUT = 15
_FLASH_MAX_TOKENS = 100
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+')
_ENGLISH_RE = re.compile(r'\b[a-zA-Z]{2,}\b')
SUPPORTED_TARGETS = ('zh', 'en')


def detect_mixed_lang(query):
    if not isinstance(query, str) or not query:
        return {
            'has_chinese': False,
            'has_english': False,
            'is_mixed': False,
            'chinese_parts': [],
            'english_parts': [],
            'chinese_ratio': 0.0,
            'primary_lang': 'unknown',
        }

    chinese_parts = _CJK_RE.findall(query)
    seen_zh = set()
    chinese_parts_unique = []
    for part in chinese_parts:
        if part not in seen_zh:
            seen_zh.add(part)
            chinese_parts_unique.append(part)

    english_parts = _ENGLISH_RE.findall(query)
    english_parts_clean = []
    seen_en = set()
    for part in english_parts:
        cleaned = part.strip()
        if cleaned and cleaned not in seen_en:
            seen_en.add(cleaned)
            english_parts_clean.append(cleaned)

    has_chinese = bool(chinese_parts_unique)
    has_english = bool(english_parts_clean)
    is_mixed = has_chinese and has_english
    cjk_count = len(''.join(_CJK_RE.findall(query)))
    alpha_count = sum(1 for c in query if c.isalpha())
    chinese_ratio = (cjk_count / alpha_count) if alpha_count > 0 else 0.0

    if is_mixed:
        primary_lang = 'mixed'
    elif has_chinese:
        primary_lang = 'zh'
    elif has_english:
        primary_lang = 'en'
    else:
        primary_lang = 'unknown'

    return {
        'has_chinese': has_chinese,
        'has_english': has_english,
        'is_mixed': is_mixed,
        'chinese_parts': chinese_parts_unique,
        'english_parts': english_parts_clean,
        'chinese_ratio': chinese_ratio,
        'primary_lang': primary_lang,
    }


def _build_translate_prompt(query, target_lang):
    if target_lang == 'en':
        instruction = (
            'Translate the following query to English. '
            'Preserve technical terms (library names, function names, API names) as-is. '
            'Output only the translated query, no explanation, no quotes.'
        )
    elif target_lang == 'zh':
        instruction = (
            '将以下查询翻译为中文。'
            '保留技术术语（库名、函数名、API 名）不翻译。'
            '仅输出翻译结果，不要解释，不要加引号。'
        )
    else:
        raise ValueError(f'Unsupported target language: {target_lang}')
    return f'{instruction}\n\nQuery: {query}\n\nTranslated:'


def _gateway_error(error, timeout):
    text = str(error or 'unknown model error')
    http_match = re.search(r'HTTP Error (\d+):?\s*(.*)', text)
    if http_match:
        return f'Flash API HTTP {http_match.group(1)}: {http_match.group(2)}'.rstrip()
    lowered = text.lower()
    if 'timed out' in lowered or 'timeout' in lowered:
        return f'Flash API timeout after {timeout}s'
    if 'urlopen error' in lowered or 'url error' in lowered:
        return f'Flash API URL error: {text}'
    if 'no choices' in lowered or 'content is not a string' in lowered:
        return f'Flash API response parse failed: {text}'
    return f'RuntimeError: {text}'


def translate_query(query, target_lang, api_key=None, timeout=_FLASH_TIMEOUT):
    if not isinstance(query, str) or not query.strip():
        return {
            'success': False,
            'original': query if isinstance(query, str) else '',
            'translated': '',
            'target_lang': target_lang,
            'error': 'Empty query',
        }
    if target_lang not in SUPPORTED_TARGETS:
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': f'Unsupported target: {target_lang}',
        }

    redacted_query, redact_meta = _redact_outbound(query)
    if redact_meta.get('redacted_count', 0) > 0:
        sys.stderr.write(
            f"[i18n] Layer 0 redacted {redact_meta['redacted_count']} PII pattern(s); "
            f"types: {redact_meta.get('patterns_matched', [])}\n"
        )

    if api_key is None:
        api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': 'DEEPSEEK_API_KEY not set',
        }

    prompt = _build_translate_prompt(redacted_query, target_lang)
    response = invoke_with_urlopen(
        task_type='i18n',
        messages=({'role': 'user', 'content': prompt},),
        urlopen=urllib.request.urlopen,
        api_key=api_key,
        model=_FLASH_MODEL,
        max_tokens=_FLASH_MAX_TOKENS,
        temperature=0.1,
        timeout_seconds=float(timeout),
        max_retries=0,
        metadata={'caller': 'search.i18n', 'target_lang': target_lang},
        record_run=False,
    )
    if not response.success:
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': _gateway_error(response.error, timeout),
        }

    text = _strip_markdown_fences(response.content)
    text = text.strip('`"\' \t\n\r')
    if not text:
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': 'Empty translation result',
        }
    return {
        'success': True,
        'original': query,
        'translated': text,
        'target_lang': target_lang,
    }


def _strip_markdown_fences(text):
    if not text:
        return text
    match = re.match(r'^```[a-zA-Z]*\n(.*?)\n```$', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    if text.startswith('```') and text.endswith('```'):
        return text[3:-3].strip()
    if text.startswith('`') and text.endswith('`') and len(text) >= 2:
        return text[1:-1].strip()
    if len(text) >= 2:
        if (text[0] == '"' and text[-1] == '"') or \
           (text[0] == "'" and text[-1] == "'"):
            return text[1:-1].strip()
    return text


def expand_query(query, api_key=None, timeout=_FLASH_TIMEOUT,
                 include_opposite=True):
    if not isinstance(query, str) or not query.strip():
        return []

    lang_info = detect_mixed_lang(query)
    queries = [query]
    if lang_info['is_mixed']:
        en_result = translate_query(query, 'en', api_key=api_key, timeout=timeout)
        if en_result['success'] and en_result['translated'] != query:
            queries.append(en_result['translated'])
        zh_result = translate_query(query, 'zh', api_key=api_key, timeout=timeout)
        if zh_result['success'] and zh_result['translated'] != query:
            queries.append(zh_result['translated'])
    elif include_opposite:
        if lang_info['primary_lang'] == 'zh':
            target = 'en'
        elif lang_info['primary_lang'] == 'en':
            target = 'zh'
        else:
            return queries
        result = translate_query(query, target, api_key=api_key, timeout=timeout)
        if result['success'] and result['translated'] != query:
            queries.append(result['translated'])

    seen = set()
    unique = []
    for value in queries:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _cli():
    parser = argparse.ArgumentParser(
        description='中英文混合查询扩展（i18n for search queries）'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_detect = sub.add_parser('detect', help='检测查询语言分布')
    p_detect.add_argument('--query', required=True, help='查询字符串')

    p_translate = sub.add_parser('translate', help='翻译查询到目标语言')
    p_translate.add_argument('--query', required=True, help='查询字符串')
    p_translate.add_argument('--target', required=True, choices=SUPPORTED_TARGETS,
                             help='目标语言：zh | en')
    p_translate.add_argument('--timeout', type=int, default=_FLASH_TIMEOUT,
                             help=f'翻译超时秒数（默认 {_FLASH_TIMEOUT}）')
    p_translate.add_argument('--json', action='store_true', help='JSON 输出')

    p_expand = sub.add_parser('expand', help='扩展查询为多语言版本')
    p_expand.add_argument('--query', required=True, help='查询字符串')
    p_expand.add_argument('--timeout', type=int, default=_FLASH_TIMEOUT,
                          help=f'翻译超时秒数（默认 {_FLASH_TIMEOUT}）')
    p_expand.add_argument('--no-opposite', action='store_true',
                          help='单语言查询不翻译到另一语言')
    p_expand.add_argument('--json', action='store_true', help='JSON 输出')

    args = parser.parse_args()
    if args.command == 'detect':
        print(json.dumps(detect_mixed_lang(args.query), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'translate':
        result = translate_query(args.query, args.target, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result['success']:
            print(f"原文: {result['original']}")
            print(f"译文 ({args.target}): {result['translated']}")
        else:
            print(f"翻译失败: {result.get('error', 'unknown')}", file=sys.stderr)
            return 1
        return 0
    if args.command == 'expand':
        queries = expand_query(
            args.query,
            timeout=args.timeout,
            include_opposite=not args.no_opposite,
        )
        if args.json:
            print(json.dumps({
                'original': args.query,
                'expanded': queries,
                'count': len(queries),
            }, ensure_ascii=False, indent=2))
        else:
            print(f"原始查询: {args.query}")
            print(f"扩展为 {len(queries)} 个版本:")
            for i, value in enumerate(queries, 1):
                print(f"  {i}. {value}")
        return 0
    return 1


if __name__ == '__main__':
    sys.exit(_cli())
