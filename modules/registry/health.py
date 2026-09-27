"""R2-B.3: Health check executor — runs manifest health_checks and collects results.

Health checks are shell commands declared in manifest.json. They must:
- Exit 0 for healthy
- Exit non-zero for unhealthy
- Complete within the declared timeout

This module executes them via subprocess and returns structured results.
It does NOT import or call module code directly — health checks are black-box.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from modules.registry.schema import CapabilityManifest, HealthCheck

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class HealthCheckResult:
    """Result of a single health check command."""
    command: str
    description: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class HealthStatus:
    """Aggregate health status for a module."""
    module_name: str
    overall: str  # "healthy" | "degraded" | "unhealthy" | "unknown"
    checks: list[HealthCheckResult] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        return {
            'module_name': self.module_name,
            'overall': self.overall,
            'passed_count': self.passed_count,
            'failed_count': self.failed_count,
            'total_checks': len(self.checks),
            'error': self.error,
            'checks': [
                {
                    'command': c.command,
                    'description': c.description,
                    'passed': c.passed,
                    'exit_code': c.exit_code,
                    'timed_out': c.timed_out,
                    'duration_ms': c.duration_ms,
                    'stdout': c.stdout[:500] if c.stdout else '',
                    'stderr': c.stderr[:500] if c.stderr else '',
                }
                for c in self.checks
            ],
        }


def _run_single_check(check: HealthCheck, cwd: Path) -> HealthCheckResult:
    """Execute a single health check command."""
    import time

    start = time.monotonic()
    try:
        if check.command == 'python' or check.command.startswith('python '):
            command = [sys.executable, *shlex.split(check.command)[1:]]
            use_shell = False
        else:
            command = check.command
            use_shell = True
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(
            command,
            shell=use_shell,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            timeout=check.timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return HealthCheckResult(
            command=check.command,
            description=check.description,
            passed=(result.returncode == 0),
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=False,
            duration_ms=duration_ms,
        )
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return HealthCheckResult(
            command=check.command,
            description=check.description,
            passed=False,
            exit_code=-1,
            stdout=e.stdout or '' if isinstance(e.stdout, str) else '',
            stderr=f"Timed out after {check.timeout}s",
            timed_out=True,
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return HealthCheckResult(
            command=check.command,
            description=check.description,
            passed=False,
            exit_code=-1,
            stdout='',
            stderr=str(e),
            timed_out=False,
            duration_ms=duration_ms,
        )


def check_module_health(manifest: CapabilityManifest,
                         cwd: Optional[Path] = None) -> HealthStatus:
    """Run all health checks for a single module.

    Args:
        manifest: The module manifest to check.
        cwd: Working directory for health check commands. Defaults to project root.

    Returns:
        HealthStatus: Aggregate result of all health checks.
    """
    work_dir = cwd or _PROJECT_ROOT

    if not manifest.health_checks:
        return HealthStatus(
            module_name=manifest.name,
            overall='unknown',
            checks=[],
            error='No health checks declared',
        )

    results: list[HealthCheckResult] = []
    for check in manifest.health_checks:
        result = _run_single_check(check, work_dir)
        results.append(result)

    # Determine overall status
    if all(r.passed for r in results):
        overall = 'healthy'
    elif any(r.passed for r in results):
        overall = 'degraded'
    else:
        overall = 'unhealthy'

    return HealthStatus(
        module_name=manifest.name,
        overall=overall,
        checks=results,
    )


def run_health_checks(modules: Optional[list[CapabilityManifest]] = None,
                       cwd: Optional[Path] = None) -> dict[str, HealthStatus]:
    """Run health checks for multiple modules.

    Args:
        modules: List of manifests to check. If None, discovers all modules.
        cwd: Working directory for health check commands.

    Returns:
        dict[str, HealthStatus]: Map of module name → health status.
    """
    if modules is None:
        from modules.registry.discovery import discover_modules
        modules = discover_modules()

    results: dict[str, HealthStatus] = {}
    for manifest in modules:
        results[manifest.name] = check_module_health(manifest, cwd)

    return results


def check_credentials(manifest: CapabilityManifest) -> list[dict]:
    """Check if required credentials (env vars) are set.

    Returns:
        list[dict]: One entry per credential, with 'env', 'required', 'present'.
    """
    results = []
    for cred in manifest.credentials:
        results.append({
            'env': cred.env,
            'required': cred.required,
            'present': bool(os.environ.get(cred.env)),
        })
    return results
