---
description: Collect and summarize an HTTP(S) source with bounded fallbacks
agent: build
---

# /collect — Collect a URL

Usage: `/collect <http-or-https-url> [output_path]`

Call `modules.orchestrator.agent_wrapper.run_collection_pipeline` and report the
backend that actually succeeded. The ordered backends are:

1. `browser-use` Agent when the package and a supported LLM key are available;
2. the separately started local Browser daemon plus Vision recognition;
3. static HTTP fetch for pages that do not require browser execution.

Inputs must be absolute credential-free HTTP(S) URLs. Reports are written
atomically; the default path is under `_runtime/reports/`. Preserve the returned
fallback errors so a degraded success is not presented as a primary-backend
success.

The Browser daemon path recognizes a screenshot rather than extracting the
complete DOM. Static fetch cannot render client-side applications. Do not claim
either limitation is equivalent to autonomous browser collection.
