#!/usr/bin/env python3
"""文件索引器 — 复用 ChromaDB，索引 _data/memory/ 和 _runtime/reports/ 下的文件。

Design:
- 复用 modules/search/semantic.py 的 ChromaDB 集成模式
- 增量扫描（按 mtime 检测变更）
- 独立 collection（memory_files），不污染 search_history
- 降级：ChromaDB 不可用时返回空结果

Usage:
    from modules.orchestrator.file_indexer import index_directory, search_files

    index_directory(root="_data/memory")
    results = search_files("Python 技术栈", top_k=5)
"""
import os
import sys
import glob
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CHROMA_DIR = _PROJECT_ROOT / "_runtime" / "search" / "chroma"
_DEFAULT_COLLECTION = "memory_files"

# ── Lazy ChromaDB import (same pattern as semantic.py) ────────
_chromadb_available = None
_client = None
_collections = {}  # cache per collection name


def _check_chromadb() -> bool:
    """Check if chromadb is available. Lazy import + cache."""
    global _chromadb_available
    if _chromadb_available is not None:
        return _chromadb_available
    try:
        import chromadb  # noqa: F401
        _chromadb_available = True
    except ImportError:
        _chromadb_available = False
    return _chromadb_available


def _get_collection(collection_name: str = _DEFAULT_COLLECTION):
    """Get or init ChromaDB collection. Returns None on failure."""
    global _client, _collections

    if collection_name in _collections:
        return _collections[collection_name]

    if not _check_chromadb():
        return None

    try:
        import chromadb
        os.makedirs(str(_CHROMA_DIR), exist_ok=True)
        if _client is None:
            _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        collection = _client.get_or_create_collection(
            name=collection_name,
            metadata={"description": f"File index: {collection_name}"},
        )
        _collections[collection_name] = collection
        return collection
    except Exception as e:
        print(f"[file_indexer] WARNING: ChromaDB init failed: {e}", file=sys.stderr)
        return None


# ── File scanning ──────────────────────────────────────────────


def _scan_files(root: str, patterns: list) -> list:
    """Scan root directory for files matching patterns."""
    root_path = Path(root)
    if not root_path.exists():
        return []

    files = []
    for pattern in patterns:
        for f in root_path.rglob(pattern):
            if f.is_file():
                files.append(str(f))
    # Deduplicate
    return list(set(files))


def _file_hash(filepath: str) -> str:
    """Compute MD5 hash of file content for change detection."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_file_content(filepath: str, max_chars: int = 10000) -> str:
    """Read file content, truncate to max_chars."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read(max_chars)
        return content
    except (UnicodeDecodeError, IOError):
        return ""


# ── Public API ─────────────────────────────────────────────────


