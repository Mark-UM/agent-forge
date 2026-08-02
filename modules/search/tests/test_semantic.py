#!/usr/bin/env python3
"""semantic.py 单元测试 — ChromaDB 集成 + 降级链。

覆盖维度：
  - _check_chromadb: 可用 / 不可用
  - _build_document: 完整 entry / 空 entry / 非 dict results
  - index_history: 无文件 / 正常 / chromadb 不可用降级
  - query_similar: 空 query / 正常 / chromadb 不可用降级 / 异常降级
  - _fallback_keyword_search: 命中 / 未命中 / 无文件
  - collection_stats: 可用 / 不可用
  - CLI: index / query / stats
"""
import sys
import os
import json
import unittest
import tempfile
from unittest.mock import patch, MagicMock
from io import StringIO

_HERE = os.path.dirname(os.path.abspath(__file__))
_SEARCH_DIR = os.path.dirname(_HERE)
if _SEARCH_DIR not in sys.path:
    sys.path.insert(0, _SEARCH_DIR)

import semantic
from semantic import (
    _check_chromadb, _get_collection, _build_document,
    index_history, query_similar, _fallback_keyword_search,
    collection_stats, _load_entries,
)


SAMPLE_ENTRY = {
    'query': 'React useEffect cleanup best practices',
    'timestamp': '2026-07-20T10:00:00',
    'layers_used': ['duckduckgo', 'searxng'],
    'results_count': 5,
    'score': 8.5,
    'satisfied': True,
    'saved': False,
    'top_results': [
        {
            'title': 'React useEffect Cleanup Best Practices (2026)',
            'url': 'https://react.dev/docs/hooks-effect',
            'snippet': 'useEffect cleanup function runs on unmount.',
            'source': 'official-docs',
        },
        {
            'title': 'Understanding React Hooks',
            'url': 'https://dev.to/react/useeffect',
            'snippet': 'A deep dive into useEffect.',
            'source': 'blog',
        },
    ],
}


def _reset_module_state():
    """重置 semantic 模块的全局状态。"""
    semantic._chromadb_available = None
    semantic._client = None
    semantic._collection = None


class TestCheckChromadb(unittest.TestCase):
    """_check_chromadb 测试。"""

    def setUp(self):
        _reset_module_state()

    def test_returns_bool(self):
        result = _check_chromadb()
        self.assertIsInstance(result, bool)

    def test_caches_result(self):
        """第二次调用应使用缓存。"""
        with patch('builtins.__import__', side_effect=ImportError):
            result1 = _check_chromadb()
        # 缓存已设置，第二次不再 import
        result2 = _check_chromadb()
        self.assertEqual(result1, result2)

    def test_import_error_returns_false(self):
        """chromadb 未安装 → False。"""
        with patch.dict(sys.modules, {'chromadb': None}):
            _reset_module_state()
            result = _check_chromadb()
        self.assertFalse(result)


class TestBuildDocument(unittest.TestCase):
    """_build_document 测试。"""

    def test_complete_entry(self):
        doc = _build_document(SAMPLE_ENTRY)
        self.assertIn('React useEffect', doc)
        self.assertIn('React useEffect Cleanup', doc)
        # 文档应包含 title 和 snippet
        self.assertIn('useEffect cleanup function', doc)

    def test_empty_entry(self):
        """空 entry 仅含空 query，join 后为空字符串。"""
        doc = _build_document({})
        # query 默认 ''，' | '.join(['']) = ''
        self.assertEqual(doc, '')

    def test_entry_with_no_top_results(self):
        entry = {'query': 'test query'}
        doc = _build_document(entry)
        self.assertEqual(doc, 'test query')

    def test_entry_with_non_list_top_results(self):
        entry = {'query': 'test', 'top_results': 'not a list'}
        doc = _build_document(entry)
        self.assertEqual(doc, 'test')

    def test_entry_with_non_dict_in_top_results(self):
        entry = {
            'query': 'test',
            'top_results': ['not dict', 42, {'title': 'OK'}],
        }
        doc = _build_document(entry)
        self.assertIn('test', doc)
        self.assertIn('OK', doc)

    def test_snippet_truncated(self):
        long_snippet = 'x' * 500
        entry = {
            'query': 'q',
            'top_results': [{'title': 't', 'snippet': long_snippet}],
        }
        doc = _build_document(entry)
        # snippet 被截断到 200 字符
        self.assertLessEqual(len(doc), 300)  # query + title + 200 snippet


