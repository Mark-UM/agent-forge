#!/usr/bin/env python3
"""中英文混合查询扩展 — i18n for search queries.

设计原则：
- 单文件独立模块，零外部依赖（仅 Python 标准库）
- Flash 模型翻译（与 planner.py / quality.py 一致），失败时降级到原文
- 出境前 PII 脱敏（query 中的 PII 永不出境）
- 失败兜底：所有 IO 异常均不阻塞主流程，返回原始 query

Usage:
  python i18n.py expand --query "React useEffect 清理副作用"
  python i18n.py detect --query "..."
  python i18n.py translate --query "..." --target en
  python -m modules.search.tests.test_i18n
"""
import sys
import os
import json
import re
import argparse
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 导入 privacy 模块做出境 PII 脱敏
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from privacy import redact_outbound as _redact_outbound
except ImportError:
    def _redact_outbound(text):
        return text, {'redacted_count': 0}


# ── Constants ────────────────────────────────────────────────
_FLASH_API = 'https://api.deepseek.com/v1/chat/completions'
_FLASH_MODEL = 'deepseek-chat'
_FLASH_TIMEOUT = 15  # 翻译较短，超时设短
_FLASH_MAX_TOKENS = 100  # 翻译结果通常 < 50 tokens

# Phase 2: import flash_guard for model resolution
# i18n is in the Flash ALLOWED task list (utility, deterministic)
try:
    import sys as _sys
    _PROJECT_ROOT_FOR_GUARD = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PROJECT_ROOT_FOR_GUARD not in _sys.path:
        _sys.path.insert(0, _PROJECT_ROOT_FOR_GUARD)
    from modules.dispatch import guard as _flash_guard
    _HAS_FLASH_GUARD = True
except ImportError:
    _HAS_FLASH_GUARD = False

# 中文字符范围（含简繁体 + 扩展 A 区）
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+')

# 英文单词（至少 2 个连续字母，避免单字母误判；按单词边界分割）
_ENGLISH_RE = re.compile(r'\b[a-zA-Z]{2,}\b')

# 支持的目标语言
SUPPORTED_TARGETS = ('zh', 'en')


# ── 语言检测 ────────────────────────────────────────────────
def detect_mixed_lang(query):
    """检测查询中的语言分布。

    Args:
        query: 用户查询字符串

    Returns:
        dict: {
            'has_chinese': bool,
            'has_english': bool,
            'is_mixed': bool,  # 同时含中文和英文
            'chinese_parts': list[str],  # 中文字符片段
            'english_parts': list[str],  # 英文片段
            'chinese_ratio': float,  # 0.0-1.0，中文字符占比
            'primary_lang': str,  # 'zh' | 'en' | 'unknown'
        }
    """
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
    # 去重并保留顺序
    seen_zh = set()
    chinese_parts_unique = []
    for part in chinese_parts:
        if part not in seen_zh:
            seen_zh.add(part)
            chinese_parts_unique.append(part)

    english_parts = _ENGLISH_RE.findall(query)
    # 清理英文片段（去首尾空白）
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

    # 计算中文占比（按字符数：拼接所有匹配的中文字符串再计数）
    cjk_chars = ''.join(_CJK_RE.findall(query))
    cjk_count = len(cjk_chars)
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


# ── Flash API 翻译 ─────────────────────────────────────────
def _build_translate_prompt(query, target_lang):
    """构建翻译 prompt。"""
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

    return (
        f'{instruction}\n\n'
        f'Query: {query}\n\n'
        f'Translated:'
    )


