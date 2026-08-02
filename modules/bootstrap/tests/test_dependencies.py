"""Behavioral tests for the local dependency environment manager."""

import os
import sys
from pathlib import Path

from modules.bootstrap import dependencies


def test_activate_vendor_path_is_idempotent(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor" / "python-libs"
    vendor.mkdir(parents=True)
    monkeypatch.setattr(dependencies, "VENDOR_LIBS", vendor)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != str(vendor)])

    assert dependencies.activate_vendor_path() is True
    assert dependencies.activate_vendor_path() is True
    assert sys.path.count(str(vendor)) == 1
    assert sys.path[0] == str(vendor)


def test_activate_vendor_path_sets_project_local_browser_use_config(monkeypatch):
    monkeypatch.delenv("BROWSER_USE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)
    monkeypatch.delenv("BROWSER_USE_CLOUD_SYNC", raising=False)

    dependencies.activate_vendor_path()

    assert Path(os.environ["BROWSER_USE_CONFIG_DIR"]) == dependencies.BROWSER_USE_CONFIG_DIR
    assert Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) == dependencies.PLAYWRIGHT_BROWSERS_DIR
    assert os.environ["ANONYMIZED_TELEMETRY"] == "false"
    assert os.environ["BROWSER_USE_CLOUD_SYNC"] == "false"


def test_activate_vendor_path_preserves_explicit_browser_use_config(monkeypatch, tmp_path):
    custom = tmp_path / "browser-use"
    monkeypatch.setenv("BROWSER_USE_CONFIG_DIR", str(custom))

    dependencies.activate_vendor_path()

    assert Path(os.environ["BROWSER_USE_CONFIG_DIR"]) == custom


def test_activate_vendor_path_reports_missing_directory(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    monkeypatch.setattr(dependencies, "VENDOR_LIBS", missing)

    assert dependencies.activate_vendor_path() is False


def test_dependency_report_distinguishes_available_and_missing(monkeypatch):
    monkeypatch.setattr(
        dependencies,
        "_find_distribution_version",
        lambda distribution: "1.2.3" if distribution == "APScheduler" else None,
    )
    monkeypatch.setattr(
        dependencies,
        "_probe_import",
        lambda import_name: (import_name == "apscheduler", "module.py", None),
    )
    monkeypatch.setattr(dependencies, "_source_is_vendor", lambda source: source is not None)

    report = dependencies.dependency_report(
        {"apscheduler": "APScheduler", "browser_use": "browser-use"}
    )

    assert report["apscheduler"]["available"] is True
    assert report["apscheduler"]["version"] == "1.2.3"
    assert report["browser_use"]["available"] is False


def test_dependency_report_detects_broken_binary_import(monkeypatch):
    monkeypatch.setattr(dependencies, "_find_distribution_version", lambda _: "1.2.3")
    monkeypatch.setattr(
        dependencies,
        "_probe_import",
        lambda _: (False, None, "ModuleNotFoundError: incompatible ABI"),
    )
    monkeypatch.setattr(dependencies, "_source_is_vendor", lambda _: False)

    report = dependencies.dependency_report({"pydantic_core": "pydantic-core"})

    assert report["pydantic_core"]["available"] is False
    assert "incompatible ABI" in report["pydantic_core"]["import_error"]


def test_environment_metadata_records_interpreter(tmp_path):
    metadata_path = dependencies.write_environment_metadata(tmp_path)
    content = metadata_path.read_text(encoding="utf-8")

    assert sys.implementation.cache_tag in content
    assert str(sys.version_info.major) in content


def test_source_is_vendor_rejects_global_package(tmp_path, monkeypatch):
    vendor = tmp_path / "vendor" / "python-libs"
    vendor.mkdir(parents=True)
    monkeypatch.setattr(dependencies, "VENDOR_LIBS", vendor)

    assert dependencies._source_is_vendor(str(vendor / "package" / "__init__.py"))
    assert not dependencies._source_is_vendor(str(tmp_path / "global" / "package.py"))


def test_environment_report_detects_wrong_abi(tmp_path):
    metadata = tmp_path / dependencies.ENVIRONMENT_METADATA.name
    metadata.write_text('{"cache_tag": "cpython-313"}', encoding="utf-8")

    report = dependencies.environment_report(tmp_path)

    assert report["compatible"] is False
    assert report["current_cache_tag"] == sys.implementation.cache_tag


def test_default_lock_file_is_repository_relative():
    assert dependencies.DEFAULT_REQUIREMENTS == dependencies.PROJECT_ROOT / "requirements.lock.txt"


def test_install_playwright_browser_uses_local_vendor_environment(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return Completed()

    monkeypatch.setattr(dependencies.subprocess, "run", fake_run)

    assert dependencies.install_playwright_browser("chromium") == 0
    assert captured["command"][-3:] == ["playwright", "install", "chromium"]
    assert str(dependencies.VENDOR_LIBS) in captured["environment"]["PYTHONPATH"]
