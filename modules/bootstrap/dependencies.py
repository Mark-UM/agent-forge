"""Rebuild and inspect AgentForge's ignored local Python dependency layer.

Third-party packages are deliberately not committed.  This module provides one
portable activation rule and one reproducible installer for ``vendor/python-libs``.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENDOR_LIBS = PROJECT_ROOT / "vendor" / "python-libs"
DEFAULT_REQUIREMENTS = PROJECT_ROOT / "requirements.lock.txt"
ENVIRONMENT_METADATA = VENDOR_LIBS / ".agent-forge-environment.json"
BROWSER_USE_CONFIG_DIR = PROJECT_ROOT / "_runtime" / "browser-use"
PLAYWRIGHT_BROWSERS_DIR = PROJECT_ROOT / "_runtime" / "playwright-browsers"

DEFAULT_DEPENDENCIES: Mapping[str, str] = {
    "apscheduler": "APScheduler",
    "browser_use": "browser-use",
    "chromadb": "chromadb",
    "playwright": "playwright",
    "PIL": "Pillow",
    "fitz": "PyMuPDF",
    "mcp_server_git": "mcp-server-git",
}

IMPORT_PROBES: Mapping[str, tuple[str, ...]] = {
    # Importing browser_use alone only loads its lazy namespace.  Resolving
    # Agent exercises the real browser/session dependency graph and catches
    # ABI errors or an unwritable default configuration directory.
    "browser_use": ("Agent",),
}


def activate_vendor_path() -> bool:
    """Prepend the ignored vendor directory to ``sys.path`` when it exists."""
    os.environ.setdefault("BROWSER_USE_CONFIG_DIR", str(BROWSER_USE_CONFIG_DIR))
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(PLAYWRIGHT_BROWSERS_DIR))
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    os.environ.setdefault("BROWSER_USE_CLOUD_SYNC", "false")
    if not VENDOR_LIBS.is_dir():
        return False
    value = str(VENDOR_LIBS)
    while value in sys.path:
        sys.path.remove(value)
    sys.path.insert(0, value)
    return True


def _find_distribution_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _probe_import(import_name: str) -> tuple[bool, str | None, str | None]:
    """Import a dependency so ABI/linker failures cannot masquerade as healthy."""
    try:
        module = importlib.import_module(import_name)
        for attribute in IMPORT_PROBES.get(import_name, ()):
            getattr(module, attribute)
        source = getattr(module, "__file__", None)
        return True, source, None
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


def _source_is_vendor(source: str | None) -> bool:
    if not source:
        return False
    try:
        Path(source).resolve().relative_to(VENDOR_LIBS.resolve())
        return True
    except (OSError, ValueError):
        return False


def dependency_report(
    dependencies: Mapping[str, str] = DEFAULT_DEPENDENCIES,
) -> dict[str, dict[str, str | bool | None]]:
    """Return installed/version state without importing optional packages."""
    activate_vendor_path()
    report: dict[str, dict[str, str | bool | None]] = {}
    for import_name, distribution in dependencies.items():
        package_version = _find_distribution_version(distribution)
        importable, source, import_error = _probe_import(import_name)
        local = _source_is_vendor(source)
        report[import_name] = {
            "distribution": distribution,
            "available": package_version is not None and importable and local,
            "version": package_version,
            "source": source,
            "local": local,
            "import_error": import_error,
        }
    return report


def write_environment_metadata(target: Path = VENDOR_LIBS) -> Path:
    """Record the interpreter ABI used to build the ignored local environment."""
    target.mkdir(parents=True, exist_ok=True)
    metadata_path = target / ENVIRONMENT_METADATA.name
    payload = {
        "python_executable": sys.executable,
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "implementation": sys.implementation.name,
        "cache_tag": sys.implementation.cache_tag,
    }
    temporary = metadata_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(metadata_path)
    return metadata_path


def environment_report(target: Path = VENDOR_LIBS) -> dict[str, str | bool | None]:
    metadata_path = target / ENVIRONMENT_METADATA.name
    recorded: dict = {}
    try:
        recorded = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    expected_tag = sys.implementation.cache_tag
    recorded_tag = recorded.get("cache_tag")
    return {
        "compatible": recorded_tag == expected_tag,
        "current_cache_tag": expected_tag,
        "recorded_cache_tag": recorded_tag,
        "python_executable": sys.executable,
        "metadata_file": str(metadata_path),
    }


def install_dependencies(
    requirements: Path = DEFAULT_REQUIREMENTS,
    target: Path = VENDOR_LIBS,
    index_url: str | None = None,
) -> int:
    """Install locked dependencies into the ignored repository-local target."""
    requirements = requirements.resolve()
    target = target.resolve()
    if not requirements.is_file():
        raise FileNotFoundError(f"requirements file not found: {requirements}")
    if PROJECT_ROOT.resolve() not in target.parents:
        raise ValueError(f"dependency target must be inside the repository: {target}")
    target.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--retries",
        "10",
        "--timeout",
        "120",
        "--index-url",
        index_url
        or os.environ.get("AGENT_FORGE_PYPI_INDEX", "https://pypi.org/simple"),
        "--upgrade",
        "--target",
        str(target),
        "--requirement",
        str(requirements),
    ]
    return_code = subprocess.run(command, check=False).returncode
    if return_code == 0:
        write_environment_metadata(target)
    return return_code


def install_playwright_browser(engine: str = "chromium") -> int:
    """Install a Playwright-managed browser into ignored project runtime."""
    if engine not in {"chromium", "firefox", "webkit"}:
        raise ValueError("browser engine must be chromium, firefox, or webkit")
    activate_vendor_path()
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{VENDOR_LIBS}{os.pathsep}{existing}" if existing else str(VENDOR_LIBS)
    )
    command = [sys.executable, "-m", "playwright", "install", engine]
    return subprocess.run(command, check=False, env=environment).returncode


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="report dependency state")
    check_parser.add_argument("--json", action="store_true")
    install_parser = subparsers.add_parser("install", help="install the locked set")
    install_parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    install_parser.add_argument("--target", type=Path, default=VENDOR_LIBS)
    install_parser.add_argument("--index-url")
    browser_parser = subparsers.add_parser(
        "install-browser", help="install a Playwright browser into local runtime"
    )
    browser_parser.add_argument(
        "engine", nargs="?", default="chromium", choices=("chromium", "firefox", "webkit")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "install":
        return install_dependencies(args.requirements, args.target, args.index_url)
    if args.command == "install-browser":
        return install_playwright_browser(args.engine)
    report = dependency_report()
    environment = environment_report()
    if args.json:
        print(
            json.dumps(
                {"environment": environment, "dependencies": report},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        state = "compatible" if environment["compatible"] else "incompatible"
        print(f"environment: {state} ({environment['current_cache_tag']})")
        for name, state in report.items():
            version = state["version"] or "missing"
            print(f"{name}: {version}")
    healthy = environment["compatible"] and all(
        state["available"] for state in report.values()
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
