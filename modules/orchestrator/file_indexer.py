#!/usr/bin/env python3
"""Root-safe ChromaDB file indexer.

Documents from multiple directories may share one collection. Every document is
owned by a stable canonical ``root_id`` and stale cleanup is restricted to that
owner. If any upsert fails, cleanup is skipped for the entire indexing run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Optional

from modules.bootstrap.dependencies import activate_vendor_path

activate_vendor_path()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CHROMA_DIR = _PROJECT_ROOT / "_runtime" / "search" / "chroma"
_DEFAULT_COLLECTION = "memory_files"

_chromadb_available = None
_client = None
_collections: dict[str, object] = {}


def _check_chromadb() -> bool:
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
    global _client, _collections
    if collection_name in _collections:
        return _collections[collection_name]
    if not _check_chromadb():
        return None
    try:
        import chromadb

        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        if _client is None:
            _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        collection = _client.get_or_create_collection(
            name=collection_name,
            metadata={"description": f"File index: {collection_name}"},
        )
        _collections[collection_name] = collection
        return collection
    except Exception as exc:
        print(f"[file_indexer] WARNING: ChromaDB init failed: {exc}", file=sys.stderr)
        return None


def canonical_root(root: str | os.PathLike[str]) -> Path:
    path = Path(root).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"index root does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"index root is not a directory: {path}")
    return path


def root_identifier(root: str | os.PathLike[str]) -> str:
    path = canonical_root(root)
    normalized = os.path.normcase(str(path))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def document_identifier(root_id: str, relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/")
    return hashlib.sha256(f"{root_id}:{normalized}".encode("utf-8")).hexdigest()[:32]


def _scan_files(root: Path, patterns: list[str]) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        for candidate in root.rglob(pattern):
            if candidate.is_file():
                files.add(candidate.resolve())
    return sorted(files, key=lambda path: path.as_posix().lower())


def _read_file_content(path: Path, max_chars: int = 10_000) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars]
    except (UnicodeDecodeError, OSError):
        return ""


def _belongs_to_root(filepath: str, root: Path) -> bool:
    try:
        Path(filepath).expanduser().resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _collection_rows(collection, *, root: Path, root_id: str) -> dict[str, dict]:
    """Return only records owned by this root, including safe legacy matches."""

    data = None
    try:
        data = collection.get(where={"root_id": root_id})
    except Exception:
        # Older Chroma versions/fakes may not support where on get(). Fall back
        # to a full read but filter locally before any delete decision.
        try:
            data = collection.get()
        except Exception:
            return {}
    ids = list((data or {}).get("ids", []) or [])
    metadatas = list((data or {}).get("metadatas", []) or [])
    rows: dict[str, dict] = {}
    for index, doc_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        owner = metadata.get("root_id")
        if owner == root_id:
            rows[str(doc_id)] = metadata
        elif not owner and _belongs_to_root(str(metadata.get("filepath", "")), root):
            # Legacy records are adopted only when their filepath resolves
            # beneath this exact canonical root.
            rows[str(doc_id)] = metadata
    return rows


def index_directory(
    root: str,
    glob_patterns: Optional[list[str]] = None,
    incremental: bool = True,
    collection_name: str = _DEFAULT_COLLECTION,
) -> dict:
    patterns = glob_patterns or ["*.md", "*.txt"]
    try:
        root_path = canonical_root(root)
    except (FileNotFoundError, NotADirectoryError) as exc:
        return {
            "success": False,
            "indexed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "deleted_count": 0,
            "cleanup_skipped": True,
            "total_files": 0,
            "error": str(exc),
        }
    root_id = root_identifier(root_path)
    collection = _get_collection(collection_name)
    if collection is None:
        return {
            "success": False,
            "root_id": root_id,
            "root_path": str(root_path),
            "indexed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "deleted_count": 0,
            "cleanup_skipped": True,
            "total_files": 0,
            "error": "ChromaDB not available",
        }

    files = _scan_files(root_path, patterns)
    existing = _collection_rows(collection, root=root_path, root_id=root_id)
    current_ids: set[str] = set()
    indexed = 0
    skipped = 0
    failed = 0

    for path in files:
        relative_path = path.relative_to(root_path).as_posix()
        doc_id = document_identifier(root_id, relative_path)
        current_ids.add(doc_id)
        try:
            content = _read_file_content(path)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            previous = existing.get(doc_id, {})
            if incremental and previous.get("content_hash") == content_hash:
                skipped += 1
                continue
            metadata = {
                "filepath": str(path),
                "root_id": root_id,
                "root_path": str(root_path),
                "relative_path": relative_path,
                "mtime": path.stat().st_mtime,
                "content_hash": content_hash,
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }
            collection.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[metadata],
            )
            indexed += 1
        except Exception as exc:
            failed += 1
            print(
                f"[file_indexer] WARNING: failed to index {path}: {exc}",
                file=sys.stderr,
            )

    deleted = 0
    cleanup_skipped = failed > 0
    if not cleanup_skipped:
        stale_ids = set(existing) - current_ids
        if stale_ids:
            try:
                collection.delete(ids=sorted(stale_ids))
                deleted = len(stale_ids)
            except Exception as exc:
                cleanup_skipped = True
                print(
                    f"[file_indexer] WARNING: stale cleanup failed for {root_path}: {exc}",
                    file=sys.stderr,
                )

    return {
        "success": failed == 0,
        "root_id": root_id,
        "root_path": str(root_path),
        "indexed_count": indexed,
        "skipped_count": skipped,
        "failed_count": failed,
        "deleted_count": deleted,
        "cleanup_skipped": cleanup_skipped,
        "total_files": len(files),
    }


def search_files(
    query: str,
    collection_name: str = _DEFAULT_COLLECTION,
    top_k: int = 5,
    root: Optional[str] = None,
) -> list[dict]:
    collection = _get_collection(collection_name)
    if collection is None:
        return []
    kwargs: dict = {"query_texts": [query], "n_results": top_k}
    if root is not None:
        try:
            kwargs["where"] = {"root_id": root_identifier(root)}
        except (FileNotFoundError, NotADirectoryError):
            return []
    try:
        results = collection.query(**kwargs)
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        output: list[dict] = []
        for rank, (document, metadata, distance) in enumerate(
            zip(documents, metadatas, distances), start=1
        ):
            metadata = metadata or {}
            output.append(
                {
                    "filepath": metadata.get("filepath", ""),
                    "content_preview": document[:500] if document else "",
                    "score": 1 - distance if distance is not None else 0,
                    "metadata": metadata,
                    "rank": rank,
                }
            )
        return output
    except Exception as exc:
        print(f"[file_indexer] WARNING: search failed: {exc}", file=sys.stderr)
        return []


def get_index_stats(
    collection_name: str = _DEFAULT_COLLECTION,
    root: Optional[str] = None,
) -> dict:
    collection = _get_collection(collection_name)
    if collection is None:
        return {
            "total_documents": 0,
            "collection_name": collection_name,
            "chromadb_available": _check_chromadb(),
        }
    try:
        if root is None:
            data = collection.get()
            root_id = None
        else:
            root_path = canonical_root(root)
            root_id = root_identifier(root_path)
            data = collection.get(where={"root_id": root_id})
        return {
            "total_documents": len(data.get("ids", [])),
            "collection_name": collection_name,
            "chromadb_available": True,
            "root_id": root_id,
        }
    except Exception:
        return {
            "total_documents": 0,
            "collection_name": collection_name,
            "chromadb_available": True,
        }


def clear_index(
    collection_name: str = _DEFAULT_COLLECTION,
    root: Optional[str] = None,
) -> bool:
    collection = _get_collection(collection_name)
    if collection is None:
        return False
    try:
        if root is None:
            data = collection.get()
        else:
            root_path = canonical_root(root)
            data = collection.get(where={"root_id": root_identifier(root_path)})
        ids = data.get("ids", [])
        if ids:
            collection.delete(ids=ids)
        return True
    except Exception:
        return False


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("Usage: python -m modules.orchestrator.file_indexer [index|search|stats] [args]")
        return 0
    command = arguments[0]
    if command == "index":
        root = arguments[1] if len(arguments) > 1 else str(_PROJECT_ROOT / "_data" / "memory")
        result = index_directory(root=root)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("success") else 1
    if command == "search":
        query = arguments[1] if len(arguments) > 1 else ""
        print(json.dumps(search_files(query=query), indent=2, ensure_ascii=False))
        return 0
    if command == "stats":
        print(json.dumps(get_index_stats(), indent=2, ensure_ascii=False))
        return 0
    print(f"Unknown command: {command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
