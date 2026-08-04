#!/usr/bin/env python3
"""Supervise Agent Forge local daemons and an optional foreground command.

The supervisor is intentionally standard-library only. It starts the Browser
and Scheduler daemons with their existing bearer tokens, verifies authenticated
health before declaring readiness, records non-secret process state, and cleans
up only processes that it owns.

Commands:

    python -m modules.runtime.supervisor start --allow-degraded
    python -m modules.runtime.supervisor status --json
    python -m modules.runtime.supervisor stop
    python -m modules.runtime.supervisor restart --allow-degraded
    python -m modules.runtime.supervisor run --allow-degraded -- opencode ...
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping, Optional
import urllib.error
import urllib.request

from modules.common.security import (
    load_or_create_service_token,
    token_fingerprint,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "_runtime" / "supervisor"
LOG_DIR = RUNTIME_DIR / "logs"
STATE_FILE = RUNTIME_DIR / "state.json"
MAX_HTTP_BYTES = 1_000_000
DEFAULT_START_TIMEOUT = 20.0
DEFAULT_STOP_TIMEOUT = 10.0
DEFAULT_POLL_SECONDS = 0.25
DEFAULT_MONITOR_SECONDS = 2.0
DEFAULT_MAX_RESTARTS = 3


class SupervisorError(RuntimeError):
    """Base class for runtime supervision errors."""


class PortConflictError(SupervisorError):
    """A configured port is occupied by a non-authenticated service."""


class ServiceStartError(SupervisorError):
    """A managed service failed to become healthy."""


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    module: str
    host: str
    port: int
    health_path: str = "/status"
    close_path: str = "/close"
    token_service: str = ""

    @property
    def command(self) -> tuple[str, ...]:
        return (sys.executable, "-m", self.module)

    @property
    def token_env(self) -> str:
        return f"AGENT_FORGE_{self.token_service.upper()}_TOKEN"


@dataclass
class ServiceState:
    name: str
    pid: Optional[int]
    command: list[str]
    host: str
    port: int
    status: str
    managed: bool
    adopted: bool
    started_at: str
    log_path: str
    token_fingerprint: str
    restart_count: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize process metadata without bearer tokens."""

        return {
            "name": self.name,
            "pid": self.pid,
            "command": list(self.command),
            "host": self.host,
            "port": self.port,
            "status": self.status,
            "managed": self.managed,
            "adopted": self.adopted,
            "started_at": self.started_at,
            "log_path": self.log_path,
            "token_fingerprint": self.token_fingerprint,
            "restart_count": self.restart_count,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ServiceState":
        return cls(
            name=str(value.get("name", "")),
            pid=int(value["pid"]) if value.get("pid") is not None else None,
            command=[str(item) for item in value.get("command", [])],
            host=str(value.get("host", "127.0.0.1")),
            port=int(value.get("port", 0)),
            status=str(value.get("status", "unknown")),
            managed=bool(value.get("managed", False)),
            adopted=bool(value.get("adopted", False)),
            started_at=str(value.get("started_at", "")),
            log_path=str(value.get("log_path", "")),
            token_fingerprint=str(value.get("token_fingerprint", "")),
            restart_count=int(value.get("restart_count", 0)),
            last_error=(
                str(value["last_error"])
                if value.get("last_error") is not None
                else None
            ),
        )


@dataclass
class StartReport:
    services: dict[str, ServiceState] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return not self.errors

    @property
    def degraded(self) -> bool:
        return bool(self.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "degraded": self.degraded,
            "services": {
                name: state.to_dict() for name, state in self.services.items()
            },
            "errors": dict(self.errors),
        }


_PROCESSES: dict[str, subprocess.Popen] = {}
_LOG_HANDLES: dict[str, Any] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def service_specs(env: Mapping[str, str] | None = None) -> dict[str, ServiceSpec]:
    environment = os.environ if env is None else env

    def port(name: str, default: int) -> int:
        try:
            value = int(environment.get(name, str(default)))
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if not 1 <= value <= 65_535:
            raise ValueError(f"{name} must be between 1 and 65535")
        return value

    return {
        "browser": ServiceSpec(
            name="browser",
            module="modules.browser.daemon",
            host="127.0.0.1",
            port=port("AGENT_FORGE_BROWSER_PORT", 9223),
            token_service="browser",
        ),
        "scheduler": ServiceSpec(
            name="scheduler",
            module="modules.scheduler.secure_daemon",
            host="127.0.0.1",
            port=port("AGENT_FORGE_SCHEDULER_PORT", 9225),
            token_service="scheduler",
        ),
    }


def parse_service_names(
    value: str | Iterable[str] | None,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    environment = os.environ if env is None else env
    if value is None:
        raw_items = environment.get(
            "AGENT_FORGE_SUPERVISOR_SERVICES", "scheduler,browser"
        ).split(",")
    elif isinstance(value, str):
        raw_items = value.split(",")
    else:
        raw_items = list(value)
    available = service_specs(environment)
    output: list[str] = []
    for raw in raw_items:
        name = str(raw).strip().lower()
        if not name or name in output:
            continue
        if name not in available:
            raise ValueError(
                f"unknown service {name!r}; expected one of {sorted(available)}"
            )
        output.append(name)
    if not output:
        raise ValueError("at least one service must be selected")
    return output


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _token(spec: ServiceSpec) -> str:
    return load_or_create_service_token(
        spec.token_service,
        runtime_root=PROJECT_ROOT / "_runtime",
    )


def authorization_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_json(
    spec: ServiceSpec,
    token: str,
    *,
    method: str,
    path: str,
    timeout: float = 2.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://{spec.host}:{spec.port}{path}",
        data=b"{}" if method == "POST" else None,
        headers=authorization_headers(token),
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_HTTP_BYTES + 1)
        if len(payload) > MAX_HTTP_BYTES:
            raise SupervisorError(f"{spec.name} response exceeded safety limit")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise SupervisorError(f"{spec.name} returned a non-object response")
        return value


def _port_open(spec: ServiceSpec, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((spec.host, spec.port), timeout=timeout):
            return True
    except OSError:
        return False


def _healthy(spec: ServiceSpec, token: str) -> bool:
    try:
        result = _request_json(
            spec,
            token,
            method="GET",
            path=spec.health_path,
            timeout=1.5,
        )
    except Exception:
        return False
    if spec.name == "browser":
        return result.get("ok") is True
    return result.get("status") in {"running", "degraded"}


def _pid_alive(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _state_payload(states: Mapping[str, ServiceState]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_root": str(PROJECT_ROOT),
        "updated_at": _utc_now(),
        "services": {name: state.to_dict() for name, state in states.items()},
    }


def write_state(states: Mapping[str, ServiceState]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(f".json.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(_state_payload(states), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_FILE)


def read_state() -> dict[str, ServiceState]:
    if not STATE_FILE.is_file():
        return {}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("project_root") != str(PROJECT_ROOT):
        return {}
    services = payload.get("services", {})
    if not isinstance(services, dict):
        return {}
    output: dict[str, ServiceState] = {}
    for name, raw in services.items():
        if isinstance(raw, dict):
            output[str(name)] = ServiceState.from_dict(raw)
    return output


def _launch_process(
    spec: ServiceSpec,
    token: str,
) -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{spec.name}.log"
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    environment = os.environ.copy()
    environment[spec.token_env] = token
    kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "env": environment,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(list(spec.command), **kwargs)
    except Exception:
        handle.close()
        raise
    _PROCESSES[spec.name] = process
    _LOG_HANDLES[spec.name] = handle
    return process


def _terminate_pid(pid: Optional[int]) -> None:
    if not _pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass


def _close_local_handles(name: str) -> None:
    process = _PROCESSES.pop(name, None)
    if process is not None:
        try:
            process.wait(timeout=0)
        except Exception:
            pass
    handle = _LOG_HANDLES.pop(name, None)
    if handle is not None:
        try:
            handle.close()
        except Exception:
            pass


def start_service(
    spec: ServiceSpec,
    *,
    start_timeout: Optional[float] = None,
) -> ServiceState:
    token = _token(spec)
    fingerprint = token_fingerprint(token)
    log_path = str(LOG_DIR / f"{spec.name}.log")

    if _port_open(spec):
        if not _healthy(spec, token):
            raise PortConflictError(
                f"{spec.host}:{spec.port} is occupied but does not pass "
                f"authenticated {spec.name} health"
            )
        return ServiceState(
            name=spec.name,
            pid=None,
            command=list(spec.command),
            host=spec.host,
            port=spec.port,
            status="healthy",
            managed=False,
            adopted=True,
            started_at=_utc_now(),
            log_path=log_path,
            token_fingerprint=fingerprint,
        )

    process = _launch_process(spec, token)
    timeout = (
        start_timeout
        if start_timeout is not None
        else _float_env("AGENT_FORGE_SUPERVISOR_START_TIMEOUT", DEFAULT_START_TIMEOUT)
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _close_local_handles(spec.name)
            raise ServiceStartError(
                f"{spec.name} exited during startup with code {process.returncode}; "
                f"see {log_path}"
            )
        if _healthy(spec, token):
            return ServiceState(
                name=spec.name,
                pid=process.pid,
                command=list(spec.command),
                host=spec.host,
                port=spec.port,
                status="healthy",
                managed=True,
                adopted=False,
                started_at=_utc_now(),
                log_path=log_path,
                token_fingerprint=fingerprint,
            )
        time.sleep(DEFAULT_POLL_SECONDS)

    _terminate_pid(process.pid)
    _close_local_handles(spec.name)
    raise ServiceStartError(
        f"{spec.name} did not become healthy within {timeout:.1f}s; see {log_path}"
    )


def stop_service(
    state: ServiceState,
    *,
    stop_timeout: Optional[float] = None,
) -> ServiceState:
    if state.adopted or not state.managed:
        state.status = "adopted"
        return state

    specs = service_specs()
    spec = specs.get(state.name)
    timeout = (
        stop_timeout
        if stop_timeout is not None
        else _float_env("AGENT_FORGE_SUPERVISOR_STOP_TIMEOUT", DEFAULT_STOP_TIMEOUT)
    )
    if spec is not None:
        try:
            _request_json(
                spec,
                _token(spec),
                method="POST",
                path=spec.close_path,
                timeout=2.0,
            )
        except Exception:
            pass

    deadline = time.monotonic() + timeout
    while _pid_alive(state.pid) and time.monotonic() < deadline:
        time.sleep(DEFAULT_POLL_SECONDS)
    if _pid_alive(state.pid):
        _terminate_pid(state.pid)
    _close_local_handles(state.name)
    state.status = "stopped"
    return state


def start_services(
    names: Iterable[str],
    *,
    allow_degraded: bool = False,
) -> StartReport:
    specs = service_specs()
    report = StartReport()
    for name in names:
        try:
            report.services[name] = start_service(specs[name])
        except Exception as exc:
            report.errors[name] = f"{type(exc).__name__}: {exc}"
            if not allow_degraded:
                for state in reversed(list(report.services.values())):
                    stop_service(state)
                write_state({})
                raise SupervisorError(report.errors[name]) from exc
    write_state(report.services)
    return report


def stop_services(states: Optional[Mapping[str, ServiceState]] = None) -> dict[str, ServiceState]:
    selected = dict(states or read_state())
    stopped: dict[str, ServiceState] = {}
    for name, state in reversed(list(selected.items())):
        stopped[name] = stop_service(state)
    write_state({})
    return stopped


def service_status(
    spec: ServiceSpec,
    state: Optional[ServiceState],
) -> dict[str, Any]:
    token = _token(spec)
    healthy = _healthy(spec, token)
    return {
        "name": spec.name,
        "host": spec.host,
        "port": spec.port,
        "healthy": healthy,
        "port_open": _port_open(spec),
        "pid": state.pid if state else None,
        "pid_alive": _pid_alive(state.pid) if state else False,
        "managed": bool(state and state.managed),
        "adopted": bool(state and state.adopted),
        "restart_count": state.restart_count if state else 0,
        "last_error": state.last_error if state else None,
        "token_fingerprint": token_fingerprint(token),
    }


def status_report(names: Iterable[str]) -> dict[str, Any]:
    specs = service_specs()
    state = read_state()
    services = {
        name: service_status(specs[name], state.get(name)) for name in names
    }
    return {
        "healthy": all(item["healthy"] for item in services.values()),
        "services": services,
        "state_file": str(STATE_FILE),
    }


def _restart_state(
    spec: ServiceSpec,
    state: ServiceState,
) -> ServiceState:
    restart_count = state.restart_count + 1
    stop_service(state, stop_timeout=1.0)
    delay = min(2 ** max(0, restart_count - 1), 8)
    time.sleep(delay)
    replacement = start_service(spec)
    replacement.restart_count = restart_count
    return replacement


def run_with_services(
    command: list[str],
    *,
    names: Iterable[str],
    allow_degraded: bool,
) -> int:
    if not command:
        raise ValueError("foreground command is required")
    report = start_services(names, allow_degraded=allow_degraded)
    environment = os.environ.copy()
    process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), env=environment)
    states = dict(report.services)
    specs = service_specs()
    max_restarts = _int_env(
        "AGENT_FORGE_SUPERVISOR_MAX_RESTARTS", DEFAULT_MAX_RESTARTS
    )
    monitor_seconds = _float_env(
        "AGENT_FORGE_SUPERVISOR_MONITOR_SECONDS", DEFAULT_MONITOR_SECONDS
    )
    try:
        while process.poll() is None:
            time.sleep(monitor_seconds)
            for name, state in list(states.items()):
                if not state.managed or state.adopted:
                    continue
                if _healthy(specs[name], _token(specs[name])):
                    continue
                state.status = "unhealthy"
                state.last_error = "authenticated health check failed"
                if state.restart_count >= max_restarts:
                    if not allow_degraded:
                        process.terminate()
                        raise SupervisorError(
                            f"{name} exceeded restart limit {max_restarts}"
                        )
                    continue
                try:
                    states[name] = _restart_state(specs[name], state)
                except Exception as exc:
                    state.last_error = f"{type(exc).__name__}: {exc}"
                    if not allow_degraded:
                        process.terminate()
                        raise
            write_state(states)
        return int(process.returncode or 0)
    finally:
        if process.poll() is None:
            process.terminate()
        stop_services(states)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Forge runtime supervisor")
    sub = parser.add_subparsers(dest="command", required=True)

    start_parser = sub.add_parser("start")
    start_parser.add_argument("--services", default=None)
    start_parser.add_argument("--allow-degraded", action="store_true")
    start_parser.add_argument("--json", action="store_true")

    stop_parser = sub.add_parser("stop")
    stop_parser.add_argument("--json", action="store_true")

    restart_parser = sub.add_parser("restart")
    restart_parser.add_argument("--services", default=None)
    restart_parser.add_argument("--allow-degraded", action="store_true")
    restart_parser.add_argument("--json", action="store_true")

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--services", default=None)
    status_parser.add_argument("--json", action="store_true")

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--services", default=None)
    run_parser.add_argument("--allow-degraded", action="store_true")
    run_parser.add_argument("foreground", nargs=argparse.REMAINDER)

    args = parser.parse_args(argv)
    if args.command == "stop":
        stopped = stop_services()
        payload = {name: state.to_dict() for name, state in stopped.items()}
        if args.json:
            _print_json(payload)
        else:
            print(f"[supervisor] stopped {len(payload)} managed service(s)")
        return 0

    names = parse_service_names(getattr(args, "services", None))
    if args.command == "status":
        payload = status_report(names)
        if args.json:
            _print_json(payload)
        else:
            for name, item in payload["services"].items():
                print(
                    f"{name}: {'healthy' if item['healthy'] else 'unhealthy'} "
                    f"({item['host']}:{item['port']})"
                )
        return 0 if payload["healthy"] else 1

    if args.command == "restart":
        stop_services()
        report = start_services(names, allow_degraded=args.allow_degraded)
        if args.json:
            _print_json(report.to_dict())
        else:
            print(
                f"[supervisor] restarted {len(report.services)} service(s); "
                f"errors={len(report.errors)}"
            )
        return 0 if report.success or args.allow_degraded else 1

    if args.command == "start":
        report = start_services(names, allow_degraded=args.allow_degraded)
        if args.json:
            _print_json(report.to_dict())
        else:
            print(
                f"[supervisor] ready={len(report.services)} "
                f"errors={len(report.errors)}"
            )
            for name, error in report.errors.items():
                print(f"[supervisor] {name}: {error}", file=sys.stderr)
        return 0 if report.success or args.allow_degraded else 1

    foreground = list(args.foreground)
    if foreground and foreground[0] == "--":
        foreground = foreground[1:]
    return run_with_services(
        foreground,
        names=names,
        allow_degraded=args.allow_degraded,
    )


if __name__ == "__main__":
    raise SystemExit(main())
