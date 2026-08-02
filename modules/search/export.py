#!/usr/bin/env python3
"""搜索历史导出 — JSON/CSV 格式，支持时间窗口 + 字段筛选 + 收藏过滤。

设计原则：
- 单文件独立模块，零外部依赖
- 支持导出为 JSON（完整结构）或 CSV（扁平化）
- 字段筛选：仅导出指定字段
- 时间窗口：--days N（最近 N 天）
- 收藏过滤：--saved-only
- 所有 IO 异常兜底

Usage:
  python export.py export --format json --days 30
  python export.py export --format csv --fields query,score,timestamp --saved-only
  python export.py export --format json --output history.json
  python -m modules.search.tests.test_export
"""
import sys
import os
import json
import csv
import argparse
import io
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_PROJECT_ROOT, '_runtime', 'search')

# 支持的导出字段（顺序即 CSV 列顺序）
SUPPORTED_FIELDS = [
    'timestamp',
    'query',
    'score',
    'satisfied',
    'saved',
    'results_count',
    'layers_used',
    'top_results',
]

# 默认导出字段（用户未指定时）
DEFAULT_FIELDS = ['timestamp', 'query', 'score', 'saved', 'results_count']


def _all_log_files():
    """列出所有 search_history.YYYY-MM-DD.jsonl 文件。"""
    if not os.path.exists(_LOG_DIR):
        return []
    files = []
    for name in os.listdir(_LOG_DIR):
        if name.startswith('search_history.') and name.endswith('.jsonl'):
            files.append(os.path.join(_LOG_DIR, name))
    files.sort(reverse=True)
    return files


def _load_entries(files, days_limit=None, saved_only=False):
    """加载并过滤历史条目。"""
    if not files:
        return []

    cutoff = None
    if days_limit is not None:
        cutoff = datetime.now() - timedelta(days=days_limit)

    entries = []
    for log_file in files:
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        # 时间过滤
                        if cutoff is not None:
                            ts_str = entry.get('timestamp', '')
                            try:
                                ts = datetime.fromisoformat(ts_str)
                                if ts < cutoff:
                                    continue
                            except (ValueError, TypeError):
                                pass
                        # 收藏过滤
                        if saved_only and not entry.get('saved', False):
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except (OSError, UnicodeDecodeError):
            # 文件读取失败或编码错误 → 跳过该文件
            continue
    return entries


def _filter_fields(entry, fields):
    """从 entry 中提取指定字段。

    Args:
        entry: 原始 entry dict
        fields: list[str] 字段名列表

    Returns:
        dict: 仅包含指定字段的 entry（深拷贝）
    """
    if not isinstance(entry, dict):
        return {}

    result = {}
    for field in fields:
        if field in entry:
            value = entry[field]
            # 对 list/dict 类型做 JSON 序列化以便 CSV 存储
            if isinstance(value, (list, dict)):
                result[field] = json.dumps(value, ensure_ascii=False)
            else:
                result[field] = value
        else:
            result[field] = ''
    return result


def export_to_json(entries, fields=None, pretty=True):
    """导出为 JSON 格式。

    Args:
        entries: 历史条目列表
        fields: 字段筛选（None = 全部字段）
        pretty: 是否美化输出（默认 True）

    Returns:
        str: JSON 字符串
    """
    if not isinstance(entries, list):
        return '[]'

    if not entries:
        return '[]'

    if fields is not None:
        # 仅保留指定字段
        filtered = [_filter_fields(e, fields) for e in entries]
    else:
        filtered = list(entries)

    if pretty:
        return json.dumps(filtered, ensure_ascii=False, indent=2)
    return json.dumps(filtered, ensure_ascii=False)


def export_to_csv(entries, fields=None):
    """导出为 CSV 格式。

    Args:
        entries: 历史条目列表
        fields: 字段筛选（None = DEFAULT_FIELDS）

    Returns:
        str: CSV 字符串（UTF-8 with BOM 便于 Excel 打开）
    """
    if not isinstance(entries, list) or not entries:
        return ''

    if fields is None:
        fields = DEFAULT_FIELDS
    elif isinstance(fields, str):
        fields = [f.strip() for f in fields.split(',') if f.strip()]

    # 验证字段名
    fields = [f for f in fields if f in SUPPORTED_FIELDS]
    if not fields:
        fields = DEFAULT_FIELDS

    output = io.StringIO()
    # 写入 UTF-8 BOM（Excel 兼容）
    output.write('\ufeff')

    writer = csv.DictWriter(output, fieldnames=fields,
                             quoting=csv.QUOTE_MINIMAL,
                             extrasaction='ignore')
    writer.writeheader()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        row = _filter_fields(entry, fields)
        # csv 模块要求所有值是 str
        for k, v in row.items():
            if v is None:
                row[k] = ''
            elif isinstance(v, bool):
                row[k] = 'true' if v else 'false'
            elif not isinstance(v, str):
                row[k] = str(v)
        try:
            writer.writerow(row)
        except (ValueError, TypeError) as e:
            # 行写入失败时跳过该行
            continue

    return output.getvalue()


