"""R2-B.1: Discovery — scan modules/*/manifest.json and load manifests.

Handles legacy manifest schemas (module/entry_point/components) by normalizing
them through validate_manifest(). Modules without manifest.json are skipped
silently — they can be registered manually or added later.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from modules.registry.schema import (
    CapabilityManifest,
    ManifestValidationError,
    validate_manifest,
)

# Project root (3 levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MODULES_DIR = _PROJECT_ROOT / "modules"


def discover_modules(modules_dir: Optional[Path] = None) -> list[CapabilityManifest]:
    """Scan modules/*/manifest.json and return validated manifests.

    Args:
        modules_dir: Override the modules directory. Defaults to <project>/modules.

    Returns:
        list[CapabilityManifest]: All valid manifests found, sorted by name.

    Notes:
        - Invalid manifests are skipped (with a warning to stderr).
        - Modules without manifest.json are skipped silently.
        - Recovery manifests in _runtime/ are never scanned.
    """
    scan_dir = modules_dir or _MODULES_DIR
    if not scan_dir.exists():
        return []

    manifests: list[CapabilityManifest] = []
    errors: list[tuple[str, str]] = []

    for entry in sorted(scan_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip non-module directories
        if entry.name.startswith('.') or entry.name.startswith('_'):
            continue
        if entry.name in {'tests', '__pycache__'}:
            continue

        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue

        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            manifest = validate_manifest(raw)
            manifests.append(manifest)
        except json.JSONDecodeError as e:
            errors.append((entry.name, f"JSON parse error: {e}"))
        except ManifestValidationError as e:
            errors.append((entry.name, str(e)))
        except Exception as e:
            errors.append((entry.name, f"Unexpected error: {e}"))

    # Report errors to stderr (non-fatal)
    if errors:
        import sys
        for mod_name, err in errors:
            print(
                f"[registry] WARNING: Skipping module {mod_name!r}: {err}",
                file=sys.stderr,
            )

    return sorted(manifests, key=lambda m: m.name)


def discover_module_names(modules_dir: Optional[Path] = None) -> list[str]:
    """Return just the names of modules with valid manifests."""
    return [m.name for m in discover_modules(modules_dir)]


def find_missing_manifests(modules_dir: Optional[Path] = None) -> list[str]:
    """Return names of module directories that lack a manifest.json.

    Useful for identifying which modules need manifests added.
    Excludes directories that are clearly not modules (tests, __pycache__, etc.).
    """
    scan_dir = modules_dir or _MODULES_DIR
    if not scan_dir.exists():
        return []

    missing: list[str] = []
    for entry in sorted(scan_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith('.') or entry.name.startswith('_'):
            continue
        if entry.name in {'tests', '__pycache__'}:
            continue
        # Check if directory has any .py files (is it a real module?)
        has_python = any(entry.rglob('*.py'))
        if not has_python:
            continue
        if not (entry / "manifest.json").exists():
            missing.append(entry.name)

    return missing
