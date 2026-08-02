#!/usr/bin/env python3
"""语义检索历史 — ChromaDB 集成。

设计原则：
- Phase P1 唯一允许引入外部依赖：chromadb
- 失败时优雅降级到 v3 的 find 子命令（关键词匹配）
- 持久化路径：_runtime/search/chroma/
- 仅索引 query + top_results titles + snippets（避免 PII 上传 embedding 模型）

降级链：
1. chromadb 未安装 → ImportError → 降级到 find
2. chromadb 初始化失败 → 降级到 find
3. embedding 模型加载失败 → 降级到 find
4. query 失败 → 降级到 find

Usage:
  python semantic.py index --days 30
  python semantic.py query "React hooks" --limit 5
  python semantic.py stats
  python -m modules.search.tests.test_semantic
"""
import sys
import os
import json
import argparse
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── 路径常量 ────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOG_DIR = os.path.join(_PROJECT_ROOT, '_runtime', 'search')
_CHROMA_DIR = os.path.join(_LOG_DIR, 'chroma')
_COLLECTION_NAME = 'search_history'

# 延迟 import chromadb，避免顶层 import 失败阻塞整个模块
_chromadb_available = None
_client = None
_collection = None


def _check_chromadb():
    """检查 chromadb 是否可用。延迟 import + 缓存结果。

    Returns:
        bool: True if chromadb 可用
    """
    global _chromadb_available
    if _chromadb_available is not None:
        return _chromadb_available
    try:
        import chromadb  # noqa: F401
        _chromadb_available = True
    except ImportError:
        _chromadb_available = False
    return _chromadb_available


def _get_collection():
    """获取或初始化 ChromaDB collection。失败返回 None。"""
    global _client, _collection
    if _collection is not None:
        return _collection

    if not _check_chromadb():
        return None

    try:
        import chromadb
        os.makedirs(_CHROMA_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=_CHROMA_DIR)
        # get_or_create 避免重复创建报错
        _collection = _client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={'description': 'Search history semantic index'},
        )
        return _collection
    except Exception as e:
        print(f'警告: ChromaDB 初始化失败 ({e})', file=sys.stderr)
        _collection = None
        return None


def _all_log_files():
    """列出所有 search_history.YYYY-MM-DD.jsonl 文件。"""
    if not os.path.exists(_LOG_DIR):
        return []
    files = []
    for name in os.listdir(_LOG_DIR):
        if name.startswith('search_history.') and name.endswith('.jsonl'):
            files.append(os.path.join(_LOG_DIR, name))
    files.sort(reverse=True)  # 最新在前
    return files


def _load_entries(files, days_limit=None):
    """从日志文件加载条目。"""
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
                        if cutoff is not None:
                            ts_str = entry.get('timestamp', '')
                            try:
                                ts = datetime.fromisoformat(ts_str)
                                if ts < cutoff:
                                    continue
                            except (ValueError, TypeError):
                                pass
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return entries


def _build_document(entry):
    """将 entry 转换为可被 embedding 的文档字符串。

    索引内容：query + top_results 的 titles + snippets（前 N 条）
    """
    parts = [str(entry.get('query', ''))]
    top_results = entry.get('top_results', [])
    if isinstance(top_results, list):
        for r in top_results[:5]:
            if not isinstance(r, dict):
                continue
            title = str(r.get('title', ''))
            snippet = str(r.get('snippet', ''))
            if title:
                parts.append(title)
            if snippet:
                parts.append(snippet[:200])  # 截断避免文档过长
    return ' | '.join(parts)


