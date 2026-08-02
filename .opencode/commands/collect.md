---
description: Collect a URL through the local Browser daemon, Vision, and Search aggregation
agent: build
---

# /collect — Collect a URL

Usage: `/collect <http-or-https-url> [output_path]`

Call `modules.orchestrator.agent_wrapper.run_collection_pipeline`. The effective
implementation currently uses the Browser daemon fallback: the browser-use
adapter is only a probe/placeholder and deliberately returns an integration
error before falling back. Start `/browser` first.

The fallback flow navigates, waits three seconds, captures a screenshot, calls
the Vision CLI, summarizes the recognized text, and atomically writes Markdown.
The default output is under `_runtime/reports/`.

Do not claim full DOM extraction: the current implementation recognizes a
screenshot and truncates aggregation input/output in several fallback paths.
