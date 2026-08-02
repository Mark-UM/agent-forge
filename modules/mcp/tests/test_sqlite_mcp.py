"""Tests for modules.mcp.sqlite_mcp (v1.6)

Covers:
- SQLiteClient: init validation, file-not-found, list_tables, get_schema,
  query (read-only), execute (writable), parameterized queries, MAX_ROWS truncation,
  write-keyword detection
- _handle_request() MCP protocol: initialize, tools/list, tools/call (all 4 tools),
  error paths, notifications, invalid request
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.mcp import sqlite_mcp
from modules.mcp.sqlite_mcp import SQLiteClient, MAX_ROWS, _WRITE_KEYWORDS


# ---------- Fixtures ----------

@pytest.fixture
def populated_db(tmp_path):
    """Create a temp SQLite DB with a users table for testing."""
    db_path = tmp_path / 'test.db'
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            age INTEGER DEFAULT 0
        );
        INSERT INTO users (name, email, age) VALUES
            ('Alice', 'alice@example.com', 30),
            ('Bob', 'bob@example.com', 25),
            ('Charlie', 'charlie@example.com', 35);
        CREATE VIEW user_names AS SELECT name FROM users;
    """)
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def writable_client(populated_db):
    return SQLiteClient(populated_db, writable=True)


@pytest.fixture
def readonly_client(populated_db):
    return SQLiteClient(populated_db, writable=False)


# ---------- SQLiteClient init ----------

def test_client_init_requires_db_path():
    with pytest.raises(ValueError, match='db_path is required'):
        SQLiteClient('')


def test_client_init_default_readonly():
    client = SQLiteClient('/tmp/nonexistent.db')
    assert client.writable is False


def test_client_init_explicit_writable():
    client = SQLiteClient('/tmp/nonexistent.db', writable=True)
    assert client.writable is True


def test_client_connect_file_not_found():
    client = SQLiteClient('/tmp/definitely_does_not_exist_12345.db')
    with pytest.raises(FileNotFoundError, match='Database file not found'):
        client._connect()


# ---------- _is_write_query ----------

@pytest.mark.parametrize('sql', [
    'INSERT INTO t VALUES (1)',
    'UPDATE t SET x=1',
    'DELETE FROM t',
    'DROP TABLE t',
    'CREATE TABLE t (x INT)',
    'ALTER TABLE t ADD COLUMN x',
    'TRUNCATE TABLE t',
    'REPLACE INTO t VALUES (1)',
    '  insert into t values (1)',  # leading whitespace
    'INSERT INTO t VALUES (1)',  # uppercase
])
def test_is_write_query_detects_write_operations(sql):
    client = SQLiteClient('/tmp/dummy.db')
    assert client._is_write_query(sql) is True


@pytest.mark.parametrize('sql', [
    'SELECT * FROM t',
    'SELECT * FROM t WHERE id = ?',
    '  select * from t',  # lowercase, leading whitespace
    'WITH cte AS (SELECT 1) SELECT * FROM cte',
])
def test_is_write_query_allows_read_queries(sql):
    client = SQLiteClient('/tmp/dummy.db')
    assert client._is_write_query(sql) is False


# ---------- list_tables ----------

def test_list_tables_returns_tables_and_views(readonly_client):
    tables = readonly_client.list_tables()
    names = [t['name'] for t in tables]
    assert 'users' in names
    assert 'user_names' in names  # view
    types = {t['name']: t['type'] for t in tables}
    assert types['users'] == 'table'
    assert types['user_names'] == 'view'


def test_list_tables_includes_sql(readonly_client):
    tables = readonly_client.list_tables()
    users_table = [t for t in tables if t['name'] == 'users'][0]
    assert users_table['sql'] is not None
    assert 'CREATE TABLE' in users_table['sql'].upper()


# ---------- get_schema ----------

def test_get_schema_valid_table(readonly_client):
    cols = readonly_client.get_schema('users')
    col_names = [c['name'] for c in cols]
    assert col_names == ['id', 'name', 'email', 'age']
    # Verify column metadata
    id_col = [c for c in cols if c['name'] == 'id'][0]
    assert id_col['pk'] is True
    assert id_col['notnull'] is False  # SQLite AUTOINCREMENT PK is not "NOT NULL" by schema
    name_col = [c for c in cols if c['name'] == 'name'][0]
    assert name_col['notnull'] is True
    age_col = [c for c in cols if c['name'] == 'age'][0]
    assert age_col['default'] == 0 or age_col['default'] == '0'


def test_get_schema_empty_table_name(readonly_client):
    with pytest.raises(ValueError, match='table_name is required'):
        readonly_client.get_schema('')


