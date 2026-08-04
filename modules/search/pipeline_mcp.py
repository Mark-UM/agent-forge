"""modules.search.pipeline_mcp — MCP entry exposing `search_pipeline` (R2-2.8).

Production MCP tool that delegates to the unified `SearchService`
(`modules.search.service`), which in turn wraps
`modules.search.pipeline.SearchPipeline`. This is the default path for
`/search`; the legacy per-MCP dispatch path is retained as a degraded
fallback and is flagged with `degraded_mode=true` when used.

All three entry points (MCP server, CLI, /search Command) now route through
`SearchService.search()` so that dependency wiring (provider registry, cache,
planner, verifier) and result normalization are consistent. This module keeps
its own lazy `planner_fn` / `verify_fn` builders (they import sibling
`planner.py` / `verifier.py`) and feed them into the Service.

Tool exposed (JSON-RPC 2.0 over stdio, MCP 2024-11-05):
    search_pipeline(
        query: str,
        mode: str = 'standard',         # quick|standard|deep|academic
        language: str = 'auto',
        max_sub_queries: int = 5,
        verify: bool = true,
    ) -> SearchPipelineResult (serialized)

Library usage:
    from modules.search.pipeline_mcp import search_pipeline
    result = search_pipeline('Python asyncio', mode='deep')

CLI:
    python modules/search/pipeline_mcp.py serve                # MCP server
    python modules/search/pipeline_mcp.py run --query '...'    # one-shot
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Local imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from modules.search.contracts import (
    SearchMode, SearchPipelineResult, SearchRequest,
)
from modules.search.pipeline import SearchPipeline, run_pipeline
from modules.search.providers import (
    ProviderRegistry, SerperProvider, SearXNGProvider, ArxivProvider,
    SemanticScholarProvider, LocalSemanticProvider, FetchProvider,
    default_registry,
)
from modules.search.service import (
    SearchService, SearchServiceRequest, SearchServiceResult,
    get_search_service,
)

# MCP protocol constants
PROTOCOL_VERSION = '2024-11-05'
SERVER_NAME = 'search-pipeline-mcp'
SERVER_VERSION = '1.0.0'

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


# ── Tool schema ──────────────────────────────────────────────

def _make_tool_list() -> dict[str, Any]:
    return {
        'tools': [
            {
                'name': 'search_pipeline',
                'description': (
                    'Execute the typed Search Pipeline. Decomposes the query '
                    'into sub-queries, dispatches each to a registered provider, '
                    'deduplicates, ranks, verifies, and caches. Returns a typed '
                    'SearchPipelineResult with step reports, verification status, '
                    'and per-result sub_query_id traceability. This is the '
                    'production path for /search (R2-2.8).'
                ),
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'Natural-language search query.',
                        },
                        'mode': {
                            'type': 'string',
                            'enum': ['quick', 'standard', 'deep', 'academic'],
                            'default': 'standard',
                            'description': 'Pipeline mode (deep enables planner).',
                        },
                        'language': {
                            'type': 'string',
                            'default': 'auto',
                            'description': 'Language hint: auto|en|zh|...',
                        },
                        'max_sub_queries': {
                            'type': 'integer',
                            'default': 5,
                            'minimum': 1,
                            'maximum': 10,
                            'description': 'Max sub-queries the planner may emit.',
                        },
                        'verify': {
                            'type': 'boolean',
                            'default': True,
                            'description': 'Run verification step.',
                        },
                    },
                    'required': ['query'],
                },
            },
        ]
    }


# ── Build registry from environment ──────────────────────────

def build_default_registry() -> ProviderRegistry:
    """Build the default provider registry based on environment.

    Serper is registered only if SERPER_API_KEY is set. Others are always
    registered but return [] if their backend is unavailable.
    """
    return default_registry(include_credentials_required=False)


# ── Library entry point (delegates to SearchService) ─────────

def search_pipeline(
    query: str,
    *,
    mode: str = 'standard',
    language: str = 'auto',
    max_sub_queries: int = 5,
    verify: bool = True,
    registry: Optional[ProviderRegistry] = None,
    planner_fn: Optional[Any] = None,
    cache_get_fn: Optional[Any] = None,
    cache_store_fn: Optional[Any] = None,
    verify_fn: Optional[Any] = None,
    no_cache: bool = False,
) -> dict[str, Any]:
    """Library entry point. Returns the serialized SearchPipelineResult.

    This is now a thin backward-compat shim over `SearchService.search()`.
    When no dependencies are injected, the module-level singleton is used
    (on-disk cache + default provider registry, consistent with the MCP
    server and CLI). When any dependency is injected, a fresh `SearchService`
    is constructed with exactly those dependencies so callers retain full
    control for testing.

    The return value is the serialized `SearchPipelineResult` (the inner
    `result` field of `SearchServiceResult`), preserving the pre-Service
    contract so existing callers keep working.
    """
    no_deps_injected = (
        registry is None and planner_fn is None and verify_fn is None
        and cache_get_fn is None and cache_store_fn is None
    )

    request = SearchServiceRequest(
        query=query,
        mode=mode,
        language=language,
        max_sub_queries=max_sub_queries,
        verify=verify,
        no_cache=no_cache,
    )

    if no_deps_injected:
        service = get_search_service()
    else:
        # Caller injected at least one dependency — build a fresh Service
        # honouring exactly what was (or was not) provided. None cache fns
        # fall back to the Service's on-disk default; pass no_cache=True to
        # bypass entirely.
        service = SearchService(
            registry=registry,
            planner_fn=planner_fn,
            verify_fn=verify_fn,
            cache_get_fn=cache_get_fn,
            cache_store_fn=cache_store_fn,
        )

    service_result = service.search(request)
    # Backward-compat: return the inner serialized pipeline result. On
    # failure this is None; mirror the old behaviour by returning an
    # error dict so callers can still do result.get('results', []).
    if service_result.success:
        return service_result.result or {}
    return {
        'success': False,
        'error': service_result.error,
        'degraded_mode': service_result.degraded_mode,
        'results': [],
        'plan': None,
        'verification': {'status': 'not_run'},
        'step_reports': {},
        'warnings': [service_result.error] if service_result.error else [],
    }


# ── Production service wiring (MCP server + CLI) ─────────────

def _build_production_service() -> SearchService:
    """Build a SearchService with the real planner and verifier wired.

    The on-disk cache and default provider registry come from the Service
    defaults. The planner/verifier are built lazily by this module's
    `_build_planner_fn` / `_build_verify_fn` helpers, which import the
    sibling `planner.py` / `verifier.py` modules. If those modules are
    unavailable, the Service runs in degraded mode (explicit in the result).
    """
    return SearchService(
        planner_fn=_build_planner_fn(),
        verify_fn=_build_verify_fn(),
    )


# ── Planner wiring (lazy) ────────────────────────────────────

def _build_planner_fn() -> Optional[Any]:
    """Build a planner_fn that calls the real planner.plan_query.

    Returns None if the planner module is unavailable (degraded mode).
    The wrapper translates the legacy plan_query signature to the typed
    planner_fn contract: (query, max_sub_queries, mode, language) -> dict.
    """
    try:
        _SEARCH_DIR = Path(__file__).resolve().parent
        if str(_SEARCH_DIR) not in sys.path:
            sys.path.insert(0, str(_SEARCH_DIR))
        from planner import plan_query  # type: ignore[import]
    except ImportError:
        return None

    def planner_fn(query: str, max_sub_queries: int = 5,
                   mode: str = 'standard', language: str = 'auto') -> dict:
        # plan_query doesn't take language/mode; we pass max_subqueries only.
        # mode/language are used downstream by providers.
        return plan_query(query, max_subqueries=max_sub_queries)
    return planner_fn


def _build_verify_fn() -> Optional[Any]:
    """Build a verify_fn that calls the real verifier.verify_against_authority.

    Returns None if the verifier module is unavailable. The wrapper maps the
    legacy Boolean `verified` field to the typed VerificationStatus via
    LegacySearchAdapter.
    """
    try:
        _SEARCH_DIR = Path(__file__).resolve().parent
        if str(_SEARCH_DIR) not in sys.path:
            sys.path.insert(0, str(_SEARCH_DIR))
        from verifier import verify_against_authority  # type: ignore[import]
    except ImportError:
        return None

    def verify_fn(query: str, results: list) -> dict:
        # Convert SearchResult to legacy dicts for verifier compatibility
        legacy_results = [
            {'url': r.url, 'title': r.title, 'snippet': r.snippet,
             'source': r.provider}
            for r in results
        ]
        return verify_against_authority(query, legacy_results)
    return verify_fn


# ── MCP server (JSON-RPC 2.0 over stdio) ─────────────────────

def _handle_request(request: dict) -> Optional[dict]:
    if not isinstance(request, dict):
        return {
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32600, 'message': 'Invalid request: not a JSON object'},
        }
    method = request.get('method')
    req_id = request.get('id')
    params = request.get('params', {}) or {}
    is_notification = req_id is None

    if method == 'initialize':
        return {
            'jsonrpc': '2.0', 'id': req_id,
            'result': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {}},
                'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            },
        }
    if method == 'notifications/initialized':
        return None
    if method == 'tools/list':
        return {'jsonrpc': '2.0', 'id': req_id, 'result': _make_tool_list()}
    if method == 'tools/call':
        tool_name = params.get('name')
        args = params.get('arguments', {}) or {}
        if tool_name != 'search_pipeline':
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'},
            }
        query = args.get('query', '')
        if not query:
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'result': {
                    'content': [{'type': 'text', 'text': json.dumps(
                        {'success': False, 'error': 'query is required'},
                        ensure_ascii=False)}],
                    'isError': True,
                },
            }
        try:
            # Route through the unified SearchService so the MCP server,
            # CLI, and /search Command share identical dependency wiring.
            service = _build_production_service()
            service_result = service.search(SearchServiceRequest(
                query=query,
                mode=args.get('mode', 'standard'),
                language=args.get('language', 'auto'),
                max_sub_queries=int(args.get('max_sub_queries', 5)),
                verify=bool(args.get('verify', True)),
            ))
            # Return the inner serialized pipeline result to preserve the
            # pre-Service response shape consumed by MCP clients.
            result = service_result.result or {
                'success': False,
                'error': service_result.error,
                'results': [],
                'plan': None,
            }
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'result': {
                    'content': [{'type': 'text',
                                 'text': json.dumps(result, ensure_ascii=False)}],
                    'isError': (
                        not service_result.success
                        or (not result.get('results') and not result.get('plan'))
                    ),
                },
            }
        except Exception as e:
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'result': {
                    'content': [{'type': 'text', 'text': json.dumps(
                        {'success': False, 'error': f'{type(e).__name__}: {e}'},
                        ensure_ascii=False)}],
                    'isError': True,
                },
            }
    if is_notification:
        return None
    return {
        'jsonrpc': '2.0', 'id': req_id,
        'error': {'code': -32601, 'message': f'Method not found: {method}'},
    }


def _run_server() -> None:
    """Run MCP server — JSON-RPC over stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            response = {
                'jsonrpc': '2.0', 'id': None,
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

def _cli() -> int:
    parser = argparse.ArgumentParser(
        description='Search Pipeline MCP Server (R2-2.8)')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('serve', help='Run as MCP server (JSON-RPC over stdio)')

    p_run = sub.add_parser('run', help='One-shot pipeline run')
    p_run.add_argument('--query', required=True)
    p_run.add_argument('--mode', default='standard',
                       choices=['quick', 'standard', 'deep', 'academic'])
    p_run.add_argument('--language', default='auto')
    p_run.add_argument('--max-sub-queries', type=int, default=5)
    p_run.add_argument('--no-verify', action='store_true')
    p_run.add_argument('--no-cache', action='store_true',
                       help='Bypass the on-disk result cache')
    p_run.add_argument('--json', action='store_true')

    args = parser.parse_args()

    if args.command == 'serve':
        _run_server()
        return 0

    if args.command == 'run':
        # Route through the unified SearchService (same path as MCP server).
        service = _build_production_service()
        service_result = service.search(SearchServiceRequest(
            query=args.query,
            mode=args.mode,
            language=args.language,
            max_sub_queries=args.max_sub_queries,
            verify=not args.no_verify,
            no_cache=args.no_cache,
        ))
        result = service_result.result or {
            'success': False,
            'error': service_result.error,
            'results': [],
            'plan': None,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result.get('formatted_output', ''))
            # Brief summary
            plan = result.get('plan') or {}
            print(f"\n--- Pipeline summary ---")
            print(f"Sub-queries: {len(plan.get('sub_queries', []))}")
            print(f"Results: {len(result.get('results', []))}")
            print(f"Verification: {result.get('verification', {}).get('status')}")
            print(f"Degraded: {result.get('degraded_mode')}")
            steps = result.get('step_reports', {})
            print(f"Steps executed: {len(steps)}")
            for s, r in steps.items():
                print(f"  - {s}: {r.get('status')}")
        return 0

    return 1


if __name__ == '__main__':
    sys.exit(_cli())
