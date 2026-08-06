from __future__ import annotations

import json
from pathlib import Path

from modules.delivery.profiles import (
    DeliveryProfile,
    detect_profiles,
    resolve_profiles,
    run_profiled_checks,
    select_rules,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_plain_project_uses_generic_profile_only(tmp_path: Path) -> None:
    profiles = resolve_profiles(tmp_path)
    assert profiles == (DeliveryProfile.GENERIC,)
    rule_ids = {rule.rule_id for rule in select_rules(profiles)}
    assert all(rule_id.startswith("generic.") for rule_id in rule_ids)


def test_python_project_does_not_enable_typescript_or_tower_rules(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "requirements.txt", "pytest\n")
    _write(tmp_path / "src" / "app.py", "print('hello')\n")
    _write(tmp_path / "tests" / "test_app.py", "def test_ok(): assert True\n")

    profiles = resolve_profiles(tmp_path)
    assert DeliveryProfile.PYTHON in profiles
    assert DeliveryProfile.TYPESCRIPT_VITE not in profiles
    assert DeliveryProfile.THREEJS not in profiles
    assert DeliveryProfile.TOWER_STACK not in profiles
    assert not any(
        rule.rule_id.startswith("tower_stack.")
        for rule in select_rules(profiles)
    )


def test_typescript_vite_detection_does_not_imply_threejs(tmp_path: Path) -> None:
    _write(tmp_path / "tsconfig.json", "{}")
    _write(
        tmp_path / "package.json",
        json.dumps(
            {
                "devDependencies": {"vite": "latest"},
                "scripts": {"build": "vite build", "test": "vitest"},
            }
        ),
    )
    _write(tmp_path / "vite.config.ts", "export default {}")

    profiles = resolve_profiles(tmp_path)
    assert DeliveryProfile.TYPESCRIPT_VITE in profiles
    assert DeliveryProfile.THREEJS not in profiles
    assert DeliveryProfile.TOWER_STACK not in profiles


def test_threejs_profile_implies_typescript_vite(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        json.dumps({"dependencies": {"three": "latest"}}),
    )
    profiles = resolve_profiles(tmp_path)
    assert DeliveryProfile.THREEJS in profiles
    assert DeliveryProfile.TYPESCRIPT_VITE in profiles
    assert DeliveryProfile.TOWER_STACK not in profiles


def test_tower_stack_detection_requires_multiple_markers(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "core" / "EventBus.ts", "export class EventBus {}")
    _write(tmp_path / "src" / "ui" / "BasePanel.ts", "export class BasePanel {}")
    evidence = {item.profile: item for item in detect_profiles(tmp_path)}
    assert DeliveryProfile.TOWER_STACK in evidence
    assert len(evidence[DeliveryProfile.TOWER_STACK].reasons) == 2

    profiles = resolve_profiles(tmp_path)
    assert DeliveryProfile.TOWER_STACK in profiles
    assert DeliveryProfile.THREEJS in profiles
    assert DeliveryProfile.TYPESCRIPT_VITE in profiles


def test_explicit_tower_profile_activates_only_through_explicit_selection(
    tmp_path: Path,
) -> None:
    profiles = resolve_profiles(
        tmp_path,
        explicit=["tower-stack"],
        auto_detect=False,
    )
    assert profiles == (
        DeliveryProfile.GENERIC,
        DeliveryProfile.TYPESCRIPT_VITE,
        DeliveryProfile.THREEJS,
        DeliveryProfile.TOWER_STACK,
    )
    rule_ids = {rule.rule_id for rule in select_rules(profiles)}
    assert "tower_stack.event_bus" in rule_ids
    assert "tower_stack.base_panel" in rule_ids
    assert "tower_stack.ui_manager" in rule_ids


def test_generic_run_does_not_emit_project_specific_failures(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_sample.py", "def test_ok(): assert True\n")
    report = run_profiled_checks(
        tmp_path,
        explicit_profiles=["generic"],
        auto_detect=False,
    )
    assert report["profiles"] == ["generic"]
    assert not any(
        result["rule_id"].startswith(("typescript.", "threejs.", "tower_stack."))
        for result in report["results"]
    )


def test_unignored_secret_file_is_generic_failure(tmp_path: Path) -> None:
    _write(tmp_path / ".env", "SECRET=value")
    report = run_profiled_checks(
        tmp_path,
        explicit_profiles=["generic"],
        auto_detect=False,
    )
    result = next(
        item
        for item in report["results"]
        if item["rule_id"] == "generic.no_tracked_secrets"
    )
    assert result["passed"] is False
    assert report["success"] is False


def test_ignored_local_secret_file_is_allowed(tmp_path: Path) -> None:
    _write(tmp_path / ".env", "SECRET=value")
    _write(tmp_path / ".gitignore", ".env\n")
    report = run_profiled_checks(
        tmp_path,
        explicit_profiles=["generic"],
        auto_detect=False,
    )
    result = next(
        item
        for item in report["results"]
        if item["rule_id"] == "generic.no_tracked_secrets"
    )
    assert result["passed"] is True


def test_complete_tower_project_passes_profile_specific_structure(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "package.json",
        json.dumps(
            {
                "dependencies": {"three": "latest"},
                "devDependencies": {"vite": "latest"},
                "scripts": {"build": "vite build", "test": "vitest"},
            }
        ),
    )
    _write(tmp_path / "tsconfig.json", "{}")
    _write(tmp_path / "vite.config.ts", "export default {}")
    _write(
        tmp_path / "src" / "app.ts",
        "requestAnimationFrame(loop); renderer.render(scene, camera);\n",
    )
    _write(tmp_path / "src" / "core" / "EventBus.ts", "export class EventBus {}")
    _write(tmp_path / "src" / "ui" / "BasePanel.ts", "export class BasePanel {}")
    _write(tmp_path / "src" / "ui" / "UIManager.ts", "export class UIManager {}")
    _write(tmp_path / "src" / "app.test.ts", "test('ok', () => {})")

    report = run_profiled_checks(tmp_path)
    tower_results = [
        item for item in report["results"] if item["rule_id"].startswith("tower_stack.")
    ]
    assert len(tower_results) == 3
    assert all(item["passed"] for item in tower_results)
