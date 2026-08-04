---
name: delivery-checklist
description: Run the six-category static TypeScript/Vite delivery checklist implemented in one Python module.
version: 1.0.0
---

# Delivery Checklist

`modules.delivery.checklist` contains all six category implementations in one
file: engineering, resource, integration, i18n, test quality, and architecture.
The integration category delegates to `modules.integration_check.checker`.
All six categories live in the single `checklist.py` module; there is no
separate checks subpackage.

```powershell
python -m modules.delivery.checklist --target <project-root>
python -m modules.delivery.checklist --target <project-root> --json
python -m modules.delivery.checklist --target <project-root> \
  --whitelist <delivery-whitelist.json> \
  --integration-whitelist <integration-whitelist.json>
```

Exit code 0 means the static checklist found no error-severity issue. It does
not prove runtime behavior, compile the target, execute its tests, or run the
three review Subagents. The `/deliver` procedure coordinates those separate
activities when explicitly invoked.

The checks are opinionated for TypeScript/Vite-style projects. Do not present
them as a general-purpose delivery gate for unrelated stacks.
