"""modules.search.pipeline_mcp — MCP entry exposing `search_pipeline` (R2-2.8).

Production MCP tool that wraps `modules.search.pipeline.SearchPipeline`.
This is the default path for `/search`; the legacy per-MCP dispatch path is
retained as a degraded fallback and is flagged with `degraded_mode=true`
when used.

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


# ── Library entry point ──────────────────────────────────────

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
) -> dict[str, Any]:
    """Library entry point. Returns the serialized SearchPipelineResult.

    When no registry/planner/cache/verifier is injected, the pipeline runs
    in degraded_mode=True (no providers, no cache, no verifier). This keeps
    the function safe to call from any context while making the degradation
    explicit in the result.

    Callers (OpenCode `/search`) SHOULD inject real callbacks. When called
    via the MCP server below, the server builds the default registry and
    wires the on-disk cache automatically.
    """
    try:
        mode_enum = SearchMode(mode)
    except ValueError:
        mode_enum = SearchMode.STANDARD

    reg = registry or build_default_registry()

    request = SearchRequest(
        query=query, mode=mode_enum, language=language,
        max_sub_queries=max_sub_queries, verify=verify,
    )
    pipeline = SearchPipeline(
        request,
        registry=reg,
        planner_fn=planner_fn,
        cache_get_fn=cache_get_fn,
        cache_store_fn=cache_store_fn,
        verify_fn=verify_fn,
    )
    result = pipeline.execute()
    return result.to_dict()


# ── On-disk cache wiring (used by MCP server) ────────────────

_CACHE_FILE = _PROJECT_ROOT / '_runtime' / 'search' / 'pipeline_cache.json'


def _load_cache() -> dict[str, Any]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_FILE.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CACHE_FILE)
    except OSError:
        pass  # cache write failure is non-fatal


def _cache_get(key: str) -> Optional[dict]:
    cache = _load_cache()
    return cache.get(key)


def _cache_store(key: str, entry: dict) -> None:
    cache = _load_cache()
    cache[key] = entry
    # Cap cache size (keep last 500 entries by insertion order via dict)
    if len(cache) > 500:
        # Drop oldest 100
        keys = list(cache.keys())[:100]
        for k in keys:
            cache.pop(k, None)
    _save_cache(cache)


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
            result = search_pipeline(
                query=query,
                mode=args.get('mode', 'standard'),
                language=args.get('language', 'auto'),
                max_sub_queries=int(args.get('max_sub_queries', 5)),
                verify=bool(args.get('verify', True)),
                registry=build_default_registry(),
                planner_fn=_build_planner_fn(),
                cache_get_fn=_cache_get,
                cache_store_fn=_cache_store,
                verify_fn=_build_verify_fn(),
            )
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'result': {
                    'content': [{'type': 'text',
                                 'text': json.dumps(result, ensure_ascii=False)}],
                    'isError': not result.get('results') and not result.get('plan'),
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
    p_run.add_argument('--json', action='store_true')

    args = parser.parse_args()

    if args.command == 'serve':
        _run_server()
        return 0

    if args.command == 'run':
        result = search_pipeline(
            query=args.query,
            mode=args.mode,
            language=args.language,
            max_sub_queries=args.max_sub_queries,
            verify=not args.no_verify,
            registry=build_default_registry(),
            planner_fn=_build_planner_fn(),
            cache_get_fn=_cache_get,
            cache_store_fn=_cache_store,
            verify_fn=_build_verify_fn(),
        )
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
