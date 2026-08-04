# TEST_REPORT.generated.md — Auto-Generated Test Report

> Generated: 2026-08-04T11:06:10.415179+00:00
> Source JSON: `_runtime\baseline.json`
> Python: unknown on unknown
> Duration: 37.26s

This report is regenerated from the pytest JSON report. Do not hand-edit; rerun `scripts/generate_test_report.py` instead.

## Summary

| Metric | Value |
|--------|-------|
| Collected | 3036 |
| Total executed | 3036 |
| Passed | 3033 |
| Failed | 0 |
| Skipped | 3 |
| Errors | 0 |
| Unique test functions | 1790 |
| Parametrized cases | 1258 |
| Logical assertions (AST `assert` count) | 1318 |
| Test source files scanned | 46 |

## Skipped Reasons

| Reason | Count |
|--------|-------|
| explicit_skip | 3 |

## External Integration Tests

Heuristic: tests whose nodeid or markers reference MCP providers, network calls, E2E/smoke flows, browser, vision, or ChromaDB.

| Metric | Value |
|--------|-------|
| Identified external tests | 526 |
| External tests passed | 526 |
| External tests skipped | 0 |

### External Test Nodeids (sample, first 20)

- `modules/bootstrap/tests/test_dependencies.py::test_activate_vendor_path_sets_project_local_browser_use_config` → passed
- `modules/bootstrap/tests/test_dependencies.py::test_activate_vendor_path_preserves_explicit_browser_use_config` → passed
- `modules/bootstrap/tests/test_dependencies.py::test_install_playwright_browser_uses_local_vendor_environment` → passed
- `modules/browser/tests/test_daemon.py::test_validate_url_accepts_http_and_https` → passed
- `modules/browser/tests/test_daemon.py::test_validate_url_rejects_unsafe_values[]` → passed
- `modules/browser/tests/test_daemon.py::test_validate_url_rejects_unsafe_values[example.com]` → passed
- `modules/browser/tests/test_daemon.py::test_validate_url_rejects_unsafe_values[file:///etc/passwd]` → passed
- `modules/browser/tests/test_daemon.py::test_validate_url_rejects_unsafe_values[https://user:pass@example.com]` → passed
- `modules/browser/tests/test_daemon.py::test_screenshot_path_is_confined` → passed
- `modules/browser/tests/test_daemon.py::test_screenshot_path_rejects_non_image` → passed
- `modules/browser/tests/test_daemon.py::test_browser_launch_defaults_to_local_runtime_profile` → passed
- `modules/browser/tests/test_daemon.py::test_browser_engine_defaults_to_chromium_and_rejects_invalid` → passed
- `modules/browser/tests/test_daemon.py::test_get_or_start_browser_reuses_open_page` → passed
- `modules/delivery/tests/test_checklist.py::TestEngineering::test_missing_files_detected` → passed
- `modules/delivery/tests/test_checklist.py::TestEngineering::test_clean_project_passes` → passed
- `modules/delivery/tests/test_checklist.py::TestEngineering::test_eslintrc_with_off_rule_detected` → passed
- `modules/delivery/tests/test_checklist.py::TestEngineering::test_vitest_low_threshold_detected` → passed
- `modules/delivery/tests/test_checklist.py::TestEngineering::test_vitest_limited_include_detected` → passed
- `modules/delivery/tests/test_checklist.py::TestEngineering::test_whitelist_downgrades_to_info` → passed
- `modules/delivery/tests/test_checklist.py::TestResource::test_missing_manifest_icons_detected` → passed
- ... and 506 more

## Per-Module Test Distribution

| Module | Test count |
|--------|------------|
| `modules/prompt/` | 1413 |
| `modules/search/` | 1121 |
| `modules/mcp/` | 129 |
| `modules/orchestrator/` | 110 |
| `modules/dispatch/` | 59 |
| `modules/bootstrap/` | 42 |
| `modules/delivery/` | 41 |
| `modules/integration_check/` | 35 |
| `modules/ui_check/` | 30 |
| `modules/scheduler/` | 29 |
| `modules/browser/` | 10 |
| `modules/memory/` | 9 |
| `modules/vision/` | 8 |

## Reproduction

```bash
# 1. Run pytest with JSON report
python -m pytest --json-report --json-report-file=_runtime/baseline.json --tb=no -q

# 2. Generate this report
python -m scripts.generate_test_report \
    --json _runtime/baseline.json \
    --out  _docs/TEST_REPORT.generated.md
```
