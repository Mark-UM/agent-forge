#!/usr/bin/env python3
"""SQLite MCP Server — Database interaction and schema inspection.

Self-hosted replacement for the archived `@modelcontextprotocol/server-sqlite` npm package
(removed from npm registry in 2025, see servers-archived repo).

Design principles:
- Zero external dependencies (Python stdlib only: sqlite3 + json)
- Single-file standalone module
- Core functions usable as a library: query, list_tables, get_schema
- MCP server: JSON-RPC 2.0 over stdio (newline-delimited)
- READ-ONLY by default for safety (write requires explicit --writable flag)
- Robust error handling: SQL injection prevention (parameterized queries),
  connection errors, malformed SQL

Protocol: MCP uses newline-delimited JSON-RPC 2.0 over stdin/stdout.

Usage as MCP server:
    python -m modules.mcp.sqlite_mcp serve --db-path /path/to/db.sqlite
    python -m modules.mcp.sqlite_mcp serve --db-path /path/to/db.sqlite --writable

Usage as library:
    from modules.mcp.sqlite_mcp import SQLiteClient
    client = SQLiteClient("/path/to/db.sqlite")
    rows = client.query("SELECT * FROM users WHERE id = ?", (1,))

CLI direct call (no MCP):
    python -m modules.mcp.sqlite_mcp query --db-path /path/to/db.sqlite \
        --sql "SELECT name FROM sqlite_master WHERE type='table'"
    python -m modules.mcp.sqlite_mcp tables --db-path /path/to/db.sqlite
    python -m modules.mcp.sqlite_mcp schema --db-path /path/to/db.sqlite --table users

Tools exposed:
    query(sql, params=[])               — execute SELECT (read-only safe)
    list_tables()                        — list all tables
    get_schema(table_name)               — get column info for a table
    execute(sql, params=[])              — execute INSERT/UPDATE/DELETE (only if writable)
"""
import sys
import os
import json
import argparse
import sqlite3

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── Constants ────────────────────────────────────────────────
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'sqlite-mcp'
SERVER_VERSION = '1.0.0'

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    '_runtime', 'mcp-sqlite.db'
)

# Maximum rows returned by a single query (safety cap)
MAX_ROWS = 1000

# SQL keywords that modify data (blocked in read-only mode)
_WRITE_KEYWORDS = frozenset((
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER',
    'TRUNCATE', 'REPLACE', 'MERGE', 'ATTACH', 'DETACH',
    'PRAGMA',  # PRAGMA can modify state
))