class TestIndexHistory(unittest.TestCase):
    """index_history 测试。"""

    def setUp(self):
        _reset_module_state()

    def test_no_log_files(self):
        with patch('semantic._all_log_files', return_value=[]):
            result = index_history()
        self.assertTrue(result['success'])
        self.assertEqual(result['indexed_count'], 0)

    def test_no_entries(self):
        with patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=[]):
            result = index_history()
        self.assertTrue(result['success'])
        self.assertEqual(result['indexed_count'], 0)

    def test_chromadb_not_available_returns_fallback(self):
        with patch('semantic._check_chromadb', return_value=False), \
             patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=[SAMPLE_ENTRY]):
            result = index_history()
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback')
        self.assertIn('error', result)

    def test_index_success_with_mock_collection(self):
        """mock chromadb collection 成功索引。"""
        mock_collection = MagicMock()
        mock_collection.upsert = MagicMock()

        with patch('semantic._check_chromadb', return_value=True), \
             patch('semantic._get_collection', return_value=mock_collection), \
             patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=[SAMPLE_ENTRY]):
            result = index_history()

        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'chromadb')
        self.assertEqual(result['indexed_count'], 1)
        mock_collection.upsert.assert_called_once()

        # 验证 upsert 参数
        call_args = mock_collection.upsert.call_args
        self.assertIn('ids', call_args.kwargs)
        self.assertIn('documents', call_args.kwargs)
        self.assertIn('metadatas', call_args.kwargs)
        self.assertEqual(len(call_args.kwargs['ids']), 1)

    def test_index_batch_processing(self):
        """超过 BATCH_SIZE 时分批。"""
        entries = [dict(SAMPLE_ENTRY, timestamp=f'2026-07-{i:02d}T10:00:00')
                   for i in range(1, 250)]
        mock_collection = MagicMock()

        with patch('semantic._check_chromadb', return_value=True), \
             patch('semantic._get_collection', return_value=mock_collection), \
             patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=entries):
            result = index_history()

        self.assertTrue(result['success'])
        self.assertEqual(result['indexed_count'], 249)
        # 249 / 100 = 3 批次
        self.assertEqual(mock_collection.upsert.call_count, 3)

    def test_index_exception_returns_fallback(self):
        """collection.upsert 异常时返回 fallback。"""
        mock_collection = MagicMock()
        mock_collection.upsert.side_effect = RuntimeError('DB error')

        with patch('semantic._check_chromadb', return_value=True), \
             patch('semantic._get_collection', return_value=mock_collection), \
             patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=[SAMPLE_ENTRY]):
            result = index_history()

        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback')
        self.assertIn('error', result)

    def test_index_skips_empty_entries(self):
        """空 query 和空 document 的 entry 被跳过。"""
        entries = [
            SAMPLE_ENTRY,
            {'query': '', 'timestamp': '2026-07-01'},
            {'query': '   ', 'timestamp': '2026-07-02'},
            {'timestamp': '2026-07-03'},  # 无 query
        ]
        mock_collection = MagicMock()

        with patch('semantic._check_chromadb', return_value=True), \
             patch('semantic._get_collection', return_value=mock_collection), \
             patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=entries):
            result = index_history()

        self.assertTrue(result['success'])
        self.assertEqual(result['indexed_count'], 1)
        self.assertEqual(result['skipped_count'], 3)