def index_history(days_limit=None):
    """将历史搜索记录索引到 ChromaDB。

    Args:
        days_limit: 仅索引最近 N 天的记录（None = 全部）

    Returns:
        dict: {
            'success': bool,
            'mode': str,  # 'chromadb' | 'fallback'
            'indexed_count': int,
            'skipped_count': int,
            'error': str,  # 仅失败时存在
        }
    """
    files = _all_log_files()
    if not files:
        return {
            'success': True,
            'mode': 'chromadb' if _check_chromadb() else 'fallback',
            'indexed_count': 0,
            'skipped_count': 0,
        }

    entries = _load_entries(files, days_limit=days_limit)
    if not entries:
        return {
            'success': True,
            'mode': 'chromadb' if _check_chromadb() else 'fallback',
            'indexed_count': 0,
            'skipped_count': 0,
        }

    collection = _get_collection()
    if collection is None:
        # 降级：不索引，返回 fallback 模式
        return {
            'success': False,
            'mode': 'fallback',
            'indexed_count': 0,
            'skipped_count': len(entries),
            'error': 'ChromaDB not available',
        }

    indexed = 0
    skipped = 0
    batch_ids = []
    batch_documents = []
    batch_metadatas = []

    try:
        for entry in entries:
            entry_id = str(entry.get('timestamp', '')) + '|' + \
                       str(entry.get('query', ''))[:50]
            if not entry_id or entry_id == '|':
                skipped += 1
                continue

            document = _build_document(entry)
            if not document.strip():
                skipped += 1
                continue

            metadata = {
                'query': str(entry.get('query', ''))[:200],
                'timestamp': str(entry.get('timestamp', '')),
                'score': float(entry.get('score', 0)),
                'saved': 1 if entry.get('saved') else 0,
                'layers_used': ','.join(entry.get('layers_used', [])),
                'results_count': int(entry.get('results_count', 0)),
            }

            batch_ids.append(entry_id)
            batch_documents.append(document)
            batch_metadatas.append(metadata)
            indexed += 1

        # 批量 upsert（chromadb 推荐批量操作）
        if batch_ids:
            # 分批处理，每批最多 100 条，避免单次请求过大
            BATCH_SIZE = 100
            for i in range(0, len(batch_ids), BATCH_SIZE):
                end = i + BATCH_SIZE
                collection.upsert(
                    ids=batch_ids[i:end],
                    documents=batch_documents[i:end],
                    metadatas=batch_metadatas[i:end],
                )

        return {
            'success': True,
            'mode': 'chromadb',
            'indexed_count': indexed,
            'skipped_count': skipped,
        }
    except Exception as e:
        print(f'警告: ChromaDB 索引失败 ({e})', file=sys.stderr)
        return {
            'success': False,
            'mode': 'fallback',
            'indexed_count': 0,
            'skipped_count': len(entries),
            'error': str(e)[:200],
        }


def query_similar(query, limit=5, where=None):
    """查询语义相似的历史搜索。

    Args:
        query: 用户查询
        limit: 返回结果数（默认 5）
        where: ChromaDB metadata 过滤条件（None = 不过滤）

    Returns:
        dict: {
            'success': bool,
            'mode': str,  # 'chromadb' | 'fallback-keyword'
            'results': list[dict],  # [{id, distance, query, timestamp, score, saved}]
            'count': int,
            'error': str,  # 仅失败时存在
        }
    """
    if not isinstance(query, str) or not query.strip():
        return {
            'success': False,
            'mode': 'fallback-keyword',
            'results': [],
            'count': 0,
            'error': 'Empty query',
        }

    collection = _get_collection()
    if collection is None:
        # 降级到关键词 find
        return _fallback_keyword_search(query, limit)

    try:
        # chromadb query
        kwargs = {
            'query_texts': [query],
            'n_results': min(max(1, limit), 50),
        }
        if where is not None:
            kwargs['where'] = where

        results = collection.query(**kwargs)

        # 解析结果
        parsed = []
        ids_list = results.get('ids', [[]])
        documents_list = results.get('documents', [[]])
        metadatas_list = results.get('metadatas', [[]])
        distances_list = results.get('distances', [[]])

        if not ids_list or not ids_list[0]:
            return {
                'success': True,
                'mode': 'chromadb',
                'results': [],
                'count': 0,
            }

        for i, entry_id in enumerate(ids_list[0]):
            metadata = metadatas_list[0][i] if i < len(metadatas_list[0]) else {}
            distance = distances_list[0][i] if i < len(distances_list[0]) else 0
            document = documents_list[0][i] if i < len(documents_list[0]) else ''

            parsed.append({
                'id': entry_id,
                'distance': float(distance),
                # distance 越小越相似（cosine distance）
                'similarity': max(0.0, 1.0 - float(distance)),
                'query': metadata.get('query', ''),
                'timestamp': metadata.get('timestamp', ''),
                'score': metadata.get('score', 0),
                'saved': bool(metadata.get('saved', 0)),
                'document': document[:200],
            })

        return {
            'success': True,
            'mode': 'chromadb',
            'results': parsed,
            'count': len(parsed),
        }
    except Exception as e:
        print(f'警告: ChromaDB query 失败，降级到关键词搜索 ({e})',
              file=sys.stderr)
        return _fallback_keyword_search(query, limit)


