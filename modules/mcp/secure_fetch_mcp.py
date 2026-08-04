#!/usr/bin/env python3
"""Security-enforced Fetch MCP entry point.

This module preserves the existing Fetch MCP contract while routing every
outbound request through ``modules.common.security``.  The original HTML to
Markdown converter is reused; networking is deliberately reimplemented here so
initial destinations and every redirect are validated before use.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from modules.common.security import (
    DEFAULT_NETWORK_POLICY,
    NetworkPolicy,
    UnsafeNetworkTarget,
    build_safe_url_opener,
    validate_outbound_url,
)
from modules.mcp.fetch_mcp import (
    DEFAULT_MAX_LENGTH,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    MAX_CONTENT_CAP,
    MAX_NETWORK_BYTES,
    MAX_START_INDEX,
    PROTOCOL_VERSION,
    _html_to_markdown,
)

SERVER_NAME = "secure-fetch-mcp"
SERVER_VERSION = "1.1.0"


def fetch_url(
    url: str,
    max_length: int = DEFAULT_MAX_LENGTH,
    start_index: int = 0,
    raw: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    *,
    policy: NetworkPolicy = DEFAULT_NETWORK_POLICY,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict:
    """Fetch a public HTTP(S) URL with bounded reads and redirect validation."""

    validate_outbound_url(url, policy=policy)
    if start_index < 0:
        raise ValueError(f"start_index must be >= 0, got {start_index}")
    if start_index > MAX_START_INDEX:
        raise ValueError(
            f"start_index {start_index} exceeds MAX_START_INDEX {MAX_START_INDEX}"
        )
    max_length = min(int(max_length), MAX_CONTENT_CAP)
    if max_length <= 0:
        max_length = DEFAULT_MAX_LENGTH
    timeout = max(1, min(int(timeout), 120))

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Accept-Encoding": "identity",
        },
    )
    safe_opener = opener or build_safe_url_opener(policy)
    with safe_opener.open(request, timeout=timeout) as response:
        final_url = response.geturl()
        # Defence in depth for custom/test openers and unusual handlers.
        validate_outbound_url(final_url, policy=policy)
        status_code = getattr(response, "status", response.getcode())
        content_type_full = response.headers.get("Content-Type", "")
        content_type = content_type_full.split(";", 1)[0].strip().lower()
        content_length = response.headers.get("Content-Length", "")
        total_length_known = bool(content_length and content_length.strip().isdigit())

        byte_limit = min((max_length + start_index) * 4 + 8192, MAX_NETWORK_BYTES)
        chunks: list[bytes] = []
        total_bytes_read = 0
        network_truncated = False
        while True:
            remaining = byte_limit - total_bytes_read
            if remaining <= 0:
                network_truncated = True
                break
            chunk = response.read(min(8192, remaining + 1))
            if not chunk:
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total_bytes_read += remaining
                network_truncated = True
                break
            chunks.append(chunk)
            total_bytes_read += len(chunk)

        encoding = "utf-8"
        lower_content_type = content_type_full.lower()
        if "charset=" in lower_content_type:
            encoding = content_type_full.split("charset=", 1)[1].split(";", 1)[0].strip()
        raw_bytes = b"".join(chunks)
        try:
            text = raw_bytes.decode(encoding, errors="replace")
        except LookupError:
            text = raw_bytes.decode("utf-8", errors="replace")

        if raw:
            converted = text
        elif content_type in ("text/html", "application/xhtml+xml"):
            converted = _html_to_markdown(text)
        else:
            converted = text

        total_length = len(converted)
        page = converted[start_index:] if start_index < total_length else ""
        truncated = network_truncated or len(page) > max_length
        page = page[:max_length]
        return {
            "url": final_url,
            "content_type": content_type,
            "status_code": status_code,
            "content": page,
            "truncated": truncated,
            "start_index": start_index,
            "total_length": total_length,
            "max_start_index": MAX_START_INDEX,
            "bytes_read": total_bytes_read,
            "network_truncated": network_truncated,
            "total_length_known": total_length_known,
            "network_policy": "public-only",
        }


def _tool_list() -> dict:
    return {
        "tools": [
            {
                "name": "fetch_url",
                "description": (
                    "Fetch a public HTTP(S) URL as Markdown or raw text. Private, "
                    "loopback, link-local, metadata and unsafe redirect targets are blocked."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_length": {
                            "type": "integer",
                            "default": DEFAULT_MAX_LENGTH,
                            "maximum": MAX_CONTENT_CAP,
                        },
                        "start_index": {"type": "integer", "default": 0},
                        "raw": {"type": "boolean", "default": False},
                    },
                    "required": ["url"],
                },
            }
        ]
    }


def _handle(request):  # noqa: ANN001
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid request"},
        }
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_list()}
    if method == "tools/call":
        params = request.get("params", {}) or {}
        if params.get("name") != "fetch_url":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Unknown tool"},
            }
        arguments = params.get("arguments", {}) or {}
        try:
            result = fetch_url(
                arguments.get("url", ""),
                max_length=arguments.get("max_length", DEFAULT_MAX_LENGTH),
                start_index=arguments.get("start_index", 0),
                raw=arguments.get("raw", False),
            )
            is_error = False
        except (ValueError, UnsafeNetworkTarget, urllib.error.URLError) as exc:
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
            is_error = True
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                ],
                "isError": is_error,
            },
        }
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve() -> None:
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
            responses = [response for item in request if (response := _handle(item)) is not None]
            if responses:
                print(json.dumps(responses, ensure_ascii=False), flush=True)
        else:
            response = _handle(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Security-enforced Fetch MCP server")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("--url", required=True)
    fetch_parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH)
    fetch_parser.add_argument("--start-index", type=int, default=0)
    fetch_parser.add_argument("--raw", action="store_true")
    fetch_parser.add_argument("--json", action="store_true")
    sub.add_parser("serve")
    args = parser.parse_args()
    if args.command == "serve":
        serve()
        return
    result = fetch_url(
        args.url,
        max_length=args.max_length,
        start_index=args.start_index,
        raw=args.raw,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["content"])


if __name__ == "__main__":
    _cli()
