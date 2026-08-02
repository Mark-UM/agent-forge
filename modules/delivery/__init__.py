"""Public API for modules.delivery (Phase 3C — Direction 8).

Final delivery gate. Runs 6 check categories and produces a consolidated
PASS/FAIL report.

Categories:
1. engineering — file existence + content checks
2. resource — manifest/public/onclick integrity
3. integration — delegates to modules.integration_check
4. i18n — UIManager wiring + setLocale refresh
5. test_quality — coverage thresholds + boundary + no soft assert
6. architecture — no any/default export/window/Core imports three
"""
from modules.delivery.checklist import (
    DeliveryFinding,
    Severity,
    run_delivery_checklist,
    load_whitelist,
)
from modules.delivery.reporter import (
    format_report_markdown,
    format_report_json,
    format_summary_table,
    group_by_category,
)

__all__ = [
    'DeliveryFinding',
    'Severity',
    'run_delivery_checklist',
    'load_whitelist',
    'format_report_markdown',
    'format_report_json',
    'format_summary_table',
    'group_by_category',
]
