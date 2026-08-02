---
description: Research task with three-layer search + MindSearch Planner
task_type: research
leading_words: [planner, aggregator, primary-source, citation]
priority: 100
version: 1.5.0
---

# Research Task

## Trigger
- User requests research / technical selection / documentation query
- Keywords: "搜索" / "调研" / "research" / "对比" / "查"

## Process

### 1. Planner decomposition (for complex queries)
For complex / comparative / multi-faceted queries, invoke `/search --deep`:
- MindSearch Planner decomposes into ≤5 atomic sub-queries
- Each sub-query is independently searchable

### 2. Three-layer search (per sub-query)
Follow search-orchestration SKILL:
- Layer 0: outbound PII redaction (mandatory)
- Layer 1: DuckDuckGo (default; skip if in China without proxy)
- Layer 2: SearXNG (fallback / China primary)
- Layer 3: Serper (highest quality, ToS-compliant)
- Bypass channels: context7 (docs) / github (repos)

### 3. Academic search (when relevant)
- `/search --academic` for arXiv
- `/search --s2` for Semantic Scholar

### 4. Aggregation
- MindSearch Aggregator generates structured Markdown summary
- Cite primary sources (official docs > blog posts > social media)

### 5. Output
- Structured Markdown with citations
- Distinguish: facts (cited) vs opinions (labelled) vs unknowns (explicit)
- If query is cross-language: use `/search --i18n`

## Completion criteria
- Every claim has a citation
- Primary sources preferred over secondary
- Unknowns explicitly stated, not hidden
- Aggregator output reviewed for hallucination

## Reference
- `.opencode/skills/search-orchestration/SKILL.md` for full search protocol
- `.opencode/prompts/web-planner.md` for Planner prompt
- `.opencode/prompts/result-aggregator.md` for Aggregator prompt
