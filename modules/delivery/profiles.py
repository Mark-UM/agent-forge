#!/usr/bin/env python3
"""Project-scoped Delivery and UI rule profiles.

The legacy checklist encoded TypeScript/Vite/Three.js/Tower Stack assumptions as
universal requirements. This module separates rule selection from rule
execution. ``generic`` rules always apply; specialized rules activate only when
explicitly selected or supported by project evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional


class DeliveryProfile(str, Enum):
    GENERIC = "generic"
    PYTHON = "python"
    TYPESCRIPT_VITE = "typescript-vite"
    THREEJS = "threejs"
    TOWER_STACK = "tower-stack"


@dataclass(frozen=True)
class ProfileEvidence:
    profile: DeliveryProfile
    confidence: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    description: str
    profiles: tuple[DeliveryProfile, ...]
    severity: str = "error"
    check: str = ""

    def applies_to(self, selected: Iterable[DeliveryProfile]) -> bool:
        active = set(selected)
        return bool(active.intersection(self.profiles))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "profiles": [profile.value for profile in self.profiles],
            "severity": self.severity,
            "check": self.check,
        }


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    severity: str
    message: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "passed": self.passed,
            "severity": self.severity,
            "message": self.message,
            "evidence": list(self.evidence),
        }


RULE_CATALOG: tuple[RuleSpec, ...] = (
    RuleSpec(
        "generic.project_root",
        "Project root exists and is a directory",
        (DeliveryProfile.GENERIC,),
        check="project_root",
    ),
    RuleSpec(
        "generic.no_tracked_secrets",
        "Known local secret files are not part of the delivery surface",
        (DeliveryProfile.GENERIC,),
        check="no_secret_files",
    ),
    RuleSpec(
        "generic.tests_present",
        "Project includes at least one recognizable test file",
        (DeliveryProfile.GENERIC,),
        severity="warning",
        check="tests_present",
    ),
    RuleSpec(
        "python.supported_runtime",
        "Python project declares or documents Python 3.11 compatibility",
        (DeliveryProfile.PYTHON,),
        check="python_runtime",
    ),
    RuleSpec(
        "python.pytest_config",
        "Python project exposes pytest configuration or tests",
        (DeliveryProfile.PYTHON,),
        check="pytest_config",
    ),
    RuleSpec(
        "typescript.tsconfig",
        "TypeScript/Vite project provides a tsconfig",
        (DeliveryProfile.TYPESCRIPT_VITE,),
        check="tsconfig",
    ),
    RuleSpec(
        "typescript.package_scripts",
        "TypeScript/Vite project provides build and test scripts",
        (DeliveryProfile.TYPESCRIPT_VITE,),
        check="package_scripts",
    ),
    RuleSpec(
        "typescript.vite_config",
        "Vite project provides a Vite configuration file",
        (DeliveryProfile.TYPESCRIPT_VITE,),
        check="vite_config",
    ),
    RuleSpec(
        "threejs.dependency",
        "Three.js profile declares the three package",
        (DeliveryProfile.THREEJS,),
        check="three_dependency",
    ),
    RuleSpec(
        "threejs.render_loop",
        "Three.js profile includes an explicit render/update loop",
        (DeliveryProfile.THREEJS,),
        severity="warning",
        check="render_loop",
    ),
    RuleSpec(
        "tower_stack.event_bus",
        "Tower Stack profile includes an EventBus boundary",
        (DeliveryProfile.TOWER_STACK,),
        check="event_bus",
    ),
    RuleSpec(
        "tower_stack.base_panel",
        "Tower Stack profile includes the BasePanel UI abstraction",
        (DeliveryProfile.TOWER_STACK,),
        check="base_panel",
    ),
    RuleSpec(
        "tower_stack.ui_manager",
        "Tower Stack profile includes UIManager/i18n integration",
        (DeliveryProfile.TOWER_STACK,),
        check="ui_manager",
    ),
)


def _load_package_json(root: Path) -> dict[str, Any]:
    path = root / "package.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _package_dependencies(package: Mapping[str, Any]) -> set[str]:
    output: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        values = package.get(key, {})
        if isinstance(values, Mapping):
            output.update(str(name) for name in values)
    return output


def _has_any(root: Path, patterns: Iterable[str]) -> bool:
    return any(any(root.glob(pattern)) for pattern in patterns)


def detect_profiles(root: Path | str) -> tuple[ProfileEvidence, ...]:
    project = Path(root).expanduser().resolve()
    evidence: list[ProfileEvidence] = [
        ProfileEvidence(
            DeliveryProfile.GENERIC,
            1.0,
            ("generic rules always apply",),
        )
    ]
    package = _load_package_json(project)
    dependencies = _package_dependencies(package)

    python_reasons: list[str] = []
    if (project / "pyproject.toml").is_file():
        python_reasons.append("pyproject.toml")
    if (project / "requirements.txt").is_file():
        python_reasons.append("requirements.txt")
    if _has_any(project, ("*.py", "modules/**/*.py", "src/**/*.py")):
        python_reasons.append("Python source files")
    if python_reasons:
        evidence.append(
            ProfileEvidence(
                DeliveryProfile.PYTHON,
                min(1.0, 0.45 + 0.2 * len(python_reasons)),
                tuple(python_reasons),
            )
        )

    ts_reasons: list[str] = []
    if (project / "tsconfig.json").is_file():
        ts_reasons.append("tsconfig.json")
    if "vite" in dependencies or _has_any(
        project, ("vite.config.*", "src/**/*.ts", "src/**/*.tsx")
    ):
        ts_reasons.append("Vite or TypeScript source evidence")
    if ts_reasons:
        evidence.append(
            ProfileEvidence(
                DeliveryProfile.TYPESCRIPT_VITE,
                min(1.0, 0.55 + 0.2 * len(ts_reasons)),
                tuple(ts_reasons),
            )
        )

    three_reasons: list[str] = []
    if "three" in dependencies:
        three_reasons.append("package.json dependency: three")
    if _has_any(project, ("src/**/*Scene*.ts", "src/**/*Renderer*.ts")):
        three_reasons.append("Scene/Renderer source markers")
    if three_reasons:
        evidence.append(
            ProfileEvidence(
                DeliveryProfile.THREEJS,
                min(1.0, 0.6 + 0.2 * len(three_reasons)),
                tuple(three_reasons),
            )
        )

    tower_markers = {
        "EventBus": _has_any(project, ("src/**/*EventBus*", "src/**/event-bus.*")),
        "BasePanel": _has_any(project, ("src/**/*BasePanel*",)),
        "UIManager": _has_any(project, ("src/**/*UIManager*",)),
    }
    tower_reasons = [name for name, present in tower_markers.items() if present]
    if len(tower_reasons) >= 2:
        evidence.append(
            ProfileEvidence(
                DeliveryProfile.TOWER_STACK,
                min(1.0, 0.55 + 0.15 * len(tower_reasons)),
                tuple(f"source marker: {name}" for name in tower_reasons),
            )
        )
    return tuple(evidence)


def _profile(value: str | DeliveryProfile) -> DeliveryProfile:
    if isinstance(value, DeliveryProfile):
        return value
    try:
        return DeliveryProfile(str(value).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"unknown delivery profile {value!r}; expected "
            f"{[profile.value for profile in DeliveryProfile]}"
        ) from exc


def resolve_profiles(
    root: Path | str,
    *,
    explicit: Optional[Iterable[str | DeliveryProfile]] = None,
    auto_detect: bool = True,
) -> tuple[DeliveryProfile, ...]:
    selected: list[DeliveryProfile] = [DeliveryProfile.GENERIC]
    if auto_detect:
        selected.extend(item.profile for item in detect_profiles(root))
    if explicit is not None:
        selected.extend(_profile(value) for value in explicit)

    # Specialized profiles imply their lower-level platform profiles.
    if DeliveryProfile.TOWER_STACK in selected:
        selected.extend(
            [DeliveryProfile.THREEJS, DeliveryProfile.TYPESCRIPT_VITE]
        )
    elif DeliveryProfile.THREEJS in selected:
        selected.append(DeliveryProfile.TYPESCRIPT_VITE)

    output: list[DeliveryProfile] = []
    for profile in DeliveryProfile:
        if profile in selected and profile not in output:
            output.append(profile)
    return tuple(output)


def select_rules(
    profiles: Iterable[DeliveryProfile],
    *,
    catalog: Iterable[RuleSpec] = RULE_CATALOG,
) -> tuple[RuleSpec, ...]:
    active = tuple(profiles)
    return tuple(rule for rule in catalog if rule.applies_to(active))


def _test_files(root: Path) -> list[Path]:
    patterns = (
        "test_*.py",
        "*_test.py",
        "tests/**/*.py",
        "**/*.test.ts",
        "**/*.test.tsx",
        "**/*.spec.ts",
        "**/*.spec.tsx",
    )
    files: set[Path] = set()
    for pattern in patterns:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files)


def _source_contains(root: Path, tokens: Iterable[str]) -> tuple[bool, tuple[str, ...]]:
    matched: list[str] = []
    lowered = tuple(token.lower() for token in tokens)
    for pattern in ("src/**/*.ts", "src/**/*.tsx", "src/**/*.js", "src/**/*.py"):
        for path in root.glob(pattern):
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if any(token in content for token in lowered):
                matched.append(str(path.relative_to(root)).replace("\\", "/"))
                if len(matched) >= 10:
                    return True, tuple(matched)
    return bool(matched), tuple(matched)


def _evaluate(rule: RuleSpec, root: Path, package: Mapping[str, Any]) -> RuleResult:
    scripts = package.get("scripts", {}) if isinstance(package.get("scripts"), Mapping) else {}
    dependencies = _package_dependencies(package)
    passed = False
    evidence: tuple[str, ...] = ()

    if rule.check == "project_root":
        passed = root.is_dir()
    elif rule.check == "no_secret_files":
        forbidden = [
            path
            for path in (
                root / "markconfig" / "secrets.json",
                root / ".env",
                root / ".env.local",
            )
            if path.is_file()
        ]
        # Presence alone is not a delivery failure for local workspaces. A
        # .gitignore covering those paths is sufficient evidence.
        ignored = ""
        try:
            ignored = (root / ".gitignore").read_text(encoding="utf-8")
        except OSError:
            pass
        uncovered = [path for path in forbidden if path.name not in ignored and str(path.relative_to(root)).replace("\\", "/") not in ignored]
        passed = not uncovered
        evidence = tuple(str(path.relative_to(root)) for path in uncovered)
    elif rule.check == "tests_present":
        files = _test_files(root)
        passed = bool(files)
        evidence = tuple(str(path.relative_to(root)).replace("\\", "/") for path in files[:10])
    elif rule.check == "python_runtime":
        sources = []
        for name in ("pyproject.toml", "runtime.txt", ".python-version", "README.md"):
            path = root / name
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "3.11" in text or name == "requirements.txt":
                    sources.append(name)
        passed = bool(sources) or (root / "requirements.txt").is_file()
        evidence = tuple(sources)
    elif rule.check == "pytest_config":
        passed = bool(_test_files(root)) or any(
            (root / name).is_file()
            for name in ("pytest.ini", "pyproject.toml", "tox.ini")
        )
    elif rule.check == "tsconfig":
        passed = (root / "tsconfig.json").is_file()
    elif rule.check == "package_scripts":
        passed = bool(scripts.get("build")) and bool(scripts.get("test"))
        evidence = tuple(sorted(str(name) for name in scripts))
    elif rule.check == "vite_config":
        paths = list(root.glob("vite.config.*"))
        passed = bool(paths)
        evidence = tuple(path.name for path in paths)
    elif rule.check == "three_dependency":
        passed = "three" in dependencies
    elif rule.check == "render_loop":
        passed, evidence = _source_contains(
            root, ("requestanimationframe", "setanimationloop", "renderer.render")
        )
    elif rule.check == "event_bus":
        passed, evidence = _source_contains(root, ("eventbus", "event bus"))
    elif rule.check == "base_panel":
        passed, evidence = _source_contains(root, ("basepanel", "base panel"))
    elif rule.check == "ui_manager":
        passed, evidence = _source_contains(root, ("uimanager", "ui manager"))
    else:
        return RuleResult(
            rule.rule_id,
            False,
            "error",
            f"unknown rule check implementation: {rule.check}",
        )

    return RuleResult(
        rule_id=rule.rule_id,
        passed=passed,
        severity=rule.severity,
        message=("passed" if passed else rule.description),
        evidence=evidence,
    )


def run_profiled_checks(
    root: Path | str,
    *,
    explicit_profiles: Optional[Iterable[str | DeliveryProfile]] = None,
    auto_detect: bool = True,
) -> dict[str, Any]:
    project = Path(root).expanduser().resolve()
    profiles = resolve_profiles(
        project,
        explicit=explicit_profiles,
        auto_detect=auto_detect,
    )
    rules = select_rules(profiles)
    package = _load_package_json(project)
    results = tuple(_evaluate(rule, project, package) for rule in rules)
    blocking_failures = [
        result
        for result in results
        if not result.passed and result.severity == "error"
    ]
    return {
        "success": not blocking_failures,
        "root": str(project),
        "profiles": [profile.value for profile in profiles],
        "detected_profiles": [
            evidence.to_dict() for evidence in detect_profiles(project)
        ],
        "rules": [rule.to_dict() for rule in rules],
        "results": [result.to_dict() for result in results],
        "blocking_failure_count": len(blocking_failures),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Profile-aware delivery checks")
    parser.add_argument("--root", default=".")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--no-auto-detect", action="store_true")
    parser.add_argument("--detect-only", action="store_true")
    args = parser.parse_args(argv)

    if args.detect_only:
        payload = [
            item.to_dict() for item in detect_profiles(Path(args.root))
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    report = run_profiled_checks(
        args.root,
        explicit_profiles=args.profile,
        auto_detect=not args.no_auto_detect,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
