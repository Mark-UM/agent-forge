from __future__ import annotations

import json
from pathlib import Path

from modules.registry.discovery import discover_modules_report


CANONICAL = {
    "name": "alpha",
    "version": "1.0.0",
    "description": "alpha module",
    "entrypoints": {"python": "python -m modules.alpha"},
    "capabilities": [
        {"name": "alpha.run", "version": "1.0", "description": "run alpha"}
    ],
    "dependencies": {"required": [], "optional": []},
    "credentials": [],
    "health_checks": [],
    "storage": [{"type": "none", "path": "", "tables": [], "description": ""}],
    "experimental": False,
}


def _module(root: Path, name: str, manifest: dict | str | None) -> Path:
    directory = root / "modules" / name
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    if isinstance(manifest, dict):
        (directory / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    elif isinstance(manifest, str):
        (directory / "manifest.json").write_text(manifest, encoding="utf-8")
    return directory


def test_invalid_json_is_retained_as_an_error(tmp_path: Path) -> None:
    _module(tmp_path, "alpha", "{not-json")
    report = discover_modules_report(tmp_path / "modules")
    assert report.manifests == []
    assert [issue.code for issue in report.errors] == ["manifest_json_invalid"]
    assert report.valid is False


def test_missing_manifest_is_an_error(tmp_path: Path) -> None:
    _module(tmp_path, "alpha", None)
    report = discover_modules_report(tmp_path / "modules")
    assert any(issue.code == "manifest_missing" for issue in report.errors)


def test_compatible_mode_warns_on_directory_name_mismatch(tmp_path: Path) -> None:
    manifest = dict(CANONICAL)
    manifest["name"] = "different"
    _module(tmp_path, "alpha", manifest)
    report = discover_modules_report(tmp_path / "modules", strict=False)
    assert len(report.manifests) == 1
    assert report.errors == []
    assert any(issue.code == "module_name_mismatch" for issue in report.warnings)


def test_strict_mode_rejects_directory_name_mismatch(tmp_path: Path) -> None:
    manifest = dict(CANONICAL)
    manifest["name"] = "different"
    _module(tmp_path, "alpha", manifest)
    report = discover_modules_report(tmp_path / "modules", strict=True)
    assert any(issue.code == "module_name_mismatch" for issue in report.errors)


def test_strict_mode_rejects_legacy_and_missing_fields(tmp_path: Path) -> None:
    manifest = {
        "module": "alpha",
        "version": "1.0.0",
        "entry_point": "python -m modules.alpha",
    }
    _module(tmp_path, "alpha", manifest)
    report = discover_modules_report(tmp_path / "modules", strict=True)
    codes = {issue.code for issue in report.errors}
    assert "missing_canonical_fields" in codes
    assert "legacy_manifest_fields" in codes


def test_strict_mode_rejects_storage_path_escape(tmp_path: Path) -> None:
    manifest = dict(CANONICAL)
    manifest["storage"] = [
        {"type": "json", "path": "../outside.json", "tables": []}
    ]
    _module(tmp_path, "alpha", manifest)
    report = discover_modules_report(tmp_path / "modules", strict=True)
    assert any(issue.code == "storage_path_escape" for issue in report.errors)


def test_duplicate_capability_owner_is_error(tmp_path: Path) -> None:
    _module(tmp_path, "alpha", CANONICAL)
    beta = dict(CANONICAL)
    beta["name"] = "beta"
    beta["entrypoints"] = {"python": "python -m modules.beta"}
    _module(tmp_path, "beta", beta)
    report = discover_modules_report(tmp_path / "modules")
    assert any(issue.code == "capability_owner_conflict" for issue in report.errors)