class TestQuerySimilar(unittest.TestCase):
    """query_similar 测试。"""

    def setUp(self):
        _reset_module_state()

    def test_empty_query_returns_fallback(self):
        result = query_similar('')
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-keyword')
        self.assertIn('error', result)

    def test_none_query(self):
        result = query_similar(None)
        self.assertFalse(result['success'])

    def test_whitespace_query(self):
        result = query_similar('   ')
        self.assertFalse(result['success'])

    def test_chromadb_not_available_falls_back(self):
        with patch('semantic._get_collection', return_value=None), \
             patch('semantic._fallback_keyword_search',
                   return_value={'success': True, 'mode': 'fallback-keyword',
                                 'results': [], 'count': 0}):
            result = query_similar('React')
        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'fallback-keyword')

    def test_query_success_with_mock_collection(self):
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [['id1|query1', 'id2|query2']],
            'documents': [['doc1 content', 'doc2 content']],
            'metadatas': [[
                {'query': 'query1', 'timestamp': '2026-07-01',
                 'score': 8.0, 'saved': 1},
                {'query': 'query2', 'timestamp': '2026-07-02',
                 'score': 7.0, 'saved': 0},
            ]],
            'distances': [[0.2, 0.5]],
        }

        with patch('semantic._get_collection', return_value=mock_collection):
            result = query_similar('React', limit=5)

        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'chromadb')
        self.assertEqual(len(result['results']), 2)

        first = result['results'][0]
        self.assertEqual(first['id'], 'id1|query1')
        self.assertEqual(first['query'], 'query1')
        self.assertEqual(first['score'], 8.0)
        self.assertTrue(first['saved'])
        self.assertAlmostEqual(first['distance'], 0.2)
        self.assertAlmostEqual(first['similarity'], 0.8)

    def test_query_with_where_filter(self):
        """where 参数传递给 collection.query。"""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]],
        }

        with patch('semantic._get_collection', return_value=mock_collection):
            result = query_similar('React', where={'saved': 1})

        self.assertTrue(result['success'])
        call_kwargs = mock_collection.query.call_args.kwargs
        self.assertEqual(call_kwargs['where'], {'saved': 1})

    def test_query_exception_falls_back(self):
        """collection.query 异常时降级。"""
        mock_collection = MagicMock()
        mock_collection.query.side_effect = RuntimeError('Query failed')

        with patch('semantic._get_collection', return_value=mock_collection), \
             patch('semantic._fallback_keyword_search',
                   return_value={'success': True, 'mode': 'fallback-keyword',
                                 'results': [], 'count': 0}):
            result = query_similar('React')
        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'fallback-keyword')

    def test_query_limit_clamped(self):
        """limit 被限制在 [1, 50] 范围。"""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]],
        }

        with patch('semantic._get_collection', return_value=mock_collection):
            query_similar('React', limit=100)
            n_results = mock_collection.query.call_args.kwargs['n_results']
            self.assertEqual(n_results, 50)

            query_similar('React', limit=0)
            n_results = mock_collection.query.call_args.kwargs['n_results']
            self.assertEqual(n_results, 1)

    def test_query_empty_results(self):
        """query 返回空结果。"""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]],
        }

        with patch('semantic._get_collection', return_value=mock_collection):
            result = query_similar('React')

        self.assertTrue(result['success'])
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['results'], [])


class TestFallbackKeywordSearch(unittest.TestCase):
    """_fallback_keyword_search 测试。"""

    def setUp(self):
        _reset_module_state()

    def test_no_files(self):
        with patch('semantic._all_log_files', return_value=[]):
            result = _fallback_keyword_search('React', limit=5)
        self.assertFalse(result['success'])
        self.assertEqual(result['mode'], 'fallback-keyword')
        self.assertIn('error', result)

    def test_match_found(self):
        entries = [SAMPLE_ENTRY]
        with patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=entries):
            result = _fallback_keyword_search('React', limit=5)
        self.assertTrue(result['success'])
        self.assertEqual(result['mode'], 'fallback-keyword')
        self.assertEqual(len(result['results']), 1)
        self.assertEqual(result['results'][0]['query'], SAMPLE_ENTRY['query'])

    def test_no_match(self):
        entries = [SAMPLE_ENTRY]
        with patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=entries):
            result = _fallback_keyword_search('Python', limit=5)
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 0)

    def test_limit_applied(self):
        entries = [
            dict(SAMPLE_ENTRY, timestamp=f'2026-07-{i:02d}T10:00:00',
                 query=f'React hooks {i}')
            for i in range(1, 10)
        ]
        with patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=entries):
            result = _fallback_keyword_search('React', limit=3)
        self.assertEqual(len(result['results']), 3)

    def test_results_sorted_by_timestamp_desc(self):
        entries = [
            dict(SAMPLE_ENTRY, timestamp='2026-07-01T10:00:00', query='React old'),
            dict(SAMPLE_ENTRY, timestamp='2026-07-20T10:00:00', query='React new'),
            dict(SAMPLE_ENTRY, timestamp='2026-07-10T10:00:00', query='React mid'),
        ]
        with patch('semantic._all_log_files', return_value=['/fake/file.jsonl']), \
             patch('semantic._load_entries', return_value=entries):
            result = _fallback_keyword_search('React', limit=10)
        timestamps = [r['timestamp'] for r in result['results']]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))


class TestCollectionStats(unittest.TestCase):
    """collection_stats 测试。"""

    def setUp(self):
        _reset_module_state()

    def test_chromadb_not_available(self):
        with patch('semantic._get_collection', return_value=None):
            result = collection_stats()
        self.assertFalse(result['available'])
        self.assertEqual(result['mode'], 'fallback')

    def test_stats_success(self):
        mock_collection = MagicMock()
        mock_collection.count.return_value = 42

        with patch('semantic._get_collection', return_value=mock_collection):
            result = collection_stats()
        self.assertTrue(result['available'])
        self.assertEqual(result['count'], 42)
        self.assertEqual(result['mode'], 'chromadb')

    def test_stats_exception(self):
        mock_collection = MagicMock()
        mock_collection.count.side_effect = RuntimeError('DB error')

        with patch('semantic._get_collection', return_value=mock_collection):
            result = collection_stats()
        self.assertFalse(result['available'])
        self.assertIn('error', result)