def test_get_schema_table_not_found(readonly_client):
    with pytest.raises(ValueError, match="Table 'nonexistent' not found"):
        readonly_client.get_schema('nonexistent')


def test_get_schema_prevents_sql_injection_via_table_name(readonly_client):
    """Table name validation happens before PRAGMA, blocking injection attempts."""
    with pytest.raises(ValueError, match='not found'):
        readonly_client.get_schema('users"; DROP TABLE users; --')


# ---------- query (read-only) ----------

def test_query_select_all(readonly_client):
    result = readonly_client.query('SELECT * FROM users ORDER BY id')
    assert result['columns'] == ['id', 'name', 'email', 'age']
    assert result['row_count'] == 3
    assert result['truncated'] is False
    assert result['rows'][0] == [1, 'Alice', 'alice@example.com', 30]


def test_query_select_specific_columns(readonly_client):
    result = readonly_client.query('SELECT name, age FROM users WHERE age > 28 ORDER BY age')
    assert result['row_count'] == 2
    assert result['rows'] == [['Alice', 30], ['Charlie', 35]]


def test_query_parameterized(readonly_client):
    result = readonly_client.query(
        'SELECT name FROM users WHERE age > ? ORDER BY name',
        [28]
    )
    assert result['row_count'] == 2
    assert result['rows'] == [['Alice'], ['Charlie']]


def test_query_empty_result_set(readonly_client):
    result = readonly_client.query('SELECT * FROM users WHERE age > 999')
    assert result['row_count'] == 0
    assert result['rows'] == []
    assert result['columns'] == ['id', 'name', 'email', 'age']


def test_query_write_blocked_in_readonly_mode(readonly_client):
    with pytest.raises(ValueError, match='Write operations.*not allowed.*read-only'):
        readonly_client.query('INSERT INTO users (name, email) VALUES (?, ?)',
                              ['Hacker', 'hack@evil.com'])


def test_query_drop_blocked_in_readonly_mode(readonly_client):
    with pytest.raises(ValueError, match='Write operations.*not allowed'):
        readonly_client.query('DROP TABLE users')


def test_query_empty_sql_raises(readonly_client):
    with pytest.raises(ValueError, match='sql is required'):
        readonly_client.query('')


def test_query_truncation_at_max_rows(readonly_client):
    """Insert MAX_ROWS+1 rows and verify truncation flag."""
    # Use writable client to seed test data
    writable_client = SQLiteClient(readonly_client.db_path, writable=True)
    for i in range(MAX_ROWS + 5):
        writable_client.execute(
            'INSERT INTO users (name, email, age) VALUES (?, ?, ?)',
            [f'user_{i}', f'u{i}@example.com', 20 + (i % 50)]
        )
    result = readonly_client.query('SELECT * FROM users')
    assert result['row_count'] == MAX_ROWS + 5 + 3  # 3 original + new
    assert result['truncated'] is True
    assert len(result['rows']) == MAX_ROWS


def test_query_default_params_is_empty_list(readonly_client):
    """params=None should default to empty list."""
    result = readonly_client.query('SELECT 1 AS x')
    assert result['rows'] == [[1]]


# ---------- execute (writable) ----------

def test_execute_blocked_in_readonly_mode(readonly_client):
    with pytest.raises(ValueError, match='Write operations require writable mode'):
        readonly_client.execute('INSERT INTO users (name, email) VALUES (?, ?)',
                                ['Test', 'test@example.com'])


def test_execute_insert(writable_client):
    result = writable_client.execute(
        'INSERT INTO users (name, email, age) VALUES (?, ?, ?)',
        ['Dave', 'dave@example.com', 40]
    )
    assert result['rows_affected'] == 1
    assert result['last_inserted_id'] == 4  # AUTOINCREMENT continues from 3

    # Verify insertion
    rows = writable_client.query("SELECT name FROM users WHERE email = 'dave@example.com'")
    assert rows['rows'] == [['Dave']]


def test_execute_update(writable_client):
    result = writable_client.execute(
        'UPDATE users SET age = ? WHERE name = ?',
        [99, 'Alice']
    )
    assert result['rows_affected'] == 1
    rows = writable_client.query("SELECT age FROM users WHERE name = 'Alice'")
    assert rows['rows'] == [[99]]


def test_execute_delete(writable_client):
    result = writable_client.execute(
        'DELETE FROM users WHERE name = ?',
        ['Bob']
    )
    assert result['rows_affected'] == 1
    rows = writable_client.query('SELECT COUNT(*) AS cnt FROM users')
    assert rows['rows'] == [[2]]  # 3 original - 1 deleted


def test_execute_empty_sql_raises(writable_client):
    with pytest.raises(ValueError, match='sql is required'):
        writable_client.execute('')


