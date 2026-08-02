#!/usr/bin/env python3
"""export.py 单元测试 — JSON/CSV 导出 + 时间窗口 + 字段筛选 + 收藏过滤。

覆盖维度：
  - _all_log_files: 无目录 / 空目录 / 正常文件排序
  - _load_entries: 无文件 / 正常条目 / 时间过滤 / saved_only / 坏 JSON / IO 错误
  - _filter_fields: 完整 entry / 缺字段 / 非 dict / list & dict 序列化
  - export_to_json: 空 / 非列表 / 完整 / 字段筛选 / pretty vs compact
  - export_to_csv: 空 / 非列表 / UTF-8 BOM / 字段序列化（list→JSON、bool→str、None→''）
  - export_history: 无文件 / 格式校验 / days 过滤 / saved_only / output_file 成功+失败 / fields 校验
  - CLI: export json/csv / list-fields / 无效格式 / 文件输出
"""
import sys
import os
import json
import csv
import unittest
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from io import StringIO
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import export
from export import (
    SUPPORTED_FIELDS, DEFAULT_FIELDS,
    _all_log_files, _load_entries, _filter_fields,
    export_to_json, export_to_csv, export_history, _cli,
)


# ── 测试数据 ────────────────────────────────────────────────────
SAMPLE_ENTRY_FULL = {
    'timestamp': '2026-07-20T10:00:00',
    'query': 'React useEffect cleanup best practices',
    'score': 8.5,
    'satisfied': True,
    'saved': False,
    'results_count': 5,
    'layers_used': ['duckduckgo', 'searxng'],
    'top_results': [
        {
            'title': 'React useEffect Cleanup (2026)',
            'url': 'https://react.dev/docs/hooks-effect',
            'snippet': 'useEffect cleanup runs on unmount.',
            'source': 'official-docs',
        },
    ],
}

SAMPLE_ENTRY_SAVED = {
    'timestamp': '2026-07-19T15:30:00',
    'query': 'DeepSeek V4 Pro context window',
    'score': 9.0,
    'satisfied': True,
    'saved': True,
    'results_count': 3,
    'layers_used': ['searxng'],
    'top_results': [],
}

SAMPLE_ENTRY_OLD = {
    'timestamp': '2026-06-01T08:00:00',  # 远早于 7 天
    'query': 'Old query',
    'score': 5.0,
    'satisfied': False,
    'saved': False,
    'results_count': 1,
    'layers_used': ['duckduckgo'],
    'top_results': [],
}


