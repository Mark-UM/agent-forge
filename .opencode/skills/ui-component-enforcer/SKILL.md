---
name: ui-component-enforcer
description: Run five opinionated regex-based UI architecture checks for TypeScript projects.
version: 1.0.0
---

# UI Component Enforcer

`modules.ui_check.enforcer` checks:

1. expected panel/component directories;
2. separate panel classes and expected base/event patterns;
3. inline `onclick` HTML;
4. selected `window` global exposure patterns;
5. UIManager i18n imports.

```powershell
python -m modules.ui_check.enforcer --target <project-root>
python -m modules.ui_check.enforcer --target <project-root> --json
```

The implementation is regex/static analysis and follows the conventions coded
in that module. Findings can include false positives or miss dynamic behavior;
review them against the target architecture. Exit code 0 means no configured
error-severity finding, not that the UI was rendered or tested.
