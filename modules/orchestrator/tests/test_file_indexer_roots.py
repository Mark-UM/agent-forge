from __future__ import annotations

from pathlib import Path

import pytest

from modules.orchestrator import file_indexer


class FakeCollection:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.documents: dict[str, str] = {}
        self.deleted: list[str] = []
        self.fail_for_document: str | None = None

    def get(self, ids=None, where=None):  # noqa: ANN001
        selected = list(self.rows)
        if ids is not None:
            selected = [doc_id for doc_id in selected if doc_id in set(ids)]
        if where:
            selected = [
                doc_id
                for doc_id in selected
                if all(self.rows[doc_id].get(key) == value for key, value in where.items())
            ]
        return {
            "ids": selected,
            "metadatas": [self.rows[doc_id] for doc_id in selected],
            "documents": [self.documents[doc_id] for doc_id in selected],
        }

    def upsert(self, ids, documents, metadatas):  # noqa: ANN001
        if self.fail_for_document and self.fail_for_document in documents[0]:
            raise RuntimeError("injected upsert failure")
        for doc_id, document, metadata in zip(ids, documents, metadatas):
            self.rows[doc_id] = dict(metadata)
            self.documents[doc_id] = document

    def delete(self, ids):  # noqa: ANN001
        for doc_id in ids:
            self.deleted.append(doc_id)
            self.rows.pop(doc_id, None)
            self.documents.pop(doc_id, None)

    def query(self, **kwargs):  # noqa: ANN003
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}


def _install(monkeypatch, collection: FakeCollection) -> None:
    monkeypatch.setattr(file_indexer, "_get_collection", lambda name="memory_files": collection)


def test_reindexing_one_root_never_deletes_another_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    collection = FakeCollection()
    _install(monkeypatch, collection)
    root_a = tmp_path / "memory"
    root_b = tmp_path / "reports"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a.md").write_text("memory A", encoding="utf-8")
    (root_b / "b.md").write_text("report B", encoding="utf-8")

    first_a = file_indexer.index_directory(str(root_a))
    first_b = file_indexer.index_directory(str(root_b))
    root_b_id = first_b["root_id"]
    root_b_documents = {
        doc_id for doc_id, metadata in collection.rows.items()
        if metadata.get("root_id") == root_b_id
    }

    (root_a / "a.md").unlink()
    second_a = file_indexer.index_directory(str(root_a))

    assert first_a["success"] is True
    assert first_b["success"] is True
    assert second_a["deleted_count"] == 1
    assert root_b_documents
    assert root_b_documents.issubset(collection.rows)
    assert not root_b_documents.intersection(collection.deleted)


def test_any_upsert_failure_disables_stale_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    collection = FakeCollection()
    _install(monkeypatch, collection)
    root = tmp_path / "memory"
    root.mkdir()
    old_file = root / "old.md"
    old_file.write_text("old content", encoding="utf-8")
    initial = file_indexer.index_directory(str(root))
    old_ids = set(collection.rows)
    assert initial["success"] is True

    old_file.unlink()
    (root / "new.md").write_text("FAIL THIS UPSERT", encoding="utf-8")
    collection.fail_for_document = "FAIL"
    result = file_indexer.index_directory(str(root))

    assert result["success"] is False
    assert result["failed_count"] == 1
    assert result["cleanup_skipped"] is True
    assert result["deleted_count"] == 0
    assert old_ids.issubset(collection.rows)


def test_document_ids_are_stable_for_same_canonical_root(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    root_id = file_indexer.root_identifier(root)
    assert root_id == file_indexer.root_identifier(root / ".")
    assert file_indexer.document_identifier(root_id, "folder/item.md") == (
        file_indexer.document_identifier(root_id, "folder\\item.md")
    )


def test_search_can_be_scoped_to_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    collection = FakeCollection()
    captured: dict = {}

    def query(**kwargs):
        captured.update(kwargs)
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    collection.query = query
    _install(monkeypatch, collection)
    root = tmp_path / "memory"
    root.mkdir()

    file_indexer.search_files("query", root=str(root))

    assert captured["where"] == {"root_id": file_indexer.root_identifier(root)}
