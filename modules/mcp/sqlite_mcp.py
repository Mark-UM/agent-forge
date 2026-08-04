#!/usr/bin/env python3
"""SQLite MCP server with fail-closed read-only enforcement.

The public surface remains compatible with the previous implementation:
``SQLiteClient``, ``query``, ``list_tables``, ``get_schema``, the stdio MCP
server, and the CLI are preserved.  Read-only safety no longer depends on
matching the first SQL keyword.  It is enforced by all of the following:

* SQLite URI ``mode=ro``;
* ``PRAGMA query_only = ON``;
* a SQLite authorizer that rejects writes, schema changes, ATTACH/DETACH,
  transactions, unsafe PRAGMAs, and extension-loading functions;
* the single-statement guarantee of ``Connection.execute``.

Write access is available only through ``execute`` on a client constructed with
``writable=True``.  ``query`` always uses a read-only connection, even for a
writable client.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "sqlite-mcp"
SERVER_VERSION = "1.1.0"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = str(_PROJECT_ROOT / "_runtime" / "mcp-sqlite.db")
MAX_ROWS = 1000
MAX_SQL_CHARS = 200_000

# Retained as an early diagnostic only.  This set is deliberately broader than
# the legacy version, but the SQLite connection and authorizer are the actual
# security boundary.
_WRITE_KEYWORDS = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
        "TRUNCATE", "REPLACE", "MERGE", "ATTACH", "DETACH", "VACUUM",
        "REINDEX", "ANALYZE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT",
        "RELEASE", "PRAGMA",
    }
)

_READ_ONLY_PRAGMAS = frozenset(
    {
        "collation_list",
        "compile_options",
        "database_list",
        "data_version",
        "foreign_key_check",
        "foreign_key_list",
        "function_list",
        "index_info",
        "index_list",
        "index_xinfo",
        "integrity_check",
        "module_list",
        "pragma_list",
        "quick_check",
        "schema_version",
        "table_info",
        "table_list",
        "table_xinfo",
    }
)

_DENIED_ACTION_NAMES = (
    "SQLITE_ALTER_TABLE",
    "SQLITE_ANALYZE",
    "SQLITE_ATTACH",
    "SQLITE_CREATE_INDEX",
    "SQLITE_CREATE_TABLE",
    "SQLITE_CREATE_TEMP_INDEX",
    "SQLITE_CREATE_TEMP_TABLE",
    "SQLITE_CREATE_TEMP_TRIGGER",
    "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER",
    "SQLITE_CREATE_VIEW",
    "SQLITE_CREATE_VTABLE",
    "SQLITE_DELETE",
    "SQLITE_DETACH",
    "SQLITE_DROP_INDEX",
    "SQLITE_DROP_TABLE",
    "SQLITE_DROP_TEMP_INDEX",
    "SQLITE_DROP_TEMP_TABLE",
    "SQLITE_DROP_TEMP_TRIGGER",
    "SQLITE_DROP_TEMP_VIEW",
    "SQLITE_DROP_TRIGGER",
    "SQLITE_DROP_VIEW",
    "SQLITE_DROP_VTABLE",
    "SQLITE_INSERT",
    "SQLITE_REINDEX",
    "SQLITE_SAVEPOINT",
    "SQLITE_TRANSACTION",
    "SQLITE_UPDATE",
)
_DENIED_ACTIONS = frozenset(
    value
    for name in _DENIED_ACTION_NAMES
    if (value := getattr(sqlite3, name, None)) is not None
)
_SQLITE_PRAGMA = getattr(sqlite3, "SQLITE_PRAGMA", -1)
_SQLITE_FUNCTION = getattr(sqlite3, "SQLITE_FUNCTION", -1)
_DANGEROUS_FUNCTIONS = frozenset({"load_extension", "writefile", "edit"})


def _normalise_sql(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql is required")
    if "\x00" in sql:
        raise ValueError("sql must not contain NUL bytes")
    if len(sql) > MAX_SQL_CHARS:
        raise ValueError(f"sql exceeds maximum length of {MAX_SQL_CHARS} characters")
    return sql


def _read_only_authorizer(action, arg1, arg2, db_name, trigger_name):  # noqa: ANN001
    del db_name, trigger_name
    if action in _DENIED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == _SQLITE_PRAGMA:
        pragma_name = (arg1 or "").lower()
        return sqlite3.SQLITE_OK if pragma_name in _READ_ONLY_PRAGMAS else sqlite3.SQLITE_DENY
    if action == _SQLITE_FUNCTION:
        function_name = (arg2 or arg1 or "").lower()
        if function_name in _DANGEROUS_FUNCTIONS:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class SQLiteClient:
    """SQLite client with separate read and write execution paths."""

    def __init__(self, db_path: str, writable: bool = False):
        if not db_path:
            raise ValueError("db_path is required")
        self.db_path = str(db_path)
        self.writable = bool(writable)

    def _resolved_path(self) -> Path:
        path = Path(self.db_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Database file not found: {self.db_path}")
        if not path.is_file():
            raise ValueError(f"Database path is not a file: {self.db_path}")
        return path.resolve()

    def _connect(self, *, read_only: bool | None = None) -> sqlite3.Connection:
        """Create a new thread-safe connection.

        ``read_only`` defaults to the inverse of ``self.writable`` for backward
        compatibility with callers that invoke ``_connect()`` directly.
        Public read APIs pass ``read_only=True`` explicitly.
        """

        if read_only is None:
            read_only = not self.writable
        path = self._resolved_path()
        if read_only:
            uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30.0)
            conn.execute("PRAGMA query_only = ON")
            conn.set_authorizer(_read_only_authorizer)
        else:
            if not self.writable:
                raise ValueError("Writable connection requested from a read-only client")
            conn = sqlite3.connect(str(path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_write_query(self, sql: str) -> bool:
        """Best-effort diagnostic helper; not a security control."""

        stripped = sql.lstrip().upper()
        return any(stripped.startswith(keyword) for keyword in _WRITE_KEYWORDS)

    def list_tables(self) -> list[dict]:
        with self._connect(read_only=True) as conn:
            cursor = conn.execute(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table', 'view') ORDER BY name"
            )
            return [
                {"name": row["name"], "type": row["type"], "sql": row["sql"]}
                for row in cursor.fetchall()
            ]

    def get_schema(self, table_name: str) -> list[dict]:
        if not isinstance(table_name, str) or not table_name:
            raise ValueError("table_name is required")
        available = {item["name"] for item in self.list_tables()}
        if table_name not in available:
            names = ", ".join(sorted(available)) or "none"
            raise ValueError(f"Table '{table_name}' not found. Available: {names}")
        escaped = table_name.replace('"', '""')
        with self._connect(read_only=True) as conn:
            cursor = conn.execute(f'PRAGMA table_info("{escaped}")')
            return [
                {
                    "cid": row["cid"],
                    "name": row["name"],
                    "type": row["type"],
                    "notnull": bool(row["notnull"]),
                    "default": row["dflt_value"],
                    "pk": bool(row["pk"]),
                }
                for row in cursor.fetchall()
            ]

    def query(self, sql: str, params: list | tuple | None = None) -> dict:
        """Execute exactly one statement on a physically read-only connection."""

        statement = _normalise_sql(sql)
        bound_params = [] if params is None else params
        with self._connect(read_only=True) as conn:
            cursor = conn.execute(statement, bound_params)
            columns = [item[0] for item in cursor.description] if cursor.description else []
            retained: list[sqlite3.Row] = []
            total = 0
            while True:
                chunk = cursor.fetchmany(256)
                if not chunk:
                    break
                total += len(chunk)
                if len(retained) < MAX_ROWS:
                    retained.extend(chunk[: MAX_ROWS - len(retained)])
            return {
                "columns": columns,
                "rows": [list(row) for row in retained],
                "row_count": total,
                "truncated": total > MAX_ROWS,
            }

    def execute(self, sql: str, params: list | tuple | None = None) -> dict:
        """Execute one statement on a writable connection."""

        if not self.writable:
            raise ValueError(
                "Write operations require writable mode. Restart server with --writable."
            )
        statement = _normalise_sql(sql)
        bound_params = [] if params is None else params
        with self._connect(read_only=False) as conn:
            cursor = conn.execute(statement, bound_params)
            conn.commit()
            return {
                "rows_affected": cursor.rowcount,
                "last_inserted_id": cursor.lastrowid,
            }


_client: SQLiteClient | None = None


def _make_tool_list() -> dict:
    return {
        "tools": [
            {
                "name": "query",
                "description": (
                    "Execute one statement using a physically read-only SQLite connection. "
                    f"Results are capped at {MAX_ROWS} returned rows."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "params": {"type": "array", "items": {}, "default": []},
                    },
                    "required": ["sql"],
                },
            },
            {
                "name": "list_tables",
                "description": "List all tables and views in the database.",
                "inputSchema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "get_schema",
                "description": "Get column schema for a table or view.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"table_name": {"type": "string"}},
                    "required": ["table_name"],
                },
            },
            {
                "name": "execute",
                "description": "Execute one write statement; requires --writable.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "params": {"type": "array", "items": {}, "default": []},
                    },
                    "required": ["sql"],
                },
            },
        ]
    }


def _tool_result(req_id, payload, *, error: bool = False):  # noqa: ANN001
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False, default=str),
                }
            ],
            "isError": error,
        },
    }


def _handle_request(request):  # noqa: ANN001
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid request: not a JSON object"},
        }

    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params", {}) or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": _make_tool_list()}
    if method != "tools/call":
        if req_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    if _client is None:
        return _tool_result(req_id, {"success": False, "error": "server not initialized"}, error=True)

    tool_name = params.get("name")
    arguments = params.get("arguments", {}) or {}
    try:
        if tool_name == "query":
            result = _client.query(arguments.get("sql", ""), arguments.get("params", []))
        elif tool_name == "list_tables":
            result = {"tables": _client.list_tables()}
        elif tool_name == "get_schema":
            result = {
                "table_name": arguments.get("table_name", ""),
                "columns": _client.get_schema(arguments.get("table_name", "")),
            }
        elif tool_name == "execute":
            result = _client.execute(arguments.get("sql", ""), arguments.get("params", []))
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        return _tool_result(req_id, result)
    except (ValueError, FileNotFoundError, sqlite3.Error) as exc:
        return _tool_result(
            req_id,
            {"success": False, "error": f"{type(exc).__name__}: {exc}"},
            error=True,
        )
    except Exception as exc:
        return _tool_result(
            req_id,
            {"success": False, "error": f"Unexpected error: {type(exc).__name__}: {exc}"},
            error=True,
        )


def _run_server(db_path: str, writable: bool = False) -> None:
    global _client
    _client = SQLiteClient(db_path, writable=writable)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if isinstance(request, list):
            responses = [response for item in request if (response := _handle_request(item)) is not None]
            if responses:
                print(json.dumps(responses, ensure_ascii=False), flush=True)
        else:
            response = _handle_request(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="SQLite MCP server")
    sub = parser.add_subparsers(dest="command", required=True)

    query_parser = sub.add_parser("query", help="Execute a read-only query")
    query_parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    query_parser.add_argument("--sql", required=True)
    query_parser.add_argument("--json", action="store_true")

    tables_parser = sub.add_parser("tables", help="List tables")
    tables_parser.add_argument("--db-path", default=DEFAULT_DB_PATH)

    schema_parser = sub.add_parser("schema", help="Describe a table")
    schema_parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    schema_parser.add_argument("--table", required=True)

    serve_parser = sub.add_parser("serve", help="Run the stdio MCP server")
    serve_parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    serve_parser.add_argument("--writable", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "query":
            result = SQLiteClient(args.db_path).query(args.sql)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"Columns: {', '.join(result['columns'])}")
                print(f"Rows: {result['row_count']}")
                if result["truncated"]:
                    print(f"[Returned first {MAX_ROWS} rows]")
                print("---")
                for row in result["rows"]:
                    print(" | ".join(str(value) for value in row))
        elif args.command == "tables":
            for table in SQLiteClient(args.db_path).list_tables():
                print(f"{table['type']}: {table['name']}")
        elif args.command == "schema":
            columns = SQLiteClient(args.db_path).get_schema(args.table)
            print(f"Table: {args.table}")
            print(f"Columns ({len(columns)}):")
            for column in columns:
                pk = " [PK]" if column["pk"] else ""
                nullable = " NOT NULL" if column["notnull"] else ""
                default = (
                    f" DEFAULT {column['default']}"
                    if column["default"] is not None
                    else ""
                )
                print(f"  - {column['name']}: {column['type']}{pk}{nullable}{default}")
        elif args.command == "serve":
            _run_server(args.db_path, writable=args.writable)
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    _cli()
