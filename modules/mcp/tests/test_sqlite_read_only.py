from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modules.mcp.sqlite_mcp import SQLiteClient


@pytest.fixture()
def database(tmp_path: Path) -> Path:
    path = tmp_path / "sample.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.executemany("INSERT INTO items(name) VALUES (?)", [("alpha",), ("beta",)])
        conn.commit()
    return path


def _names(path: Path) -> list[str]:
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute("SELECT name FROM items ORDER BY id")]


def test_select_and_schema_remain_available(database: Path) -> None:
    client = SQLiteClient(str(database))
    result = client.query("SELECT id, name FROM items ORDER BY id")

    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "alpha"], [2, "beta"]]
    assert result["row_count"] == 2
    assert result["truncated"] is False
    assert client.get_schema("items")[1]["name"] == "name"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE items SET name = 'changed' WHERE id = 1",
        "WITH selected AS (SELECT id FROM items) UPDATE items SET name = 'changed' WHERE id IN selected",
        "DELETE FROM items WHERE id = 1",
        "INSERT INTO items(name) VALUES ('changed')",
        "REPLACE INTO items(id, name) VALUES (1, 'changed')",
        "DROP TABLE items",
        "CREATE TABLE other(id INTEGER)",
        "ALTER TABLE items ADD COLUMN note TEXT",
        "VACUUM",
        "REINDEX",
        "ANALYZE",
        "PRAGMA user_version = 2",
        "PRAGMA journal_mode = WAL",
        "ATTACH DATABASE ':memory:' AS extra",
    ],
)
def test_read_only_query_rejects_writes_and_state_changes(
    database: Path,
    statement: str,
) -> None:
    client = SQLiteClient(str(database))

    # Explicit statements preserve the historical ValueError contract, while
    # statements detected by SQLite's authorizer may surface DatabaseError.
    # Both are fail-closed rejection paths; database immutability is the
    # security invariant asserted below.
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        client.query(statement)

    assert _names(database) == ["alpha", "beta"]


def test_query_rejects_multiple_statements(database: Path) -> None:
    client = SQLiteClient(str(database))

    with pytest.raises(sqlite3.ProgrammingError):
        client.query("SELECT 1; DELETE FROM items")

    assert _names(database) == ["alpha", "beta"]


def test_query_on_writable_client_is_still_read_only(database: Path) -> None:
    client = SQLiteClient(str(database), writable=True)

    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        client.query("UPDATE items SET name = 'changed'")

    assert _names(database) == ["alpha", "beta"]


def test_execute_requires_explicit_writable_mode(database: Path) -> None:
    with pytest.raises(ValueError, match="writable mode"):
        SQLiteClient(str(database)).execute("UPDATE items SET name = 'changed'")

    writable = SQLiteClient(str(database), writable=True)
    result = writable.execute(
        "UPDATE items SET name = ? WHERE id = ?",
        ["changed", 1],
    )

    assert result["rows_affected"] == 1
    assert _names(database) == ["changed", "beta"]


def test_read_only_connection_cannot_be_switched_back(database: Path) -> None:
    client = SQLiteClient(str(database))
    with client._connect(read_only=True) as conn:
        value = conn.execute("PRAGMA query_only").fetchone()[0]
        assert value == 1
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE items SET name = 'changed'")

    assert _names(database) == ["alpha", "beta"]


def test_missing_database_does_not_get_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError):
        SQLiteClient(str(missing)).query("SELECT 1")

    assert not missing.exists()
