from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.common.security import UnsafeNetworkTarget
from modules.orchestrator import agent_wrapper


def test_private_collection_target_is_rejected():
    with pytest.raises((UnsafeNetworkTarget, ValueError)):
        agent_wrapper._validate_target("http://127.0.0.1:8080/secrets")


def test_browser_use_is_disabled_without_explicit_override(monkeypatch):
    monkeypatch.setattr(agent_wrapper, "_BROWSER_USE_AVAILABLE", None)
    monkeypatch.setattr(agent_wrapper, "_browser_use_installed", lambda: True)
    monkeypatch.delenv("AGENT_FORGE_ALLOW_UNGUARDED_BROWSER_USE", raising=False)
    assert agent_wrapper._check_browser_use() is False


def test_browser_use_requires_explicit_reduced_security_override(monkeypatch):
    monkeypatch.setattr(agent_wrapper, "_BROWSER_USE_AVAILABLE", None)
    monkeypatch.setattr(agent_wrapper, "_browser_use_installed", lambda: True)
    monkeypatch.setenv("AGENT_FORGE_ALLOW_UNGUARDED_BROWSER_USE", "1")
    assert agent_wrapper._check_browser_use() is True


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]


def test_browser_daemon_client_sends_bearer_token(monkeypatch):
    observed = {}
    monkeypatch.setattr(agent_wrapper, "_browser_token", lambda: "t" * 32)

    def fake_open(request, timeout):
        observed["authorization"] = request.headers.get("Authorization")
        observed["content_type"] = request.headers.get("Content-type")
        observed["timeout"] = timeout
        return FakeResponse({"ok": True})

    monkeypatch.setattr(agent_wrapper.urllib.request, "urlopen", fake_open)
    assert agent_wrapper._call_browser_daemon("/status") == {"ok": True}
    assert observed["authorization"] == f"Bearer {'t' * 32}"
    assert observed["content_type"] == "application/json"
    assert observed["timeout"] == 30


def test_browser_daemon_response_is_bounded(monkeypatch):
    monkeypatch.setattr(agent_wrapper, "_browser_token", lambda: "t" * 32)

    class OversizedResponse(FakeResponse):
        def read(self, limit=-1):
            return b"x" * (agent_wrapper.MAX_DAEMON_RESPONSE_BYTES + 1)

    monkeypatch.setattr(
        agent_wrapper.urllib.request,
        "urlopen",
        lambda request, timeout: OversizedResponse({}),
    )
    result = agent_wrapper._call_browser_daemon("/status")
    assert "safety limit" in result["error"]


def test_merge_text_snapshots_deduplicates_complete_snapshots():
    assert agent_wrapper._merge_text_snapshots([" alpha ", "alpha", "beta"]) == (
        "alpha\n\n--- lazy-load snapshot ---\n\nbeta"
    )


def test_collection_uses_daemon_before_fetch(monkeypatch, tmp_path):
    output = tmp_path / "report.md"
    monkeypatch.setattr(agent_wrapper, "_validate_target", lambda target: target)
    monkeypatch.setattr(agent_wrapper, "_check_browser_use", lambda: False)
    monkeypatch.setattr(
        agent_wrapper,
        "_run_with_browser_daemon",
        lambda target, task, output_path: {
            "success": True,
            "backend": "browser_daemon",
            "output_path": output_path,
        },
    )

    def fetch_must_not_run(*args, **kwargs):
        raise AssertionError("fetch should not run after daemon success")

    monkeypatch.setattr(agent_wrapper, "_run_with_fetch", fetch_must_not_run)
    result = agent_wrapper.run_collection_pipeline(
        "https://example.com", str(output), "collect"
    )
    assert result["backend"] == "browser_daemon"


def test_collection_falls_back_to_secure_fetch(monkeypatch, tmp_path):
    output = tmp_path / "report.md"
    monkeypatch.setattr(agent_wrapper, "_validate_target", lambda target: target)
    monkeypatch.setattr(agent_wrapper, "_check_browser_use", lambda: False)
    monkeypatch.setattr(
        agent_wrapper,
        "_run_with_browser_daemon",
        lambda *args, **kwargs: {
            "success": False,
            "backend": "browser_daemon",
            "error": "unreachable",
        },
    )
    monkeypatch.setattr(
        agent_wrapper,
        "_run_with_fetch",
        lambda target, task, output_path: {
            "success": True,
            "backend": "fetch",
            "output_path": output_path,
        },
    )
    result = agent_wrapper.run_collection_pipeline(
        "https://example.com", str(output), "collect"
    )
    assert result["backend"] == "fetch"
    assert result["fallback_errors"] == ["unreachable"]


def test_scroll_count_is_bounded(monkeypatch):
    monkeypatch.setenv("AGENT_FORGE_COLLECTION_SCROLLS", "6")
    with pytest.raises(ValueError, match="between"):
        agent_wrapper._bounded_scroll_count()
