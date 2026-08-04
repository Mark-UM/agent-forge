---
description: Provider-aware search procedure with privacy, history, quality, and optional academic/deep stages
argument-hint: "[--no-cache] [--save] [--recent N] [--deep] [--aggregate] [--parallel] [--stream] [--i18n] [--academic] [--s2] <query>"
agent: build
---

# /search — Search Procedure

This Slash Command is interpreted by the active agent. It is not a direct
binding to `SearchOrchestrator` and must not claim deterministic execution of
every Python stage.

## Unified Service entry point

All search execution routes through the unified `SearchService`
(`modules.search.service`). The MCP server (`pipeline_mcp.py`), the CLI
(`python -m modules.search.search pipeline`), and this Slash Command share
the same dependency wiring (provider registry, on-disk cache, planner,
verifier) and the same normalized `SearchServiceResult` contract. The Service
delegates to `SearchPipeline.execute()` — it wires dependencies, it does not
implement search logic. When the agent runs Python stages directly, prefer
`SearchService.search()` over constructing `SearchPipeline` ad hoc.

## Provider state

Read `opencode.json` at execution time. In the current repository:

- enabled general web providers: `searxng`, `serper`;
- enabled specialized providers: `context7`, `github`, `arxiv`,
  `semantic_scholar`;
- disabled rollback definitions: `duckduckgo`, `g-search`.

Do not call a disabled provider or infer provider availability from archived
layer documents. Serper also requires `SERPER_API_KEY`; every remote provider
may still fail because of network or service state.

## Procedure

1. Parse the query and flags. Reject an empty query.
2. Run outbound redaction with `modules.search.privacy.redact_outbound` before
   sending query text to any external MCP/API. If redaction fails, stop rather
   than sending unreviewed text.
3. Unless `--no-cache` is set, query the local cache through
   `python -m modules.search.search cache-get`. The unified `SearchService`
   also reads/writes the pipeline result cache at
   `_runtime/search/pipeline_cache.json` (shared by MCP, CLI, and this
   Command).
4. Choose providers from current configuration:
   - library/framework documentation: prefer Context7;
   - repository/code questions: prefer GitHub;
   - general web: use SearXNG and/or Serper;
   - `--academic`: add arXiv;
   - `--s2`: add Semantic Scholar.
5. Optional stages:
   - `--deep`: use `modules.search.planner` to produce bounded subqueries;
   - `--i18n`: use `modules.search.i18n` for language expansion;
   - `--parallel` or `--stream`: coordinate actual MCP calls at the agent
     level. The similarly named Python helpers require injected callables and
     do not discover OpenCode MCP tools themselves.
6. Normalize and deduplicate URLs with Search utilities, apply recency where
   provider metadata supports it, and score returned results with
   `modules.search.quality`.
7. When verification conditions are met, cross-check official/authoritative
   sources. `modules.search.verifier` requires a supplied fetch callback; it is
   not a standalone web client.
8. With `--aggregate`, synthesize the collected results through
   `modules.search.aggregator`; otherwise return a source-linked result list.
9. Record the redacted query and result metadata with
   `python -m modules.search.search log`. Logs are dated files under
   `_runtime/search/`.
10. With `--save`, mark the matching history entry saved. With an explicit
    memory request, persist only redacted summaries—never raw sensitive text.

## Output contract

State which providers actually returned results, any degraded/skipped stage,
the quality assessment, and direct source URLs. Configured or attempted is not
the same as successful.

## Local utility commands

```powershell
# Unified Service entry points (MCP / CLI / Command share these)
python -m modules.search.search pipeline --query "..." --mode standard --json
python -m modules.search.search pipeline --query "..." --no-cache --no-verify
python -m modules.search.search service-health
python modules/search/pipeline_mcp.py run --query "..." --json
python modules/search/pipeline_mcp.py serve

# History / cache / stats utilities
python -m modules.search.search recent --limit 20 --days 7
python -m modules.search.search stats --days 30
python -m modules.search.search find --query "keyword"
python -m modules.search.search save --query "keyword"
python -m modules.search.search cache-clean
python -m modules.search.search health
python -m modules.search.export export --format json
python -m modules.search.export export --format csv --output search.csv
python -m modules.search.prewarm run --dry-run
python -m modules.search.orchestrator "query" --dry-run
```

`pipeline` and `pipeline_mcp.py run` both route through `SearchService.search()`
— they are the canonical Python execution path and share the on-disk cache.
`service-health` reports the Service-layer status (provider registry, cache
writability, credentials); `health` pings remote MCP servers. The
`orchestrator` command prints/plans the Python pipeline. Without injected
callbacks, it does not perform the same live MCP search as this Slash Command.
