"""Unified Search Pipeline MCP, CLI, and compatibility library entrypoint."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from modules.search.factory import (
    build_planner_fn,
    build_search_service,
    build_verify_fn,
)
from modules.search.providers import ProviderRegistry, default_registry
from modules.search.service import SearchService, SearchServiceRequest

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "search-pipeline-mcp"
SERVER_VERSION = "2.0.0"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _make_tool_list() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "search_pipeline",
                "description": (
                    "Execute the canonical SearchService pipeline with mode-aware "
                    "provider fallback, truthful output usability, ranking, "
                    "verification, and bounded caching."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "enum": ["quick", "standard", "deep", "academic"],
                            "default": "standard",
                        },
                        "language": {"type": "string", "default": "auto"},
                        "max_sub_queries": {
                            "type": "integer",
                            "default": 5,
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "verify": {"type": "boolean", "default": True},
                        "no_cache": {"type": "boolean", "default": False},
                    },
                    "required": ["query"],
                },
            }
        ]
    }


def build_default_registry() -> ProviderRegistry:
    """Compatibility alias for the canonical registry builder."""

    return default_registry(include_credentials_required=False)


def _build_planner_fn() -> Optional[Any]:
    return build_planner_fn()


def _build_verify_fn() -> Optional[Any]:
    return build_verify_fn()


def _build_production_service() -> SearchService:
    return build_search_service()


def _failure_payload(service_result) -> dict:  # noqa: ANN001
    return {
        "success": False,
        "output_usable": False,
        "execution_status": service_result.execution_status,
        "error": service_result.error,
        "degraded_mode": service_result.degraded_mode,
        "provider_executions": list(service_result.provider_attempts),
        "results": [],
        "plan": None,
        "verification": {"status": "not_run"},
        "step_reports": {},
        "warnings": [service_result.error] if service_result.error else [],
    }


def search_pipeline(
    query: str,
    *,
    mode: str = "standard",
    language: str = "auto",
    max_sub_queries: int = 5,
    verify: bool = True,
    registry: Optional[ProviderRegistry] = None,
    planner_fn: Optional[Any] = None,
    cache_get_fn: Optional[Any] = None,
    cache_store_fn: Optional[Any] = None,
    verify_fn: Optional[Any] = None,
    no_cache: bool = False,
) -> dict[str, Any]:
    """Run Search and preserve the historical serialized pipeline shape."""

    injected = any(
        value is not None
        for value in (
            registry,
            planner_fn,
            cache_get_fn,
            cache_store_fn,
            verify_fn,
        )
    )
    if injected:
        service = SearchService(
            registry=registry,
            planner_fn=planner_fn,
            verify_fn=verify_fn,
            cache_get_fn=cache_get_fn,
            cache_store_fn=cache_store_fn,
        )
    else:
        service = _build_production_service()

    service_result = service.search(
        SearchServiceRequest(
            query=query,
            mode=mode,
            language=language,
            max_sub_queries=max_sub_queries,
            verify=verify,
            no_cache=no_cache,
        )
    )
    return service_result.result or _failure_payload(service_result)


def _tool_response(request_id, result: dict, *, error: bool) -> dict:  # noqa: ANN001
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False),
                }
            ],
            "isError": error,
        },
    }


def _handle_request(request: dict) -> Optional[dict]:
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid request"},
        }
    method = request.get("method")
    request_id = request.get("id")
    parameters = request.get("params", {}) or {}
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
        return {"jsonrpc": "2.0", "id": request_id, "result": _make_tool_list()}
    if method == "tools/call":
        tool_name = parameters.get("name")
        arguments = parameters.get("arguments", {}) or {}
        if tool_name != "search_pipeline":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        query = arguments.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return _tool_response(
                request_id,
                {"success": False, "error": "query is required"},
                error=True,
            )
        try:
            service = _build_production_service()
            service_result = service.search(
                SearchServiceRequest(
                    query=query,
                    mode=arguments.get("mode", "standard"),
                    language=arguments.get("language", "auto"),
                    max_sub_queries=int(arguments.get("max_sub_queries", 5)),
                    verify=bool(arguments.get("verify", True)),
                    no_cache=bool(arguments.get("no_cache", False)),
                )
            )
            result = service_result.result or _failure_payload(service_result)
            return _tool_response(
                request_id,
                result,
                error=not service_result.output_usable,
            )
        except Exception as exc:
            return _tool_response(
                request_id,
                {
                    "success": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                error=True,
            )
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _run_server() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            continue
        if isinstance(request, list):
            responses = [
                response
                for item in request
                if (response := _handle_request(item)) is not None
            ]
            if responses:
                print(json.dumps(responses, ensure_ascii=False), flush=True)
        else:
            response = _handle_request(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Unified Search Pipeline MCP")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve")
    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("--query", required=True)
    run_parser.add_argument(
        "--mode",
        default="standard",
        choices=["quick", "standard", "deep", "academic"],
    )
    run_parser.add_argument("--language", default="auto")
    run_parser.add_argument("--max-sub-queries", type=int, default=5)
    run_parser.add_argument("--no-verify", action="store_true")
    run_parser.add_argument("--no-cache", action="store_true")
    run_parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)

    if arguments.command == "serve":
        _run_server()
        return 0

    service_result = _build_production_service().search(
        SearchServiceRequest(
            query=arguments.query,
            mode=arguments.mode,
            language=arguments.language,
            max_sub_queries=arguments.max_sub_queries,
            verify=not arguments.no_verify,
            no_cache=arguments.no_cache,
        )
    )
    result = service_result.result or _failure_payload(service_result)
    if arguments.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("formatted_output", ""))
        print("\n--- Pipeline summary ---")
        print(f"Status: {service_result.execution_status}")
        print(f"Results: {len(result.get('results', []))}")
        print(f"Degraded: {service_result.degraded_mode}")
    return 0 if service_result.output_usable else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
