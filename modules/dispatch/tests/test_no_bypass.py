from __future__ import annotations

from pathlib import Path

from modules.dispatch.no_bypass import scan_model_bypasses


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_business_endpoint_literal_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "modules/search/direct.py",
        'URL = "https://api.deepseek.com/v1/chat/completions"\n',
    )
    findings = scan_model_bypasses(project_root=tmp_path)
    assert len(findings) >= 1
    assert findings[0].path == "modules/search/direct.py"
    assert findings[0].line == 1


def test_gateway_is_the_only_allowed_endpoint_owner(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "modules/dispatch/gateway.py",
        'BASE = "https://api.deepseek.com"\n',
    )
    assert scan_model_bypasses(project_root=tmp_path) == []


def test_tests_are_not_scanned(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "modules/search/tests/test_transport.py",
        'SENTINEL = "https://api.deepseek.com/v1/chat/completions"\n',
    )
    assert scan_model_bypasses(project_root=tmp_path) == []


def test_gateway_client_usage_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "modules/prompt/classify.py",
        "from modules.dispatch.gateway import ModelGateway\n",
    )
    assert scan_model_bypasses(project_root=tmp_path) == []


def test_current_repository_has_no_business_bypass() -> None:
    assert scan_model_bypasses() == []