def _fallback_keyword_search(query, limit=5):
    """降级到 v3 的 find 子命令逻辑（关键词匹配）。

    Args:
        query: 用户查询
        limit: 返回结果数

    Returns:
        dict: 同 query_similar 返回格式
    """
    files = _all_log_files()
    if not files:
        return {
            'success': False,
            'mode': 'fallback-keyword',
            'results': [],
            'count': 0,
            'error': 'No history files',
        }

    entries = _load_entries(files)
    query_lower = query.lower()

    matched = []
    for entry in entries:
        entry_query = str(entry.get('query', '')).lower()
        if query_lower in entry_query or entry_query in query_lower:
            matched.append({
                'id': str(entry.get('timestamp', '')),
                'distance': 1.0,  # 关键词匹配无距离概念
                'similarity': 0.5,  # 标记为低相似度
                'query': entry.get('query', ''),
                'timestamp': entry.get('timestamp', ''),
                'score': entry.get('score', 0),
                'saved': entry.get('saved', False),
                'document': '',
            })

    # 按时间倒序
    matched.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

    return {
        'success': True,
        'mode': 'fallback-keyword',
        'results': matched[:limit],
        'count': min(len(matched), limit),
    }


def collection_stats():
    """获取 collection 统计信息。"""
    collection = _get_collection()
    if collection is None:
        return {
            'mode': 'fallback',
            'available': False,
            'count': 0,
            'error': 'ChromaDB not available',
        }

    try:
        count = collection.count()
        return {
            'mode': 'chromadb',
            'available': True,
            'count': count,
            'path': _CHROMA_DIR,
        }
    except Exception as e:
        return {
            'mode': 'fallback',
            'available': False,
            'count': 0,
            'error': str(e)[:200],
        }


def _cli():
    """命令行接口。"""
    parser = argparse.ArgumentParser(
        description='语义检索历史 — ChromaDB 集成（失败降级到关键词搜索）'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # index 子命令
    p_index = sub.add_parser('index', help='将历史搜索索引到 ChromaDB')
    p_index.add_argument('--days', type=int, default=None,
                         help='仅索引最近 N 天的记录（默认全部）')

    # query 子命令
    p_query = sub.add_parser('query', help='查询语义相似的历史搜索')
    p_query.add_argument('--query', required=True, help='查询字符串')
    p_query.add_argument('--limit', type=int, default=5,
                         help='返回结果数（默认 5）')
    p_query.add_argument('--saved-only', action='store_true',
                         help='仅查询已收藏的记录')

    # stats 子命令
    sub.add_parser('stats', help='查看 collection 统计')

    args = parser.parse_args()

    if args.command == 'index':
        result = index_history(days_limit=args.days)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['success'] else 1

    if args.command == 'query':
        where = {'saved': 1} if args.saved_only else None
        result = query_similar(args.query, limit=args.limit, where=where)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['success'] else 1

    if args.command == 'stats':
        result = collection_stats()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
