"""End-to-end smoke test: verify MCP servers launch and respond to JSON-RPC over stdio.

This validates the actual `python -m modules.mcp.X serve` invocation path used by opencode.json,
which the unit tests (calling _handle_request directly) do not cover.
"""
import json
import subprocess
import sys
import tempfile
import os
import sqlite3

PYTHON = sys.executable
PROJECT_ROOT = r'E:\system_folder\.claude\.claude'


def send(proc, obj):
    """Send a JSON-RPC request line and read one response line."""
    proc.stdin.write((json.dumps(obj) + '\n').encode('utf-8'))
    proc.stdin.flush()
    line = proc.stdout.readline().decode('utf-8').strip()
    return json.loads(line) if line else None


def test_time_mcp_e2e():
    proc = subprocess.Popen(
        [PYTHON, '-m', 'modules.mcp.time_mcp', 'serve'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=PROJECT_ROOT,
    )
    try:
        # 1. initialize
        resp = send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert resp['result']['serverInfo']['name'] == 'time-mcp', f"got: {resp}"

        # 2. tools/list
        resp = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = [t['name'] for t in resp['result']['tools']]
        assert 'get_current_time' in tools and 'convert_time' in tools, f"got: {tools}"

        # 3. tools/call get_current_time
        resp = send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_current_time", "arguments": {"timezone": "UTC"}}
        })
        data = json.loads(resp['result']['content'][0]['text'])
        assert data['timezone'] == 'UTC', f"got: {data}"
        assert '+00:00' in data['datetime'], f"got: {data}"
        print(f"[OK] time_mcp: initialize + tools/list + get_current_time(UTC) = {data['datetime']}")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_fetch_mcp_e2e():
    proc = subprocess.Popen(
        [PYTHON, '-m', 'modules.mcp.fetch_mcp', 'serve'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=PROJECT_ROOT,
    )
    try:
        # 1. initialize
        resp = send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert resp['result']['serverInfo']['name'] == 'fetch-mcp', f"got: {resp}"

        # 2. tools/list
        resp = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = [t['name'] for t in resp['result']['tools']]
        assert tools == ['fetch_url'], f"got: {tools}"

        # 3. tools/call with invalid URL (no network needed)
        resp = send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "fetch_url", "arguments": {"url": "not-a-url"}}
        })
        data = json.loads(resp['result']['content'][0]['text'])
        assert resp['result']['isError'] is True
        assert 'Invalid URL' in data['error'], f"got: {data}"
        print(f"[OK] fetch_mcp: initialize + tools/list + invalid URL handled = {data['error']}")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_sqlite_mcp_e2e():
    # Create a temp DB with one table
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT); INSERT INTO items (name) VALUES ('alpha'), ('beta');")
        conn.commit()
        conn.close()

        proc = subprocess.Popen(
            [PYTHON, '-m', 'modules.mcp.sqlite_mcp', 'serve', '--db-path', db_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=PROJECT_ROOT,
        )
        try:
            # 1. initialize
            resp = send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            assert resp['result']['serverInfo']['name'] == 'sqlite-mcp', f"got: {resp}"

            # 2. tools/list
            resp = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = [t['name'] for t in resp['result']['tools']]
            assert set(tools) == {'query', 'list_tables', 'get_schema', 'execute'}, f"got: {tools}"

            # 3. tools/call list_tables
            resp = send(proc, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "list_tables", "arguments": {}}
            })
            data = json.loads(resp['result']['content'][0]['text'])
            names = [t['name'] for t in data['tables']]
            assert 'items' in names, f"got: {names}"

            # 4. tools/call query
            resp = send(proc, {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "query", "arguments": {"sql": "SELECT name FROM items ORDER BY name"}}
            })
            data = json.loads(resp['result']['content'][0]['text'])
            assert data['rows'] == [['alpha'], ['beta']], f"got: {data}"

            # 5. tools/call execute (should fail — server started without --writable)
            resp = send(proc, {
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "execute", "arguments": {"sql": "INSERT INTO items (name) VALUES ('gamma')"}}
            })
            data = json.loads(resp['result']['content'][0]['text'])
            assert resp['result']['isError'] is True
            assert 'writable mode' in data['error'], f"got: {data}"

            print(f"[OK] sqlite_mcp: initialize + tools/list + list_tables + query + execute(read-only blocked)")
        finally:
            proc.terminate()
            proc.wait(timeout=5)
    finally:
        try:
            os.unlink(db_path)
        except PermissionError:
            pass  # Windows: file may still be locked; ignore


if __name__ == '__main__':
    test_time_mcp_e2e()
    test_fetch_mcp_e2e()
    test_sqlite_mcp_e2e()
    print("\nAll MCP servers passed end-to-end protocol smoke test.")