def export_history(format='json', days=None, fields=None,
                   saved_only=False, output_file=None):
    """导出搜索历史。

    Args:
        format: 'json' | 'csv'
        days: 仅导出最近 N 天（None = 全部）
        fields: 字段筛选（list 或 comma-separated string，None = 全部/默认）
        saved_only: 仅导出已收藏的记录
        output_file: 输出文件路径（None = 输出到 stdout）

    Returns:
        dict: {
            'success': bool,
            'format': str,
            'entry_count': int,
            'output_file': str or None,
            'output': str,  # 仅 output_file=None 时
            'error': str,  # 仅失败时存在
        }
    """
    files = _all_log_files()
    if not files:
        return {
            'success': True,
            'format': format,
            'entry_count': 0,
            'output_file': output_file,
            'output': '[]' if format == 'json' else '',
        }

    entries = _load_entries(files, days_limit=days, saved_only=saved_only)
    if not entries:
        return {
            'success': True,
            'format': format,
            'entry_count': 0,
            'output_file': output_file,
            'output': '[]' if format == 'json' else '',
        }

    # 解析 fields 参数
    parsed_fields = None
    if fields is not None:
        if isinstance(fields, str):
            parsed_fields = [f.strip() for f in fields.split(',') if f.strip()]
        elif isinstance(fields, list):
            parsed_fields = fields
        # 验证字段
        parsed_fields = [f for f in parsed_fields if f in SUPPORTED_FIELDS]
        if not parsed_fields:
            parsed_fields = None  # 回退到全部字段（JSON）或 DEFAULT_FIELDS（CSV）

    # 生成导出内容
    if format == 'json':
        content = export_to_json(entries, fields=parsed_fields)
    elif format == 'csv':
        content = export_to_csv(entries, fields=parsed_fields)
    else:
        return {
            'success': False,
            'format': format,
            'entry_count': 0,
            'output_file': output_file,
            'error': f'Unsupported format: {format}',
        }

    # 写入文件或返回 stdout
    if output_file:
        try:
            # 确保目录存在
            parent_dir = os.path.dirname(os.path.abspath(output_file))
            os.makedirs(parent_dir, exist_ok=True)
            # 原子写入：先写 tmp 文件，再 os.replace 覆盖（避免崩溃时文件损坏）
            tmp_file = output_file + '.tmp'
            with open(tmp_file, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_file, output_file)
            return {
                'success': True,
                'format': format,
                'entry_count': len(entries),
                'output_file': output_file,
            }
        except OSError as e:
            # 清理可能残留的 tmp 文件
            try:
                if os.path.exists(tmp_file):
                    os.unlink(tmp_file)
            except OSError:
                pass
            return {
                'success': False,
                'format': format,
                'entry_count': len(entries),
                'output_file': output_file,
                'error': str(e)[:200],
            }
    else:
        return {
            'success': True,
            'format': format,
            'entry_count': len(entries),
            'output_file': None,
            'output': content,
        }


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='搜索历史导出 — JSON/CSV 格式'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # export 子命令
    p_export = sub.add_parser('export', help='导出搜索历史')
    p_export.add_argument('--format', default='json',
                          choices=['json', 'csv'],
                          help='导出格式（默认 json）')
    p_export.add_argument('--days', type=int, default=None,
                          help='仅导出最近 N 天')
    p_export.add_argument('--fields', default=None,
                          help=f'字段筛选（comma-separated，支持: '
                               f'{",".join(SUPPORTED_FIELDS)}）')
    p_export.add_argument('--saved-only', action='store_true',
                          help='仅导出已收藏的记录')
    p_export.add_argument('--output', default=None,
                          help='输出文件路径（默认 stdout）')

    # list-fields 子命令
    sub.add_parser('list-fields', help='列出支持的字段名')

    args = parser.parse_args()

    if args.command == 'export':
        result = export_history(
            format=args.format,
            days=args.days,
            fields=args.fields,
            saved_only=args.saved_only,
            output_file=args.output,
        )
        if result['success']:
            if result['output_file']:
                print(f"已导出 {result['entry_count']} 条记录到 "
                      f"{result['output_file']}")
            else:
                print(result['output'])
            return 0
        else:
            print(f"错误: {result.get('error', '未知错误')}", file=sys.stderr)
            return 1

    if args.command == 'list-fields':
        print('Supported fields:')
        for f in SUPPORTED_FIELDS:
            mark = ' (default)' if f in DEFAULT_FIELDS else ''
            print(f'  - {f}{mark}')
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
