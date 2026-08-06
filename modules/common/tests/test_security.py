from __future__ import annotations

import ipaddress
from pathlib import Path
import socket

import pytest

from modules.common.security import (
    NetworkPolicy,
    SafeRedirectHandler,
    UnsafeNetworkTarget,
    bearer_token_matches,
    load_or_create_service_token,
    token_fingerprint,
    validate_outbound_url,
)


def _resolver_for(*addresses: str):
    def resolve(host, port, *, type=socket.SOCK_STREAM):  # noqa: A002, ARG001
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (address, port, 0, 0)
             if ":" in address else (address, port))
            for address in addresses
        ]

    return resolve


@pytest.mark.parametrize(
    "url,address",
    [
        ("http://localhost.test", "127.0.0.1"),
        ("http://private.test", "10.0.0.1"),
        ("http://link-local.test", "169.254.169.254"),
        ("http://ipv6-loopback.test", "::1"),
        ("http://ula.test", "fd00::1"),
        ("http://unspecified.test", "0.0.0.0"),
    ],
)
def test_default_policy_rejects_non_public_targets(url: str, address: str) -> None:
    with pytest.raises(UnsafeNetworkTarget):
        validate_outbound_url(url, resolver=_resolver_for(address))


def test_default_policy_accepts_public_target() -> None:
    parsed = validate_outbound_url(
        "https://example.test/docs",
        resolver=_resolver_for("93.184.216.34"),
    )
    assert parsed.hostname == "example.test"


def test_mixed_dns_answer_fails_closed() -> None:
    with pytest.raises(UnsafeNetworkTarget, match="non-public"):
        validate_outbound_url(
            "https://rebinding.test",
            resolver=_resolver_for("93.184.216.34", "127.0.0.1"),
        )


def test_metadata_hostname_is_blocked_before_dns() -> None:
    def should_not_resolve(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise AssertionError("resolver must not be called")

    with pytest.raises(UnsafeNetworkTarget, match="metadata"):
        validate_outbound_url(
            "http://metadata.google.internal/computeMetadata/v1/",
            resolver=should_not_resolve,
        )


def test_userinfo_and_non_http_schemes_are_rejected() -> None:
    with pytest.raises(UnsafeNetworkTarget, match="user-info"):
        validate_outbound_url(
            "https://user:password@example.test/",
            resolver=_resolver_for("93.184.216.34"),
        )
    with pytest.raises(UnsafeNetworkTarget, match="scheme"):
        validate_outbound_url("file:///etc/passwd", resolver=_resolver_for("93.184.216.34"))


def test_exact_hostname_allowlist() -> None:
    policy = NetworkPolicy(allowed_hosts=("api.example.test",))
    validate_outbound_url(
        "https://api.example.test/v1",
        policy=policy,
        resolver=_resolver_for("93.184.216.34"),
    )
    with pytest.raises(UnsafeNetworkTarget, match="not allowed"):
        validate_outbound_url(
            "https://other.example.test/v1",
            policy=policy,
            resolver=_resolver_for("93.184.216.34"),
        )


def test_private_network_requires_explicit_policy() -> None:
    parsed = validate_outbound_url(
        "http://internal.test/health",
        policy=NetworkPolicy(allow_private=True),
        resolver=_resolver_for("10.0.0.5"),
    )
    assert parsed.hostname == "internal.test"


def test_bearer_header_validation() -> None:
    token = "a" * 40
    assert bearer_token_matches(f"Bearer {token}", token)
    assert bearer_token_matches(f"bearer {token}", token)
    assert not bearer_token_matches(token, token)
    assert not bearer_token_matches("Basic abc", token)
    assert not bearer_token_matches("Bearer wrong", token)
    assert not bearer_token_matches(None, token)


def test_service_token_is_stable_and_not_exposed(tmp_path: Path) -> None:
    first = load_or_create_service_token("scheduler", runtime_root=tmp_path, env={})
    second = load_or_create_service_token("scheduler", runtime_root=tmp_path, env={})
    assert first == second
    assert len(first) >= 32
    assert first not in token_fingerprint(first)
    assert (tmp_path / "auth" / "scheduler.token").read_text(encoding="utf-8").strip() == first


def test_explicit_service_token_takes_precedence(tmp_path: Path) -> None:
    supplied = "supplied-" + "x" * 40
    token = load_or_create_service_token(
        "browser",
        runtime_root=tmp_path,
        env={"AGENT_FORGE_BROWSER_TOKEN": supplied},
    )
    assert token == supplied
    assert not (tmp_path / "auth" / "browser.token").exists()


def test_short_explicit_service_token_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="32"):
        load_or_create_service_token(
            "browser",
            runtime_root=tmp_path,
            env={"AGENT_FORGE_BROWSER_TOKEN": "short"},
        )
