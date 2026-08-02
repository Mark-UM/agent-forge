"""Self-hosted MCP servers (v1.6) — replacements for archived npm packages.

Modules:
- time_mcp: Time and timezone conversion (replaces @modelcontextprotocol/server-time)
- fetch_mcp: Web content fetching + HTML-to-markdown (replaces @modelcontextprotocol/server-fetch)
- sqlite_mcp: Database interaction + schema inspection (replaces @modelcontextprotocol/server-sqlite)

All modules:
- Zero external dependencies (Python stdlib only)
- JSON-RPC 2.0 over stdio (MCP protocol)
- Single-file standalone modules
- Core functions usable as libraries
- Robust error handling

Usage:
    python -m modules.mcp.time_mcp serve
    python -m modules.mcp.fetch_mcp serve
    python -m modules.mcp.sqlite_mcp serve --db-path /path/to/db.sqlite
"""