def translate_query(query, target_lang, api_key=None, timeout=_FLASH_TIMEOUT):
    """使用 Flash 模型翻译查询到目标语言。

    Args:
        query: 原始查询字符串
        target_lang: 'zh' | 'en'
        api_key: DeepSeek API key（None 则从 env 读取 DEEPSEEK_API_KEY）
        timeout: HTTP 超时秒数

    Returns:
        dict: {
            'success': bool,
            'original': str,
            'translated': str,  # 成功时为翻译，失败时为原文
            'target_lang': str,
            'error': str,  # 仅失败时存在
        }
    """
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

    # 出境前 PII 脱敏
    redacted_query, redact_meta = _redact_outbound(query)
    if redact_meta.get('redacted_count', 0) > 0:
        sys.stderr.write(
            f"[i18n] Layer 0 redacted {redact_meta['redacted_count']} PII pattern(s); "
            f"types: {redact_meta.get('patterns_matched', [])}\n"
        )

    # 若脱敏后 query 与原 query 一致（无 PII），跳过冗余翻译请求
    # 否则继续翻译脱敏后的 query

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

    # Phase 2: resolve model via guard (i18n is allowlisted → Flash)
    if _HAS_FLASH_GUARD:
        model = _flash_guard.resolve_model(
            "i18n", caller="i18n._call_translate_api")
    else:
        model = _FLASH_MODEL
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': _FLASH_MAX_TOKENS,
        'temperature': 0.1,  # 翻译需要确定性
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
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
        # scrub API key from error body
        if api_key and api_key in body:
            body = body.replace(api_key, '[REDACTED-KEY]')
        return {
            'success': False,
            'original': query,
            'translated': query,  # 失败时降级到原文
            'target_lang': target_lang,
            'error': f'Flash API HTTP {e.code}: {body}',
        }
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError) or 'timeout' in str(reason).lower() or 'timed out' in str(reason).lower():
            return {
                'success': False,
                'original': query,
                'translated': query,
                'target_lang': target_lang,
                'error': f'Flash API timeout after {timeout}s',
            }
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': f'Flash API URL error: {reason}',
        }
    except TimeoutError:
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': f'Flash API timeout after {timeout}s',
        }
    except Exception as e:
        err_msg = f'{type(e).__name__}: {e}'
        # Defense-in-depth: scrub API key from unexpected exception strings
        if api_key and api_key in err_msg:
            err_msg = err_msg.replace(api_key, '[REDACTED-KEY]')
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': err_msg,
        }

    # 解析响应
    try:
        text = data['choices'][0]['message']['content'].strip()
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        return {
            'success': False,
            'original': query,
            'translated': query,
            'target_lang': target_lang,
            'error': f'Flash API response parse failed: {e}',
        }

    # 清理可能的 markdown 代码块 / 引号 / 多余空白
    text = _strip_markdown_fences(text)
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
    """去除 markdown 代码块围栏 + 包裹引号。"""
    if not text:
        return text
    # ```lang\ntext\n```  →  text
    m = re.match(r'^```[a-zA-Z]*\n(.*?)\n```$', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # ``text``
    if text.startswith('```') and text.endswith('```'):
        return text[3:-3].strip()
    # `text`
    if text.startswith('`') and text.endswith('`') and len(text) >= 2:
        return text[1:-1].strip()
    # "text" or 'text'（包裹引号）
    if len(text) >= 2:
        if (text[0] == '"' and text[-1] == '"') or \
           (text[0] == "'" and text[-1] == "'"):
            return text[1:-1].strip()
    return text


# ── 查询扩展 ────────────────────────────────────────────────
def expand_query(query, api_key=None, timeout=_FLASH_TIMEOUT,
                 include_opposite=True):
    """扩展查询为多语言版本。

    若 query 含中英文混合，返回 [original, en_translation, zh_translation]。
    若 query 为单语言，返回 [original, opposite_lang_translation]（若 include_opposite=True）
    或仅 [original]（若 include_opposite=False）。
    若翻译失败，回退到 [original]。

    Args:
        query: 原始查询
        api_key: DeepSeek API key
        timeout: 翻译超时
        include_opposite: 单语言 query 是否翻译到另一语言

    Returns:
        list[str]: 去重后的查询列表（保留顺序）
    """
    if not isinstance(query, str) or not query.strip():
        return []

    lang_info = detect_mixed_lang(query)
    queries = [query]

    if lang_info['is_mixed']:
        # 中英混合：翻译为纯英文 + 纯中文
        en_result = translate_query(query, 'en', api_key=api_key, timeout=timeout)
        if en_result['success'] and en_result['translated'] != query:
            queries.append(en_result['translated'])

        zh_result = translate_query(query, 'zh', api_key=api_key, timeout=timeout)
        if zh_result['success'] and zh_result['translated'] != query:
            queries.append(zh_result['translated'])

    elif include_opposite:
        # 单语言：翻译到另一语言
        if lang_info['primary_lang'] == 'zh':
            target = 'en'
        elif lang_info['primary_lang'] == 'en':
            target = 'zh'
        else:
            return queries

        result = translate_query(query, target, api_key=api_key, timeout=timeout)
        if result['success'] and result['translated'] != query:
            queries.append(result['translated'])

    # 去重（保留顺序）
    seen = set()
    unique = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


# ── CLI ──────────────────────────────────────────────────────
def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='中英文混合查询扩展（i18n for search queries）'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # detect 子命令
    p_detect = sub.add_parser('detect', help='检测查询语言分布')
    p_detect.add_argument('--query', required=True, help='查询字符串')

    # translate 子命令
    p_translate = sub.add_parser('translate', help='翻译查询到目标语言')
    p_translate.add_argument('--query', required=True, help='查询字符串')
    p_translate.add_argument('--target', required=True, choices=SUPPORTED_TARGETS,
                             help='目标语言：zh | en')
    p_translate.add_argument('--timeout', type=int, default=_FLASH_TIMEOUT,
                             help=f'翻译超时秒数（默认 {_FLASH_TIMEOUT}）')
    p_translate.add_argument('--json', action='store_true', help='JSON 输出')

    # expand 子命令
    p_expand = sub.add_parser('expand', help='扩展查询为多语言版本')
    p_expand.add_argument('--query', required=True, help='查询字符串')
    p_expand.add_argument('--timeout', type=int, default=_FLASH_TIMEOUT,
                          help=f'翻译超时秒数（默认 {_FLASH_TIMEOUT}）')
    p_expand.add_argument('--no-opposite', action='store_true',
                          help='单语言查询不翻译到另一语言')
    p_expand.add_argument('--json', action='store_true', help='JSON 输出')

    args = parser.parse_args()

    if args.command == 'detect':
        result = detect_mixed_lang(args.query)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == 'translate':
        result = translate_query(args.query, args.target,
                                 timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if result['success']:
                print(f"原文: {result['original']}")
                print(f"译文 ({args.target}): {result['translated']}")
            else:
                print(f"翻译失败: {result.get('error', 'unknown')}", file=sys.stderr)
                return 1
        return 0

    if args.command == 'expand':
        queries = expand_query(args.query,
                               timeout=args.timeout,
                               include_opposite=not args.no_opposite)
        if args.json:
            print(json.dumps({
                'original': args.query,
                'expanded': queries,
                'count': len(queries),
            }, ensure_ascii=False, indent=2))
        else:
            print(f"原始查询: {args.query}")
            print(f"扩展为 {len(queries)} 个版本:")
            for i, q in enumerate(queries, 1):
                print(f"  {i}. {q}")
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
