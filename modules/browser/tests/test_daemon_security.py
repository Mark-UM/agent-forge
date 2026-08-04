from __future__ import annotations

import pytest

from modules.browser import daemon
from modules.common.security import UnsafeNetworkTarget


class FakeRequest:
    def __init__(self, url: str):
        self.url = url


class FakeRoute:
    def __init__(self):
        self.continued = False
        self.aborted = None

    def continue_(self):
        self.continued = True

    def abort(self, reason: str):
        self.aborted = reason


class FakeContext:
    def __init__(self):
        self.pattern = None
        self.handler = None
        self.scripts = []

    def route(self, pattern, handler):
        self.pattern = pattern
        self.handler = handler

    def add_init_script(self, script):
        self.scripts.append(script)


def test_navigation_rejects_literal_loopback():
    with pytest.raises((UnsafeNetworkTarget, ValueError)):
        daemon._validate_public_url("http://127.0.0.1:8080/admin")


def test_navigation_rejects_url_credentials():
    with pytest.raises((UnsafeNetworkTarget, ValueError)):
        daemon._validate_public_url("https://user:password@example.com/")


def test_syntax_validator_remains_backward_compatible_for_loopback():
    assert daemon._validate_url("http://127.0.0.1:8080/admin") == (
        "http://127.0.0.1:8080/admin"
    )


def test_route_allows_local_data_resource():
    route = FakeRoute()
    daemon._guard_network_route(route, FakeRequest("data:text/plain,hello"))
    assert route.continued is True
    assert route.aborted is None


def test_route_blocks_private_subresource(monkeypatch):
    def deny(url):
        raise UnsafeNetworkTarget(f"blocked {url}")

    monkeypatch.setattr(daemon, "validate_outbound_url", deny)
    route = FakeRoute()
    daemon._guard_network_route(route, FakeRequest("http://169.254.169.254/latest"))
    assert route.continued is False
    assert route.aborted == "blockedbyclient"
    assert daemon._blocked_requests


def test_route_allows_validated_public_subresource(monkeypatch):
    observed = []
    monkeypatch.setattr(daemon, "validate_outbound_url", observed.append)
    route = FakeRoute()
    daemon._guard_network_route(route, FakeRequest("https://example.com/app.js"))
    assert observed == ["https://example.com/app.js"]
    assert route.continued is True
    assert route.aborted is None


def test_context_installs_global_route_and_websocket_block(monkeypatch):
    monkeypatch.delenv("AGENT_FORGE_BROWSER_ALLOW_WEBSOCKETS", raising=False)
    context = FakeContext()
    daemon._install_browser_network_guards(context)
    assert context.pattern == "**/*"
    assert context.handler is daemon._guard_network_route
    assert len(context.scripts) == 1
    assert "WebSocket" in context.scripts[0]


def test_websocket_override_can_be_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("AGENT_FORGE_BROWSER_ALLOW_WEBSOCKETS", "1")
    context = FakeContext()
    daemon._install_browser_network_guards(context)
    assert context.pattern == "**/*"
    assert context.scripts == []


def test_arbitrary_javascript_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_FORGE_BROWSER_ALLOW_JS", raising=False)
    with pytest.raises(PermissionError, match="disabled"):
        daemon._validate_js_code("document.title")


def test_javascript_requires_explicit_opt_in_and_remains_bounded(monkeypatch):
    monkeypatch.setenv("AGENT_FORGE_BROWSER_ALLOW_JS", "1")
    assert daemon._validate_js_code("document.title") == "document.title"
    with pytest.raises(ValueError, match="exceeds"):
        daemon._validate_js_code("x" * (daemon.MAX_JS_CODE_LENGTH + 1))


def test_internal_client_sends_bearer_token(monkeypatch):
    monkeypatch.setattr(daemon, "_service_token", "x" * 32)
    headers = daemon._authorization_headers()
    assert headers["Authorization"] == f"Bearer {'x' * 32}"
    assert headers["Content-Type"] == "application/json"
