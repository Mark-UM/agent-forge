---
name: integration-link-checker
description: Run the repository's five regex-based TypeScript integration checks before delivery.
version: 1.0.0
---

# Integration Link Checker

`modules.integration_check.checker` implements five static rules:

| Rule | Default severity | Scope |
|---|---|---|
| `init_called` | error | defined initialization methods not found in configured bootstrap files |
| `event_balance` | error | emitted events without a matching listener |
| `state_cleanup` | error | requestAnimationFrame usage without expected pause cleanup |
| `i18n_usage` | error | configured UI text patterns outside translation calls |
| `cross_layer_call` | warning | configured direct cross-layer calls |

```powershell
python -m modules.integration_check.checker --target <project-root>
python -m modules.integration_check.checker --target <project-root> --json
python -m modules.integration_check.checker --target <project-root> --whitelist <file>
```

Rule definitions live in `modules/integration_check/rules/*.json`. Findings are
heuristic regex/static-analysis results, not TypeScript compiler proof. Review
warnings and false positives manually. Whitelisted matches are retained as
informational findings rather than silently removed.