# ── Core client ──────────────────────────────────────────────
class SQLiteClient:
    """SQLite client with read-only safety and parameterized queries.

    Args:
        db_path: Path to SQLite database file
        writable: If False (default), only SELECT queries are allowed
                  If True, INSERT/UPDATE/DELETE/etc. are allowed
    """

    def __init__(self, db_path: str, writable: bool = False):
        if not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        self.writable = writable

    def _connect(self) -> sqlite3.Connection:
        """Create a new connection (not cached, thread-safe)."""
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database file not found: {self.db_path}")
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _is_write_query(self, sql: str) -> bool:
        """Check if a SQL statement modifies data."""
        stripped = sql.lstrip().upper()
        return any(stripped.startswith(kw) for kw in _WRITE_KEYWORDS)

    def list_tables(self) -> list:
        """List all tables in the database.

        Returns:
            list of dict: [{'name': str, 'type': str, 'sql': str or None}]
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table', 'view') ORDER BY name"
            )
            return [
                {'name': row['name'], 'type': row['type'], 'sql': row['sql']}
                for row in cursor.fetchall()
            ]

    def get_schema(self, table_name: str) -> list:
        """Get column schema for a specific table.

        Args:
            table_name: Name of the table

        Returns:
            list of dict: [{'cid': int, 'name': str, 'type': str,
                           'notnull': int, 'default': any, 'pk': int}]
        """
        if not table_name:
            raise ValueError("table_name is required")
        # Validate table exists to prevent SQL injection via PRAGMA
        tables = [t['name'] for t in self.list_tables()]
        if table_name not in tables:
            raise ValueError(
                f"Table '{table_name}' not found. Available: {', '.join(tables) or 'none'}"
            )
        with self._connect() as conn:
            cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
            return [
                {
                    'cid': row['cid'],
                    'name': row['name'],
                    'type': row['type'],
                    'notnull': bool(row['notnull']),
                    'default': row['dflt_value'],
                    'pk': bool(row['pk']),
                }
                for row in cursor.fetchall()
            ]

    def query(self, sql: str, params: list = None) -> dict:
        """Execute a SELECT query (read-only).

        Args:
            sql: SQL SELECT statement (use ? for parameters)
            params: List of parameter values

        Returns:
            dict: {
                'columns': [str],
                'rows': [list],
                'row_count': int,
                'truncated': bool,
            }
        """
        params = params or []
        if not sql:
            raise ValueError("sql is required")
        if self._is_write_query(sql) and not self.writable:
            raise ValueError(
                "Write operations (INSERT/UPDATE/DELETE/etc.) are not allowed "
                "in read-only mode. Restart server with --writable to enable."
            )

        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            total = len(rows)
            truncated = total > MAX_ROWS
            if truncated:
                rows = rows[:MAX_ROWS]
            return {
                'columns': columns,
                'rows': [list(r) for r in rows],
                'row_count': total,
                'truncated': truncated,
            }

    def execute(self, sql: str, params: list = None) -> dict:
        """Execute a write statement (INSERT/UPDATE/DELETE) — requires writable mode.

        Args:
            sql: SQL write statement (use ? for parameters)
            params: List of parameter values

        Returns:
            dict: {
                'rows_affected': int,
                'last_inserted_id': int or None,
            }
        """
        params = params or []
        if not sql:
            raise ValueError("sql is required")
        if not self.writable:
            raise ValueError(
                "Write operations require writable mode. Restart server with --writable."
            )

        with self._connect() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return {
                'rows_affected': cursor.rowcount,
                'last_inserted_id': cursor.lastrowid,
            }


# ── MCP protocol ─────────────────────────────────────────────
# Global client (initialized in _run_server)
_client: SQLiteClient = None


def _make_tool_list():
    return {
        'tools': [
            {
                'name': 'query',
                'description': (
                    'Execute a read-only SQL SELECT query. '
                    'Use ? for parameter placeholders (pass params array). '
                    f'Results capped at {MAX_ROWS} rows.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'sql': {
                            'type': 'string',
                            'description': 'SQL SELECT statement (use ? for params)',
                        },
                        'params': {
                            'type': 'array',
                            'description': 'Parameter values for ? placeholders',
                            'items': {},
                            'default': [],
                        },
                    },
                    'required': ['sql'],
                },
            },
            {
                'name': 'list_tables',
                'description': 'List all tables and views in the database.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {},
                    'required': [],
                },
            },
            {
                'name': 'get_schema',
                'description': 'Get column schema for a specific table.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'table_name': {
                            'type': 'string',
                            'description': 'Name of the table to inspect',
                        },
                    },
                    'required': ['table_name'],
                },
            },
            {
                'name': 'execute',
                'description': (
                    'Execute a write statement (INSERT/UPDATE/DELETE). '
                    'Only available if server started with --writable flag.'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'sql': {
                            'type': 'string',
                            'description': 'SQL write statement (use ? for params)',
                        },
                        'params': {
                            'type': 'array',
                            'description': 'Parameter values for ? placeholders',
                            'items': {},
                            'default': [],
                        },
                    },
                    'required': ['sql'],
                },
            },
        ]
    }


def _handle_request(request):
    if not isinstance(request, dict):
        return {
            'jsonrpc': '2.0',
            'id': None,
            'error': {'code': -32600, 'message': 'Invalid request: not a JSON object'},
        }

    method = request.get('method')
    req_id = request.get('id')
    params = request.get('params', {}) or {}

    is_notification = req_id is None

    if method == 'initialize':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {
                    'name': SERVER_NAME,
                    'version': SERVER_VERSION,
                },
            },
        }

    if method == 'notifications/initialized':
        return None

    if method == 'tools/list':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': _make_tool_list(),
        }

    if method == 'tools/call':
        tool_name = params.get('name')
        args = params.get('arguments', {}) or {}

        try:
            if tool_name == 'query':
                sql = args.get('sql', '')
                if not sql:
                    raise ValueError("sql is required")
                result = _client.query(sql, args.get('params', []))

            elif tool_name == 'list_tables':
                result = {'tables': _client.list_tables()}

            elif tool_name == 'get_schema':
                table = args.get('table_name', '')
                if not table:
                    raise ValueError("table_name is required")
                result = {
                    'table_name': table,
                    'columns': _client.get_schema(table),
                }

            elif tool_name == 'execute':
                sql = args.get('sql', '')
                if not sql:
                    raise ValueError("sql is required")
                result = _client.execute(sql, args.get('params', []))

            else:
                return {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'},
                }

            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps(result, ensure_ascii=False, default=str),
                    }],
                    'isError': False,
                },
            }
        except ValueError as e:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({'success': False, 'error': str(e)},
                                           ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }
        except sqlite3.Error as e:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({
                            'success': False,
                            'error': f'SQLite error: {e}',
                        }, ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }
        except FileNotFoundError as e:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({'success': False, 'error': str(e)},
                                           ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }
        except Exception as e:
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({
                            'success': False,
                            'error': f'Unexpected error: {type(e).__name__}: {e}',
                        }, ensure_ascii=False),
                    }],
                    'isError': True,
                },
            }

    if is_notification:
        return None
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'error': {'code': -32601, 'message': f'Method not found: {method}'},
    }


def _run_server(db_path: str, writable: bool = False):
    """Run MCP server — JSON-RPC over stdio.

    Args:
        db_path: Path to SQLite database file
        writable: If True, allow write operations
    """
    global _client
    _client = SQLiteClient(db_path, writable=writable)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = {
                'jsonrpc': '2.0',
                'id': None,
                'error': {'code': -32700, 'message': f'Parse error: {e}'},
            }
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            sys.stdout.flush()
            continue

        if isinstance(request, list):
            responses = []
            for single in request:
                resp = _handle_request(single)
                if resp is not None:
                    responses.append(resp)
            if responses:
                sys.stdout.write(json.dumps(responses, ensure_ascii=False) + '\n')
                sys.stdout.flush()
            continue

        response = _handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            sys.stdout.flush()


# ── CLI ──────────────────────────────────────────────────────
def _cli():
    parser = argparse.ArgumentParser(
        description='SQLite MCP Server — Database interaction and schema inspection'
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # query subcommand
    p_query = sub.add_parser('query', help='Execute a SELECT query')
    p_query.add_argument('--db-path', default=DEFAULT_DB_PATH,
                         help=f'SQLite database path (default: {DEFAULT_DB_PATH})')
    p_query.add_argument('--sql', required=True, help='SQL SELECT statement')
    p_query.add_argument('--json', action='store_true', help='JSON output')

    # tables subcommand
    p_tables = sub.add_parser('tables', help='List all tables')
    p_tables.add_argument('--db-path', default=DEFAULT_DB_PATH,
                          help=f'SQLite database path (default: {DEFAULT_DB_PATH})')

    # schema subcommand
    p_schema = sub.add_parser('schema', help='Get table schema')
    p_schema.add_argument('--db-path', default=DEFAULT_DB_PATH,
                          help=f'SQLite database path (default: {DEFAULT_DB_PATH})')
    p_schema.add_argument('--table', required=True, help='Table name')

    # serve subcommand
    p_serve = sub.add_parser('serve', help='Run as MCP server (JSON-RPC over stdio)')
    p_serve.add_argument('--db-path', default=DEFAULT_DB_PATH,
                         help=f'SQLite database path (default: {DEFAULT_DB_PATH})')
    p_serve.add_argument('--writable', action='store_true',
                         help='Allow write operations (INSERT/UPDATE/DELETE)')

    args = parser.parse_args()

    if args.command == 'query':
        try:
            client = SQLiteClient(args.db_path)
            result = client.query(args.sql)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"Columns: {', '.join(result['columns'])}")
                print(f"Rows: {result['row_count']}")
                if result['truncated']:
                    print(f"[Truncated to {MAX_ROWS} rows]")
                print("---")
                for row in result['rows']:
                    print(' | '.join(str(v) for v in row))
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'tables':
        try:
            client = SQLiteClient(args.db_path)
            tables = client.list_tables()
            if not tables:
                print("No tables found.")
            else:
                for t in tables:
                    print(f"{t['type']}: {t['name']}")
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'schema':
        try:
            client = SQLiteClient(args.db_path)
            columns = client.get_schema(args.table)
            print(f"Table: {args.table}")
            print(f"Columns ({len(columns)}):")
            for col in columns:
                pk = ' [PK]' if col['pk'] else ''
                nn = ' NOT NULL' if col['notnull'] else ''
                default = f" DEFAULT {col['default']}" if col['default'] is not None else ''
                print(f"  - {col['name']}: {col['type']}{pk}{nn}{default}")
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == 'serve':
        _run_server(args.db_path, writable=args.writable)


if __name__ == '__main__':
    if __package__ is None and __name__ == '__main__':
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _cli()
