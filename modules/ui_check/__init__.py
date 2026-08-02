"""Public API for modules.ui_check (Phase 3D — Direction 7).

UI component architecture enforcer. Checks:
1. src/ui/panels/ + src/ui/components/ directories exist
2. Each Panel is a class extending BasePanel
3. No inline onclick in index.html
4. No (window as unknown as ...) global exposure
5. UIManager imports { t, setLocale } from i18n
"""
from modules.ui_check.enforcer import (
    UIFinding,
    Severity,
    run_ui_checks,
    load_whitelist,
)

__all__ = [
    'UIFinding',
    'Severity',
    'run_ui_checks',
    'load_whitelist',
]