def index_directory(
    root: str,
    glob_patterns: list = None,
    incremental: bool = True,
    collection_name: str = _DEFAULT_COLLECTION,
) -> dict:
    """Index files in root directory to ChromaDB.

    Args:
        root: Directory to scan
        glob_patterns: File patterns to match (default: ["*.md", "*.txt"])
        incremental: If True, skip unchanged files (by mtime + hash)
        collection_name: ChromaDB collection name

    Returns:
        dict: {indexed_count, skipped_count, deleted_count, total_files}
    """
    if glob_patterns is None:
        glob_patterns = ["*.md", "*.txt"]

    collection = _get_collection(collection_name)
    if collection is None:
        return {
            "indexed_count": 0,
            "skipped_count": 0,
            "deleted_count": 0,
            "total_files": 0,
            "error": "ChromaDB not available",
        }

    files = _scan_files(root, glob_patterns)
    indexed = 0
    skipped = 0

    # Get existing indexed file IDs
    existing_ids = set()
    try:
        all_data = collection.get()
        existing_ids = set(all_data.get("ids", []))
    except Exception:
        pass

    current_ids = set()

    for filepath in files:
        # Use file path as document ID (sanitized)
        doc_id = hashlib.md5(filepath.encode("utf-8")).hexdigest()[:16]
        current_ids.add(doc_id)

        try:
            mtime = os.path.getmtime(filepath)
            content = _read_file_content(filepath)
            content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

            # Check if file changed (incremental)
            if incremental and doc_id in existing_ids:
                try:
                    meta = collection.get(ids=[doc_id])
                    stored_hash = meta.get("metadatas", [{}])[0].get("content_hash", "")
                    if stored_hash == content_hash:
                        skipped += 1
                        continue
                except Exception:
                    pass  # Re-index on error

            # Index the file
            metadata = {
                "filepath": filepath,
                "mtime": mtime,
                "content_hash": content_hash,
                "indexed_at": datetime.now().isoformat(),
            }

            collection.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[metadata],
            )
            indexed += 1

        except Exception as e:
            print(f"[file_indexer] WARNING: failed to index {filepath}: {e}", file=sys.stderr)
            skipped += 1

    # Remove deleted files from index
    deleted = 0
    stale_ids = existing_ids - current_ids
    if stale_ids:
        try:
            collection.delete(ids=list(stale_ids))
            deleted = len(stale_ids)
        except Exception:
            pass

    return {
        "indexed_count": indexed,
        "skipped_count": skipped,
        "deleted_count": deleted,
        "total_files": len(files),
    }


def search_files(
    query: str,
    collection_name: str = _DEFAULT_COLLECTION,
    top_k: int = 5,
) -> list:
    """Semantic search indexed files.

    Args:
        query: Search query
        collection_name: ChromaDB collection name
        top_k: Number of results

    Returns:
        list[dict]: [{filepath, content_preview, score, metadata}]
    """
    collection = _get_collection(collection_name)
    if collection is None:
        return []

    try:
        results = collection.query(
            query_texts=[query],
            n_results=top_k,
        )

        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        output = []
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
            output.append({
                "filepath": meta.get("filepath", ""),
                "content_preview": doc[:500] if doc else "",
                "score": 1 - dist if dist is not None else 0,  # Convert distance to similarity
                "metadata": meta,
                "rank": i + 1,
            })
        return output

    except Exception as e:
        print(f"[file_indexer] WARNING: search failed: {e}", file=sys.stderr)
        return []


def get_index_stats(collection_name: str = _DEFAULT_COLLECTION) -> dict:
    """Get index statistics.

    Returns:
        dict: {total_documents, collection_name, chromadb_available}
    """
    collection = _get_collection(collection_name)
    if collection is None:
        return {
            "total_documents": 0,
            "collection_name": collection_name,
            "chromadb_available": _check_chromadb(),
        }

    try:
        data = collection.get()
        return {
            "total_documents": len(data.get("ids", [])),
            "collection_name": collection_name,
            "chromadb_available": True,
        }
    except Exception:
        return {
            "total_documents": 0,
            "collection_name": collection_name,
            "chromadb_available": True,
        }


def clear_index(collection_name: str = _DEFAULT_COLLECTION) -> bool:
    """Clear all documents from a collection.

    Returns:
        bool: True if cleared successfully
    """
    collection = _get_collection(collection_name)
    if collection is None:
        return False

    try:
        data = collection.get()
        ids = data.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # CLI: python -m modules.orchestrator.file_indexer [index|search|stats]
    if len(sys.argv) < 2:
        print("Usage: python -m modules.orchestrator.file_indexer [index|search|stats] [args]")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "index":
        root = sys.argv[2] if len(sys.argv) > 2 else str(_PROJECT_ROOT / "_data" / "memory")
        result = index_directory(root=root)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif cmd == "search":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        results = search_files(query=query, top_k=5)
        print(json.dumps(results, indent=2, ensure_ascii=False))

    elif cmd == "stats":
        stats = get_index_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    else:
        print(f"Unknown command: {cmd}")
