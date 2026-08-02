---
name: search-orchestration
description: Run privacy-preserving, provider-aware web or academic research using only currently enabled OpenCode MCP providers and truthful fallback reporting.
---

# Search Orchestration

Use this skill for multi-source research, cross-verification, technical search,
or academic discovery. For a single library or repository lookup, prefer the
purpose-built Context7 or GitHub MCP directly.

## Required sequence

1. Read current provider state from `opencode.json`.
2. Redact outbound text with `modules.search.privacy.redact_outbound` before any
   external call.
3. Check local cache unless freshness or `--no-cache` requires live data.
4. Select providers by task, not by a fixed historical layer number.
5. Normalize/deduplicate results, score quality, and verify consequential claims
   against the strongest available source.
6. Log only redacted query/result metadata through `modules.search.search`.
7. Report providers actually used, failures, fallbacks, and source URLs.

## Current providers

| Use case | Provider | Config state |
|---|---|---|
| library/framework docs | Context7 | enabled remote MCP |
| repositories/code | GitHub | enabled local MCP process |
| general web | SearXNG | enabled; public instance configured |
| general web | Serper | enabled; API key required |
| academic preprints | arXiv | enabled self-hosted MCP |
| papers/citation graph | Semantic Scholar | enabled self-hosted MCP |
| rollback only | DuckDuckGo, g-search | disabled |

Configuration does not guarantee connectivity. Never silently substitute a
disabled provider.

## Module boundaries

- `search.py`: dated history, cache, filtering, stats, save, health.
- `privacy.py`: outbound PII redaction.
- `planner.py`: optional model-assisted query decomposition.
- `i18n.py`: optional model-assisted language expansion.
- `parallel.py` / `stream.py`: execute caller-supplied callbacks; they do not
  call OpenCode MCP tools on their own.
- `quality.py`: heuristic or model-assisted quality/authority scoring.
- `verifier.py`: verifies with a caller-supplied fetch callback.
- `aggregator.py`: optional synthesis with a deterministic fallback.
- `semantic.py`: optional ChromaDB history retrieval.
- `summarize.py`: URL fetching/summarization helper.
- `export.py` / `prewarm.py`: history export and cache prewarming.
- `arxiv_mcp.py`, `semantic_scholar_mcp.py`, `serper_mcp.py`: standalone MCP
  servers configured in `opencode.json`.
- `orchestrator.py`: stage-order model/CLI with callback injection; not wired
  directly to the `/search` Slash Command.

## Privacy and reliability rules

- Redaction is regex-based and incomplete; keep runtime logs private.
- Preserve timeouts and return partial results when one provider fails.
- Do not invent recency when a provider omits dates.
- Do not claim a quality score proves factual correctness.
- Do not describe mock/demo callbacks as live MCP execution.
- History lives in `_runtime/search/search_history.YYYY-MM-DD.jsonl`, not an
  undated `search_history.jsonl` and not a nonexistent `logger.py` module.
