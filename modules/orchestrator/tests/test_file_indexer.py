"""Tests for modules/orchestrator/file_indexer.py — v1.8 Phase B3."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def temp_dir_with_files(tmp_path):
    """Create a temp directory with some .md files."""
    (tmp_path / "file1.md").write_text("# Python\nPython is great for AI.", encoding="utf-8")
    (tmp_path / "file2.md").write_text("# Java\nEnterprise language.", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("Some notes here.", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text("# Nested\nDeep content.", encoding="utf-8")
    return tmp_path


@pytest.fixture
def mock_chromadb(monkeypatch):
    """Mock ChromaDB to avoid real database."""
    mock_collection = MagicMock()
    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_collection

    def mock_get_collection(collection_name="memory_files"):
        return mock_collection

    monkeypatch.setattr(
        "modules.orchestrator.file_indexer._get_collection", mock_get_collection
    )
    monkeypatch.setattr(
        "modules.orchestrator.file_indexer._check_chromadb", lambda: True
    )
    return mock_collection


# ── File scanning tests ────────────────────────────────────────


def test_scan_files_finds_md(temp_dir_with_files):
    """_scan_files should find all .md files."""
    from modules.orchestrator.file_indexer import _scan_files

    files = _scan_files(str(temp_dir_with_files), ["*.md"])
    assert len(files) == 3  # file1.md, file2.md, sub/nested.md


def test_scan_files_finds_txt(temp_dir_with_files):
    """_scan_files should find .txt files."""
    from modules.orchestrator.file_indexer import _scan_files

    files = _scan_files(str(temp_dir_with_files), ["*.txt"])
    assert len(files) == 1


def test_scan_files_finds_multiple_patterns(temp_dir_with_files):
    """_scan_files should support multiple patterns."""
    from modules.orchestrator.file_indexer import _scan_files

    files = _scan_files(str(temp_dir_with_files), ["*.md", "*.txt"])
    assert len(files) == 4


def test_scan_files_nonexistent_dir():
    """_scan_files should return empty list for nonexistent dir."""
    from modules.orchestrator.file_indexer import _scan_files

    files = _scan_files("/nonexistent/path", ["*.md"])
    assert files == []


# ── File hash tests ────────────────────────────────────────────


def test_file_hash_consistent(temp_dir_with_files):
    """_file_hash should be consistent for same content."""
    from modules.orchestrator.file_indexer import _file_hash

    filepath = str(temp_dir_with_files / "file1.md")
    h1 = _file_hash(filepath)
    h2 = _file_hash(filepath)
    assert h1 == h2


def test_file_hash_different_files(temp_dir_with_files):
    """_file_hash should differ for different content."""
    from modules.orchestrator.file_indexer import _file_hash

    h1 = _file_hash(str(temp_dir_with_files / "file1.md"))
    h2 = _file_hash(str(temp_dir_with_files / "file2.md"))
    assert h1 != h2


# ── Content reading tests ──────────────────────────────────────


def test_read_file_content(temp_dir_with_files):
    """_read_file_content should read file content."""
    from modules.orchestrator.file_indexer import _read_file_content

    content = _read_file_content(str(temp_dir_with_files / "file1.md"))
    assert "Python" in content


def test_read_file_content_truncation(tmp_path):
    """_read_file_content should truncate to max_chars."""
    from modules.orchestrator.file_indexer import _read_file_content

    long_file = tmp_path / "long.md"
    long_file.write_text("A" * 20000, encoding="utf-8")
    content = _read_file_content(str(long_file), max_chars=100)
    assert len(content) == 100


def test_read_file_content_binary_fallback(tmp_path):
    """_read_file_content should return empty for binary files."""
    from modules.orchestrator.file_indexer import _read_file_content

    binary_file = tmp_path / "binary.md"
    binary_file.write_bytes(b"\x80\x81\x82\x83")
    content = _read_file_content(str(binary_file))
    assert content == ""


# ── index_directory tests ──────────────────────────────────────


def test_index_directory_no_chromadb():
    """index_directory should return error dict when ChromaDB unavailable."""
    from modules.orchestrator.file_indexer import index_directory

    with patch("modules.orchestrator.file_indexer._get_collection", return_value=None):
        result = index_directory(root="/tmp")
        assert result["indexed_count"] == 0
        assert "error" in result


def test_index_directory_with_mock(temp_dir_with_files, mock_chromadb):
    """index_directory should index files with mocked ChromaDB."""
    from modules.orchestrator.file_indexer import index_directory

    # Mock collection.get to return empty (no existing docs)
    mock_chromadb.get.return_value = {"ids": []}

    result = index_directory(root=str(temp_dir_with_files))

    assert result["total_files"] == 4  # 3 .md + 1 .txt (default patterns)
    assert result["indexed_count"] == 4
    assert result["skipped_count"] == 0
    # Verify upsert was called for each file
    assert mock_chromadb.upsert.call_count == 4


def test_index_directory_incremental_skip(temp_dir_with_files, mock_chromadb):
    """Incremental indexing should skip unchanged files."""
    from modules.orchestrator.file_indexer import index_directory, _read_file_content, _scan_files
    import hashlib

    # Compute doc_ids for all files (same logic as index_directory)
    files = _scan_files(str(temp_dir_with_files), ["*.md", "*.txt"])

    # Pick first file to simulate as already indexed (unchanged)
    first_file = files[0]
    content = _read_file_content(first_file)
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    first_doc_id = hashlib.md5(first_file.encode("utf-8")).hexdigest()[:16]

    # Mock: existing_ids contains only the first file's doc_id
    def mock_get(ids=None):
        if ids:
            # Return the stored hash for the first file
            return {
                "ids": ids,
                "metadatas": [{"content_hash": content_hash}],
            }
        # No ids = get all → return only first file as existing
        return {"ids": [first_doc_id]}

    mock_chromadb.get.side_effect = mock_get

    result = index_directory(root=str(temp_dir_with_files), incremental=True)
    # First file should be skipped (hash matches), others indexed
    assert result["indexed_count"] == 3  # 4 total - 1 skipped
    assert result["skipped_count"] == 1


# ── search_files tests ─────────────────────────────────────────


def test_search_files_no_chromadb():
    """search_files should return empty list when ChromaDB unavailable."""
    from modules.orchestrator.file_indexer import search_files

    with patch("modules.orchestrator.file_indexer._get_collection", return_value=None):
        results = search_files("test query")
        assert results == []


def test_search_files_with_mock(mock_chromadb):
    """search_files should return formatted results."""
    from modules.orchestrator.file_indexer import search_files

    mock_chromadb.query.return_value = {
        "documents": [["Python content here"]],
        "metadatas": [[{"filepath": "/test/file1.md"}]],
        "distances": [[0.2]],
    }

    results = search_files("Python", top_k=5)

    assert len(results) == 1
    assert results[0]["filepath"] == "/test/file1.md"
    assert results[0]["rank"] == 1
    assert "content_preview" in results[0]
    assert "score" in results[0]


# ── Stats and clear tests ──────────────────────────────────────


def test_get_index_stats_no_chromadb():
    """get_index_stats should return zeros when ChromaDB unavailable."""
    from modules.orchestrator.file_indexer import get_index_stats

    with patch("modules.orchestrator.file_indexer._get_collection", return_value=None):
        stats = get_index_stats()
        assert stats["total_documents"] == 0


def test_get_index_stats_with_mock(mock_chromadb):
    """get_index_stats should return document count."""
    from modules.orchestrator.file_indexer import get_index_stats

    mock_chromadb.get.return_value = {"ids": ["id1", "id2", "id3"]}

    stats = get_index_stats()
    assert stats["total_documents"] == 3
    assert stats["chromadb_available"] is True


def test_clear_index_with_mock(mock_chromadb):
    """clear_index should delete all documents."""
    from modules.orchestrator.file_indexer import clear_index

    mock_chromadb.get.return_value = {"ids": ["id1", "id2"]}

    result = clear_index()
    assert result is True
    mock_chromadb.delete.assert_called_once()
