from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.runtime import supervisor


class FakeProcess:
    def __init__(self, *, pid: int = 1234, polls=None, returncode=None):
        self.pid = pid
        self._polls = list(polls or [None])
        self.returncode = returncode
        self.terminated = False

    def poll(self):
        if len(self._polls) > 1:
            value = self._polls.pop(0)
        else:
            value = self._polls[0]
        if value is not None:
            self.returncode = value
        return value

    def terminate(self):
        self.terminated = True
        self.returncode = -15


def _spec(name: str = "browser", port: int = 9223) -> supervisor.ServiceSpec:
    return supervisor.ServiceSpec(
        name=name,
        module=f"modules.{name}.daemon",
        host="127.0.0.1",
        port=port,
        token_service=name,
    )


def _state(*, managed=True, adopted=False) -> supervisor.ServiceState:
    return supervisor.ServiceState(
        name="browser",
        pid=1234 if managed else None,
        command=["python", "-m", "modules.browser.daemon"],
        host="127.0.0.1",
        port=9223,
        status="healthy",
        managed=managed,
        adopted=adopted,
        started_at="2026-08-05T00:00:00+00:00",
        log_path="_runtime/supervisor/logs/browser.log",
        token_fingerprint="abcdef123456",
    )


def test_parse_service_names_deduplicates_and_preserves_order() -> None:
    assert supervisor.parse_service_names(
        "browser,scheduler,browser", env={}
    ) == ["browser", "scheduler"]


def test_parse_service_names_uses_environment_default() -> None:
    assert supervisor.parse_service_names(
        None,
        env={"AGENT_FORGE_SUPERVISOR_SERVICES": "browser"},
    ) == ["browser"]


def test_unknown_service_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown service"):
        supervisor.parse_service_names("database", env={})


def test_authorization_header_contains_bearer_token() -> None:
    headers = supervisor.authorization_headers("x" * 32)
    assert headers["Authorization"] == f"Bearer {'x' * 32}"
    assert headers["Accept"] == "application/json"


def test_serialized_state_contains_fingerprint_not_token() -> None:
    serialized = _state().to_dict()
    assert serialized["token_fingerprint"] == "abcdef123456"
    assert "token" not in serialized
    assert "authorization" not in json.dumps(serialized).lower()


def test_existing_authenticated_service_is_adopted(monkeypatch) -> None:
    spec = _spec()
    monkeypatch.setattr(supervisor, "_token", lambda value: "t" * 32)
    monkeypatch.setattr(supervisor, "_port_open", lambda value: True)
    monkeypatch.setattr(supervisor, "_healthy", lambda value, token: True)
    monkeypatch.setattr(
        supervisor,
        "_launch_process",
        lambda *args: pytest.fail("adopted service must not be launched"),
    )

    state = supervisor.start_service(spec)

    assert state.adopted is True
    assert state.managed is False
    assert state.pid is None


def test_occupied_unauthenticated_port_is_a_conflict(monkeypatch) -> None:
    spec = _spec()
    monkeypatch.setattr(supervisor, "_token", lambda value: "t" * 32)
    monkeypatch.setattr(supervisor, "_port_open", lambda value: True)
    monkeypatch.setattr(supervisor, "_healthy", lambda value, token: False)

    with pytest.raises(supervisor.PortConflictError, match="occupied"):
        supervisor.start_service(spec)


def test_managed_service_waits_for_authenticated_health(monkeypatch) -> None:
    spec = _spec()
    process = FakeProcess(polls=[None, None])
    health = iter([False, True])
    monkeypatch.setattr(supervisor, "_token", lambda value: "t" * 32)
    monkeypatch.setattr(supervisor, "_port_open", lambda value: False)
    monkeypatch.setattr(supervisor, "_launch_process", lambda value, token: process)
    monkeypatch.setattr(supervisor, "_healthy", lambda value, token: next(health))
    monkeypatch.setattr(supervisor.time, "sleep", lambda value: None)

    state = supervisor.start_service(spec, start_timeout=1)

    assert state.managed is True
    assert state.adopted is False
    assert state.pid == process.pid


def test_process_exit_during_startup_is_reported(monkeypatch) -> None:
    spec = _spec()
    process = FakeProcess(polls=[7], returncode=7)
    closed = []
    monkeypatch.setattr(supervisor, "_token", lambda value: "t" * 32)
    monkeypatch.setattr(supervisor, "_port_open", lambda value: False)
    monkeypatch.setattr(supervisor, "_launch_process", lambda value, token: process)
    monkeypatch.setattr(supervisor, "_close_local_handles", closed.append)

    with pytest.raises(supervisor.ServiceStartError, match="code 7"):
        supervisor.start_service(spec, start_timeout=1)
    assert closed == ["browser"]


def test_stop_does_not_terminate_adopted_service(monkeypatch) -> None:
    monkeypatch.setattr(
        supervisor,
        "_request_json",
        lambda *args, **kwargs: pytest.fail("adopted service must not be closed"),
    )
    monkeypatch.setattr(
        supervisor,
        "_terminate_pid",
        lambda pid: pytest.fail("adopted service must not be killed"),
    )
    state = supervisor.stop_service(_state(managed=False, adopted=True))
    assert state.status == "adopted"


def test_write_state_is_atomic_and_secret_free(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "supervisor"
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(supervisor, "STATE_FILE", runtime / "state.json")

    supervisor.write_state({"browser": _state()})
    text = (runtime / "state.json").read_text(encoding="utf-8")
    payload = json.loads(text)

    assert payload["services"]["browser"]["token_fingerprint"] == "abcdef123456"
    assert "Bearer" not in text
    assert "AGENT_FORGE_BROWSER_TOKEN" not in text


def test_degraded_start_collects_error_without_stopping_ready_service(
    monkeypatch,
) -> None:
    specs = {"browser": _spec(), "scheduler": _spec("scheduler", 9225)}
    ready = _state()

    def start(spec):
        if spec.name == "scheduler":
            raise supervisor.ServiceStartError("missing dependency")
        return ready

    written = []
    monkeypatch.setattr(supervisor, "service_specs", lambda env=None: specs)
    monkeypatch.setattr(supervisor, "start_service", start)
    monkeypatch.setattr(supervisor, "write_state", written.append)

    report = supervisor.start_services(
        ["browser", "scheduler"], allow_degraded=True
    )

    assert report.services == {"browser": ready}
    assert "scheduler" in report.errors
    assert written[-1] == {"browser": ready}


def test_explicit_empty_stop_set_does_not_load_stale_state(monkeypatch) -> None:
    monkeypatch.setattr(
        supervisor,
        "read_state",
        lambda: pytest.fail("explicit empty state must not read state.json"),
    )
    monkeypatch.setattr(supervisor, "write_state", lambda states: None)
    assert supervisor.stop_services({}) == {}


def test_foreground_launch_failure_cleans_up_started_services(monkeypatch) -> None:
    state = _state()
    report = supervisor.StartReport(services={"browser": state})
    cleaned = []
    monkeypatch.setattr(
        supervisor,
        "start_services",
        lambda names, allow_degraded: report,
    )
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing command")),
    )
    monkeypatch.setattr(
        supervisor,
        "stop_services",
        lambda states=None: cleaned.append(dict(states or {})) or {},
    )

    with pytest.raises(OSError, match="missing command"):
        supervisor.run_with_services(
            ["missing-command"],
            names=["browser"],
            allow_degraded=False,
        )

    assert cleaned == [{"browser": state}]