class TestCLI(unittest.TestCase):
    """CLI 接口测试。"""

    def setUp(self):
        _reset_module_state()

    def test_cli_index_no_data(self):
        from semantic import _cli
        with patch('semantic._all_log_files', return_value=[]):
            with patch('sys.argv', ['semantic.py', 'index']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    exit_code = _cli()
                self.assertEqual(exit_code, 0)
                data = json.loads(buf.getvalue())
                self.assertTrue(data['success'])

    def test_cli_query_empty_query(self):
        """空 query 返回非零退出码。"""
        from semantic import _cli
        with patch('sys.argv', ['semantic.py', 'query', '--query', '']):
            buf = StringIO()
            with patch('sys.stdout', new=buf):
                exit_code = _cli()
            self.assertEqual(exit_code, 1)

    def test_cli_query_success_via_fallback(self):
        """通过 fallback 模式查询成功。"""
        from semantic import _cli
        with patch('semantic._get_collection', return_value=None), \
             patch('semantic._fallback_keyword_search',
                   return_value={'success': True, 'mode': 'fallback-keyword',
                                 'results': [], 'count': 0}):
            with patch('sys.argv', ['semantic.py', 'query', '--query', 'React']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    exit_code = _cli()
                self.assertEqual(exit_code, 0)

    def test_cli_stats(self):
        from semantic import _cli
        with patch('semantic._get_collection', return_value=None):
            with patch('sys.argv', ['semantic.py', 'stats']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    exit_code = _cli()
                self.assertEqual(exit_code, 0)
                data = json.loads(buf.getvalue())
                self.assertFalse(data['available'])

    def test_cli_query_saved_only_filter(self):
        """--saved-only 触发 where 过滤。"""
        mock_collection = MagicMock()
        mock_collection.query.return_value = {
            'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]],
        }
        with patch('semantic._get_collection', return_value=mock_collection):
            from semantic import _cli
            with patch('sys.argv', ['semantic.py', 'query', '--query', 'React',
                                     '--saved-only']):
                buf = StringIO()
                with patch('sys.stdout', new=buf):
                    exit_code = _cli()
                self.assertEqual(exit_code, 0)
                call_kwargs = mock_collection.query.call_args.kwargs
                self.assertEqual(call_kwargs['where'], {'saved': 1})


class TestLoadEntries(unittest.TestCase):
    """_load_entries 测试。"""

    def test_no_files(self):
        self.assertEqual(_load_entries([]), [])

    def test_loads_entries(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl',
                                          delete=False, encoding='utf-8') as f:
            f.write(json.dumps(SAMPLE_ENTRY) + '\n')
            f.write('not json line\n')
            f.write(json.dumps({'query': 'second'}) + '\n')
            temp_path = f.name
        try:
            entries = _load_entries([temp_path])
            self.assertEqual(len(entries), 2)  # 跳过 not json line
            self.assertEqual(entries[0]['query'], 'React useEffect cleanup best practices')
            self.assertEqual(entries[1]['query'], 'second')
        finally:
            os.unlink(temp_path)

    def test_days_limit_filters_old(self):
        """days_limit 过滤旧记录。"""
        from datetime import datetime
        old_entry = dict(SAMPLE_ENTRY,
                         timestamp='2020-01-01T10:00:00')
        recent_entry = dict(SAMPLE_ENTRY,
                            timestamp=datetime.now().isoformat())
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl',
                                          delete=False, encoding='utf-8') as f:
            f.write(json.dumps(old_entry) + '\n')
            f.write(json.dumps(recent_entry) + '\n')
            temp_path = f.name
        try:
            entries = _load_entries([temp_path], days_limit=30)
            self.assertEqual(len(entries), 1)
            self.assertIn(datetime.now().strftime('%Y-%m-%d'),
                          entries[0]['timestamp'])
        finally:
            os.unlink(temp_path)

    def test_oserror_skipped(self):
        """文件读取失败跳过，不抛异常。"""
        entries = _load_entries(['/nonexistent/file.jsonl'])
        self.assertEqual(entries, [])


class TestConstraints(unittest.TestCase):
    """约束遵守验证。"""

    def test_chromadb_lazy_import(self):
        """chromadb 仅在 _check_chromadb / _get_collection 中被 import。"""
        source_file = os.path.join(_SEARCH_DIR, 'semantic.py')
        with open(source_file, 'r', encoding='utf-8') as f:
            content = f.read()
        # 不应有顶层 import chromadb
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('import chromadb') or \
               stripped.startswith('from chromadb'):
                # 仅在函数体内允许
                self.assertTrue(line.startswith(' '),
                                f'顶层 chromadb import at line {i}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