def _make_log_dir_with_entries(entries, dir_path):
    """在指定 dir_path 下写入 search_history.YYYY-MM-DD.jsonl 文件。"""
    today = datetime.now().strftime('%Y-%m-%d')
    fname = f'search_history.{today}.jsonl'
    fpath = os.path.join(dir_path, fname)
    with open(fpath, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    return fpath


class TestAllLogFiles(unittest.TestCase):
    """_all_log_files 测试。"""

    def test_no_log_dir_returns_empty(self):
        with patch('export._LOG_DIR', '/nonexistent/path/xyz'):
            result = _all_log_files()
        self.assertEqual(result, [])

    def test_empty_dir_returns_empty(self):
        tmp = tempfile.mkdtemp()
        try:
            with patch('export._LOG_DIR', tmp):
                result = _all_log_files()
            self.assertEqual(result, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_returns_matching_files_sorted_reverse(self):
        tmp = tempfile.mkdtemp()
        try:
            # 创建多个日志文件
            for date in ['2026-07-20', '2026-07-19', '2026-07-18']:
                fpath = os.path.join(tmp, f'search_history.{date}.jsonl')
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write('')
            with patch('export._LOG_DIR', tmp):
                result = _all_log_files()
            self.assertEqual(len(result), 3)
            # 倒序：最新在前
            self.assertIn('2026-07-20', result[0])
            self.assertIn('2026-07-19', result[1])
            self.assertIn('2026-07-18', result[2])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ignores_non_matching_files(self):
        tmp = tempfile.mkdtemp()
        try:
            # 不匹配的文件
            for name in ['other.jsonl', 'search_history.txt',
                         'search_history.jsonl.bak', 'history.jsonl']:
                with open(os.path.join(tmp, name), 'w', encoding='utf-8') as f:
                    f.write('')
            # 匹配的文件
            with open(os.path.join(tmp, 'search_history.2026-07-20.jsonl'),
                      'w', encoding='utf-8') as f:
                f.write('')
            with patch('export._LOG_DIR', tmp):
                result = _all_log_files()
            self.assertEqual(len(result), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLoadEntries(unittest.TestCase):
    """_load_entries 测试。"""

    def test_no_files_returns_empty(self):
        self.assertEqual(_load_entries([]), [])

    def test_loads_all_entries_no_filter(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_entries(files)
            self.assertEqual(len(entries), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_days_filter_excludes_old_entries(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_OLD], tmp)
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            # 仅最近 7 天
            entries = _load_entries(files, days_limit=7)
            # SAMPLE_ENTRY_OLD (2026-06-01) 应被排除
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['query'],
                             'React useEffect cleanup best practices')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_saved_only_filter(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_entries(files, saved_only=True)
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0]['saved'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invalid_json_line_skipped(self):
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(json.dumps(SAMPLE_ENTRY_FULL) + '\n')
                f.write('this is not json\n')
                f.write(json.dumps(SAMPLE_ENTRY_SAVED) + '\n')
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_entries(files)
            self.assertEqual(len(entries), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_empty_lines_skipped(self):
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write('\n')
                f.write('   \n')
                f.write(json.dumps(SAMPLE_ENTRY_FULL) + '\n')
                f.write('\n')
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            entries = _load_entries(files)
            self.assertEqual(len(entries), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_io_error_on_file_skipped(self):
        """文件读取失败时跳过该文件，不抛异常。"""
        with patch('builtins.open', side_effect=OSError('permission denied')):
            entries = _load_entries(['/fake/path.jsonl'])
        self.assertEqual(entries, [])

    def test_unicode_decode_error_on_file_skipped(self):
        """非 UTF-8 字节序列导致 UnicodeDecodeError 时跳过该文件。"""
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            # 写入有效 JSON + 非 UTF-8 字节
            with open(fpath, 'wb') as f:
                f.write(json.dumps(SAMPLE_ENTRY_FULL).encode('utf-8') + b'\n')
                f.write(b'\xff\xfe invalid utf-8\n')
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            # UnicodeDecodeError 应被捕获，整个文件被跳过
            entries = _load_entries(files)
            # 即使第一行有效，整文件因 UnicodeDecodeError 被跳过 → 0 条
            self.assertEqual(entries, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invalid_timestamp_not_filtered(self):
        """时间戳格式错误时不做时间过滤（保留条目）。"""
        tmp = tempfile.mkdtemp()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            fpath = os.path.join(tmp, f'search_history.{today}.jsonl')
            bad_entry = dict(SAMPLE_ENTRY_FULL)
            bad_entry['timestamp'] = 'not-a-date'
            with open(fpath, 'w', encoding='utf-8') as f:
                f.write(json.dumps(bad_entry) + '\n')
            with patch('export._LOG_DIR', tmp):
                files = _all_log_files()
            # 即使 days_limit=7，时间戳解析失败也不会过滤掉
            entries = _load_entries(files, days_limit=7)
            self.assertEqual(len(entries), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFilterFields(unittest.TestCase):
    """_filter_fields 测试。"""

    def test_complete_entry_all_fields_present(self):
        result = _filter_fields(SAMPLE_ENTRY_FULL, SUPPORTED_FIELDS)
        for f in SUPPORTED_FIELDS:
            self.assertIn(f, result)

    def test_missing_fields_filled_with_empty(self):
        entry = {'query': 'test', 'score': 5.0}
        result = _filter_fields(entry, ['query', 'score', 'timestamp', 'saved'])
        self.assertEqual(result['query'], 'test')
        self.assertEqual(result['score'], 5.0)
        self.assertEqual(result['timestamp'], '')
        self.assertEqual(result['saved'], '')

    def test_non_dict_entry_returns_empty_dict(self):
        result = _filter_fields('not a dict', SUPPORTED_FIELDS)
        self.assertEqual(result, {})

    def test_non_dict_entry_none(self):
        result = _filter_fields(None, SUPPORTED_FIELDS)
        self.assertEqual(result, {})

    def test_list_field_serialized_to_json(self):
        result = _filter_fields(SAMPLE_ENTRY_FULL, ['layers_used'])
        # list 被序列化为 JSON 字符串
        self.assertIsInstance(result['layers_used'], str)
        parsed = json.loads(result['layers_used'])
        self.assertEqual(parsed, ['duckduckgo', 'searxng'])

    def test_dict_field_serialized_to_json(self):
        result = _filter_fields(SAMPLE_ENTRY_FULL, ['top_results'])
        self.assertIsInstance(result['top_results'], str)
        parsed = json.loads(result['top_results'])
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]['title'], 'React useEffect Cleanup (2026)')

    def test_scalar_fields_unchanged(self):
        result = _filter_fields(SAMPLE_ENTRY_FULL,
                                ['query', 'score', 'satisfied'])
        self.assertEqual(result['query'], SAMPLE_ENTRY_FULL['query'])
        self.assertEqual(result['score'], SAMPLE_ENTRY_FULL['score'])
        self.assertEqual(result['satisfied'], SAMPLE_ENTRY_FULL['satisfied'])

    def test_empty_fields_list_returns_empty_dict(self):
        result = _filter_fields(SAMPLE_ENTRY_FULL, [])
        self.assertEqual(result, {})


class TestExportToJson(unittest.TestCase):
    """export_to_json 测试。"""

    def test_empty_entries_returns_empty_array(self):
        self.assertEqual(export_to_json([]), '[]')

    def test_non_list_returns_empty_array(self):
        self.assertEqual(export_to_json('not a list'), '[]')
        self.assertEqual(export_to_json(None), '[]')

    def test_full_entries_pretty(self):
        entries = [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED]
        result = export_to_json(entries, pretty=True)
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]['query'],
                         'React useEffect cleanup best practices')
        # pretty 应有缩进
        self.assertIn('\n', result)

    def test_full_entries_compact(self):
        entries = [SAMPLE_ENTRY_FULL]
        result = export_to_json(entries, pretty=False)
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 1)
        # compact 无缩进
        self.assertNotIn('\n  ', result)

    def test_field_filtering(self):
        entries = [SAMPLE_ENTRY_FULL]
        result = export_to_json(entries, fields=['query', 'score'])
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 1)
        self.assertIn('query', parsed[0])
        self.assertIn('score', parsed[0])
        self.assertNotIn('timestamp', parsed[0])
        self.assertNotIn('saved', parsed[0])

    def test_field_filter_serializes_list(self):
        """字段筛选时 list 字段被序列化为 JSON 字符串。"""
        entries = [SAMPLE_ENTRY_FULL]
        result = export_to_json(entries, fields=['layers_used'])
        parsed = json.loads(result)
        # list 被转为 JSON 字符串（_filter_fields 行为）
        self.assertIsInstance(parsed[0]['layers_used'], str)


class TestExportToCsv(unittest.TestCase):
    """export_to_csv 测试。"""

    def test_empty_entries_returns_empty(self):
        self.assertEqual(export_to_csv([]), '')

    def test_non_list_returns_empty(self):
        self.assertEqual(export_to_csv('not a list'), '')

    def test_none_entries_returns_empty(self):
        self.assertEqual(export_to_csv(None), '')

    def test_utf8_bom_present(self):
        """CSV 输出包含 UTF-8 BOM 以便 Excel 正确识别。"""
        result = export_to_csv([SAMPLE_ENTRY_FULL])
        self.assertTrue(result.startswith('\ufeff'))

    def test_default_fields_used_when_none(self):
        result = export_to_csv([SAMPLE_ENTRY_FULL])
        lines = result.splitlines()
        # 第一行是 header（去掉 BOM）
        header = lines[0].lstrip('\ufeff')
        cols = header.split(',')
        for f in DEFAULT_FIELDS:
            self.assertIn(f, cols)

    def test_custom_fields(self):
        result = export_to_csv([SAMPLE_ENTRY_FULL], fields=['query', 'score'])
        lines = result.splitlines()
        header = lines[0].lstrip('\ufeff')
        cols = header.split(',')
        self.assertEqual(cols, ['query', 'score'])

    def test_fields_as_comma_separated_string(self):
        """fields 可以是逗号分隔字符串。"""
        result = export_to_csv([SAMPLE_ENTRY_FULL], fields='query,score')
        lines = result.splitlines()
        header = lines[0].lstrip('\ufeff')
        cols = header.split(',')
        self.assertEqual(cols, ['query', 'score'])

    def test_invalid_fields_fall_back_to_default(self):
        """所有字段名都无效 → 回退到 DEFAULT_FIELDS。"""
        result = export_to_csv([SAMPLE_ENTRY_FULL],
                               fields=['nonexistent', 'also_bad'])
        lines = result.splitlines()
        header = lines[0].lstrip('\ufeff')
        cols = header.split(',')
        for f in DEFAULT_FIELDS:
            self.assertIn(f, cols)

    def test_list_field_serialized_to_json_string(self):
        result = export_to_csv([SAMPLE_ENTRY_FULL], fields=['layers_used'])
        lines = result.splitlines()
        # 第二行是数据
        self.assertEqual(len(lines), 2)
        data_line = lines[1]
        # layers_used 被 _filter_fields 序列化为 JSON 字符串
        self.assertIn('duckduckgo', data_line)
        self.assertIn('searxng', data_line)

    def test_bool_field_converted_to_string(self):
        result = export_to_csv([SAMPLE_ENTRY_FULL], fields=['satisfied', 'saved'])
        lines = result.splitlines()
        data_line = lines[1]
        # satisfied=True → 'true'; saved=False → 'false'
        cols = data_line.split(',')
        self.assertIn('true', cols)
        self.assertIn('false', cols)

    def test_none_value_converted_to_empty_string(self):
        entry = {'query': None, 'score': None}
        result = export_to_csv([entry], fields=['query', 'score'])
        lines = result.splitlines()
        data_line = lines[1]
        cols = data_line.split(',')
        # None → ''
        self.assertIn('', cols)

    def test_non_dict_entry_skipped(self):
        """非 dict entry 行被跳过。"""
        entries = [SAMPLE_ENTRY_FULL, 'not a dict', SAMPLE_ENTRY_SAVED]
        result = export_to_csv(entries, fields=['query'])
        lines = result.splitlines()
        # header + 2 valid rows
        self.assertEqual(len(lines), 3)

    def test_invalid_row_skipped(self):
        """数据行写入失败时跳过（header 调用成功，数据行抛异常）。

        csv.DictWriter.writeheader() 内部也调用 writerow(dict) — header dict
        的 keys==values。我们用调用次数区分：第 1 次为 header，后续为数据行。
        """
        original_writerow = csv.DictWriter.writerow
        call_count = {'n': 0}

        def _fail_on_data_row(self, row):
            call_count['n'] += 1
            if call_count['n'] > 1:
                raise TypeError('mocked data row failure')
            return original_writerow(self, row)

        with patch('csv.DictWriter.writerow', side_effect=_fail_on_data_row,
                   autospec=True):
            result = export_to_csv([SAMPLE_ENTRY_FULL], fields=['query'])
            # header + 0 data rows（数据行被跳过）
            lines = result.splitlines()
            self.assertEqual(len(lines), 1)  # only header


class TestExportHistory(unittest.TestCase):
    """export_history 测试。"""

    def test_no_files_returns_empty_success(self):
        with patch('export._all_log_files', return_value=[]):
            result = export_history(format='json')
        self.assertTrue(result['success'])
        self.assertEqual(result['entry_count'], 0)
        self.assertEqual(result['output'], '[]')

    def test_no_files_csv_returns_empty(self):
        with patch('export._all_log_files', return_value=[]):
            result = export_history(format='csv')
        self.assertTrue(result['success'])
        self.assertEqual(result['entry_count'], 0)
        self.assertEqual(result['output'], '')

    def test_unsupported_format_returns_failure(self):
        """格式不支持时返回失败（需有 entries 才能触发 format 校验）。"""
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='xml')
            self.assertFalse(result['success'])
            self.assertIn('Unsupported format', result['error'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_json_export_with_entries(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json')
            self.assertTrue(result['success'])
            self.assertEqual(result['entry_count'], 1)
            self.assertIn('output', result)
            parsed = json.loads(result['output'])
            self.assertEqual(len(parsed), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_csv_export_with_entries(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='csv')
            self.assertTrue(result['success'])
            self.assertEqual(result['entry_count'], 1)
            self.assertIn('output', result)
            self.assertTrue(result['output'].startswith('\ufeff'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_days_filter(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_OLD], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json', days=7)
            self.assertTrue(result['success'])
            self.assertEqual(result['entry_count'], 1)
            parsed = json.loads(result['output'])
            self.assertEqual(parsed[0]['query'],
                             'React useEffect cleanup best practices')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_saved_only_filter(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json', saved_only=True)
            self.assertTrue(result['success'])
            self.assertEqual(result['entry_count'], 1)
            parsed = json.loads(result['output'])
            self.assertTrue(parsed[0]['saved'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fields_filter_json(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json',
                                        fields=['query', 'score'])
            self.assertTrue(result['success'])
            parsed = json.loads(result['output'])
            self.assertIn('query', parsed[0])
            self.assertIn('score', parsed[0])
            self.assertNotIn('timestamp', parsed[0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fields_as_comma_string(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='csv',
                                        fields='query,score')
            self.assertTrue(result['success'])
            lines = result['output'].splitlines()
            header = lines[0].lstrip('\ufeff')
            cols = header.split(',')
            self.assertEqual(cols, ['query', 'score'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fields_with_invalid_names_filtered_out(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='csv',
                                        fields=['query', 'bad_field', 'score'])
            self.assertTrue(result['success'])
            lines = result['output'].splitlines()
            header = lines[0].lstrip('\ufeff')
            cols = header.split(',')
            # bad_field 被过滤掉
            self.assertEqual(cols, ['query', 'score'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_all_invalid_fields_falls_back_to_default_csv(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='csv',
                                        fields=['bad1', 'bad2'])
            self.assertTrue(result['success'])
            lines = result['output'].splitlines()
            header = lines[0].lstrip('\ufeff')
            cols = header.split(',')
            for f in DEFAULT_FIELDS:
                self.assertIn(f, cols)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_output_file_write_success(self):
        tmp = tempfile.mkdtemp()
        out_file = os.path.join(tmp, 'export.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json',
                                         output_file=out_file)
            self.assertTrue(result['success'])
            self.assertEqual(result['output_file'], out_file)
            self.assertNotIn('output', result)
            # 文件已写入
            with open(out_file, 'r', encoding='utf-8') as f:
                content = f.read()
            parsed = json.loads(content)
            self.assertEqual(len(parsed), 1)
            # tmp 文件应已被清理（原子写入完成后删除）
            self.assertFalse(os.path.exists(out_file + '.tmp'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_output_file_atomic_write_no_tmp_residue(self):
        """原子写入成功后不留 .tmp 残留文件。"""
        tmp = tempfile.mkdtemp()
        out_file = os.path.join(tmp, 'deep', 'export.csv')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='csv',
                                         output_file=out_file)
            self.assertTrue(result['success'])
            self.assertTrue(os.path.exists(out_file))
            self.assertFalse(os.path.exists(out_file + '.tmp'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_output_file_creates_parent_dir(self):
        tmp = tempfile.mkdtemp()
        out_dir = os.path.join(tmp, 'subdir', 'deeper')
        out_file = os.path.join(out_dir, 'export.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json',
                                         output_file=out_file)
            self.assertTrue(result['success'])
            self.assertTrue(os.path.exists(out_file))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_output_file_write_failure_returns_error(self):
        """输出文件写入失败时返回 error（日志读取必须成功）。

        原子写入流程：先写 .tmp 文件，再 os.replace 覆盖。
        我们拦截 .tmp 文件的写入（'w' mode）但允许日志读取（'r' mode）。
        """
        tmp = tempfile.mkdtemp()
        out_file = os.path.join(tmp, 'export.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            # 拦截 .tmp 文件的写入（'w' mode）但允许日志读取（'r' mode）
            original_open = open

            def _fail_on_write(file, mode='r', *args, **kwargs):
                if 'w' in mode:
                    raise OSError('disk full')
                return original_open(file, mode, *args, **kwargs)

            with patch('export._LOG_DIR', tmp):
                with patch('builtins.open', side_effect=_fail_on_write):
                    result = export_history(format='json',
                                             output_file=out_file)
            self.assertFalse(result['success'])
            self.assertIn('error', result)
            self.assertIn('disk full', result['error'])
            # tmp 文件应被清理
            self.assertFalse(os.path.exists(out_file + '.tmp'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_entries_empty_after_filter(self):
        """所有条目被过滤后返回空输出。"""
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                result = export_history(format='json', saved_only=True)
            self.assertTrue(result['success'])
            self.assertEqual(result['entry_count'], 0)
            self.assertEqual(result['output'], '[]')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def _run_cli(self, argv):
        """运行 CLI 并捕获 stdout/stderr/exit_code。"""
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.argv = ['export.py'] + argv
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        try:
            exit_code = _cli()
            stdout = sys.stdout.getvalue()
            stderr = sys.stderr.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        return exit_code, stdout, stderr

    def test_list_fields(self):
        code, stdout, _ = self._run_cli(['list-fields'])
        self.assertEqual(code, 0)
        self.assertIn('Supported fields:', stdout)
        for f in SUPPORTED_FIELDS:
            self.assertIn(f, stdout)
        # default 字段应有标记
        self.assertIn('(default)', stdout)

    def test_export_json_to_stdout(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'json'])
            self.assertEqual(code, 0)
            parsed = json.loads(stdout)
            self.assertEqual(len(parsed), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_csv_to_stdout(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'csv'])
            self.assertEqual(code, 0)
            self.assertTrue(stdout.startswith('\ufeff'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_with_fields(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'json', '--fields', 'query,score'])
            self.assertEqual(code, 0)
            parsed = json.loads(stdout)
            self.assertIn('query', parsed[0])
            self.assertIn('score', parsed[0])
            self.assertNotIn('timestamp', parsed[0])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_saved_only(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_SAVED], tmp)
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'json', '--saved-only'])
            self.assertEqual(code, 0)
            parsed = json.loads(stdout)
            self.assertEqual(len(parsed), 1)
            self.assertTrue(parsed[0]['saved'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_days(self):
        tmp = tempfile.mkdtemp()
        try:
            _make_log_dir_with_entries(
                [SAMPLE_ENTRY_FULL, SAMPLE_ENTRY_OLD], tmp)
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'json', '--days', '7'])
            self.assertEqual(code, 0)
            parsed = json.loads(stdout)
            self.assertEqual(len(parsed), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_to_output_file(self):
        tmp = tempfile.mkdtemp()
        out_file = os.path.join(tmp, 'out.json')
        try:
            _make_log_dir_with_entries([SAMPLE_ENTRY_FULL], tmp)
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'json', '--output', out_file])
            self.assertEqual(code, 0)
            self.assertIn('已导出', stdout)
            self.assertIn('1', stdout)
            self.assertTrue(os.path.exists(out_file))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_export_no_files(self):
        """无历史文件时输出空 JSON 数组。"""
        tmp = tempfile.mkdtemp()
        try:
            with patch('export._LOG_DIR', tmp):
                code, stdout, _ = self._run_cli(
                    ['export', '--format', 'json'])
            self.assertEqual(code, 0)
            self.assertEqual(stdout.strip(), '[]')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invalid_format_choice(self):
        """argparse choices 限制 format 参数。"""
        with self.assertRaises(SystemExit):
            self._run_cli(['export', '--format', 'xml'])


class TestSupportedFieldsConstant(unittest.TestCase):
    """常量校验。"""

    def test_supported_fields_order(self):
        expected = [
            'timestamp', 'query', 'score', 'satisfied', 'saved',
            'results_count', 'layers_used', 'top_results',
        ]
        self.assertEqual(SUPPORTED_FIELDS, expected)

    def test_default_fields_subset_of_supported(self):
        for f in DEFAULT_FIELDS:
            self.assertIn(f, SUPPORTED_FIELDS)

    def test_default_fields_contains_expected(self):
        expected = ['timestamp', 'query', 'score', 'saved', 'results_count']
        self.assertEqual(DEFAULT_FIELDS, expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