def test_execute_default_params_is_empty_list(writable_client):
    result = writable_client.execute(
        'INSERT INTO users (name, email) VALUES ("Eve", "eve@example.com")'
    )
    assert result['rows_affected'] == 1


# ---------- MCP protocol: _handle_request ----------

def _setup_global_client(db_path, writable=False):
    """Helper: set the module-global _client used by _handle_request."""
    sqlite_mcp._client = SQLiteClient(db_path, writable=writable)


def test_handle_initialize():
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = sqlite_mcp._handle_request(req)
    assert resp['jsonrpc'] == '2.0'
    assert resp['id'] == 1
    assert resp['result']['protocolVersion'] == '2024-11-05'
    assert resp['result']['serverInfo']['name'] == 'sqlite-mcp'


def test_handle_tools_list_returns_four_tools():
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = sqlite_mcp._handle_request(req)
    tool_names = [t['name'] for t in resp['result']['tools']]
    assert set(tool_names) == {'query', 'list_tables', 'get_schema', 'execute'}


def test_handle_call_list_tables(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "list_tables", "arguments": {}}
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    names = [t['name'] for t in data['tables']]
    assert 'users' in names


def test_handle_call_get_schema(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "get_schema", "arguments": {"table_name": "users"}}
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    assert data['table_name'] == 'users'
    col_names = [c['name'] for c in data['columns']]
    assert col_names == ['id', 'name', 'email', 'age']


def test_handle_call_get_schema_missing_arg(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "get_schema", "arguments": {}}
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'table_name is required' in data['error']


def test_handle_call_get_schema_table_not_found(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "get_schema", "arguments": {"table_name": "nonexistent"}}
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'not found' in data['error']


def test_handle_call_query(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {
            "name": "query",
            "arguments": {"sql": "SELECT name FROM users ORDER BY name"}
        }
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    assert data['row_count'] == 3
    assert data['rows'][0] == ['Alice']


def test_handle_call_query_missing_sql(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "query", "arguments": {}}
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'sql is required' in data['error']


def test_handle_call_query_with_params(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {
            "name": "query",
            "arguments": {
                "sql": "SELECT name FROM users WHERE age > ? ORDER BY name",
                "params": [28]
            }
        }
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    assert data['row_count'] == 2


def test_handle_call_query_write_blocked_in_readonly(populated_db):
    _setup_global_client(populated_db, writable=False)
    req = {
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {
            "name": "query",
            "arguments": {"sql": "DROP TABLE users"}
        }
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'Write operations' in data['error']


def test_handle_call_execute_in_readonly_mode(populated_db):
    _setup_global_client(populated_db, writable=False)
    req = {
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {
            "name": "execute",
            "arguments": {
                "sql": "INSERT INTO users (name, email) VALUES (?, ?)",
                "params": ["X", "x@example.com"]
            }
        }
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'writable mode' in data['error']


def test_handle_call_execute_in_writable_mode(populated_db):
    _setup_global_client(populated_db, writable=True)
    req = {
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {
            "name": "execute",
            "arguments": {
                "sql": "INSERT INTO users (name, email, age) VALUES (?, ?, ?)",
                "params": ["Frank", "frank@example.com", 50]
            }
        }
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is False
    data = json.loads(resp['result']['content'][0]['text'])
    assert data['rows_affected'] == 1
    assert data['last_inserted_id'] == 4


def test_handle_call_sqlite_error(populated_db):
    """Malformed SQL should produce a sqlite3.Error response, not a crash."""
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 13, "method": "tools/call",
        "params": {
            "name": "query",
            "arguments": {"sql": "SELECT FROM users"}
        }
    }
    resp = sqlite_mcp._handle_request(req)
    assert resp['result']['isError'] is True
    data = json.loads(resp['result']['content'][0]['text'])
    assert 'SQLite error' in data['error']


def test_handle_call_unknown_tool(populated_db):
    _setup_global_client(populated_db)
    req = {
        "jsonrpc": "2.0", "id": 14, "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}}
    }
    resp = sqlite_mcp._handle_request(req)
    assert 'error' in resp
    assert resp['error']['code'] == -32601


def test_handle_notification_initialized():
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    resp = sqlite_mcp._handle_request(req)
    assert resp is None


def test_handle_unknown_method():
    req = {"jsonrpc": "2.0", "id": 15, "method": "unknown/method"}
    resp = sqlite_mcp._handle_request(req)
    assert 'error' in resp
    assert resp['error']['code'] == -32601


def test_handle_invalid_request_not_dict():
    resp = sqlite_mcp._handle_request("not a dict")
    assert resp['error']['code'] == -32600
