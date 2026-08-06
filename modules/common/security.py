"""Security primitives shared by network clients and local daemons.

The repository intentionally uses only the Python standard library for these
boundaries.  The helpers in this module are fail-closed:

* outbound HTTP(S) targets are resolved before use and every resolved address
  must be globally routable;
* redirects are validated again before urllib follows them;
* URL user-info is rejected to avoid credential confusion and log leakage;
* loopback daemons use a random bearer token stored under ``_runtime`` unless
  an explicit token is supplied through the environment.

Callers that deliberately need private-network access must construct an
explicit ``NetworkPolicy(allow_private=True)``.  The default policy never makes
that exception implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import secrets
import socket
from typing import Callable, Iterable, Mapping, Sequence
import urllib.parse
import urllib.request


class UnsafeNetworkTarget(ValueError):
    """Raised when a URL resolves to a disallowed network target."""


@dataclass(frozen=True)
class NetworkPolicy:
    """Rules applied to outbound HTTP clients.

    ``allowed_hosts`` is an optional exact hostname allow-list.  It is useful
    for provider adapters that are expected to contact a single API.  Private,
    loopback, link-local, multicast, unspecified and reserved addresses remain
    blocked unless ``allow_private`` is explicitly enabled.
    """

    allowed_schemes: tuple[str, ...] = ("http", "https")
    allowed_hosts: tuple[str, ...] = ()
    allow_private: bool = False
    resolve_dns: bool = True


DEFAULT_NETWORK_POLICY = NetworkPolicy()

# Common cloud metadata endpoints.  Most are also rejected by the IP checks,
# but keeping explicit names makes the policy resilient to local DNS aliases.
_BLOCKED_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.google.com",
        "instance-data",
        "instance-data.ec2.internal",
    }
)

Resolver = Callable[..., Sequence[tuple]]


def _normalise_hostname(hostname: str) -> str:
    host = hostname.rstrip(".").lower()
    if not host:
        raise UnsafeNetworkTarget("URL hostname is required")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeNetworkTarget("URL hostname is not valid IDNA") from exc


def _is_disallowed_ip(address: ipaddress._BaseAddress) -> bool:
    # IPv4-mapped IPv6 addresses must be evaluated as IPv4 as well.
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
        )
    )


def _resolved_addresses(
    hostname: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> tuple[ipaddress._BaseAddress, ...]:
    try:
        records = resolver(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeNetworkTarget(f"Unable to resolve host: {hostname}") from exc

    addresses: list[ipaddress._BaseAddress] = []
    for record in records:
        sockaddr = record[4]
        if not sockaddr:
            continue
        raw = sockaddr[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise UnsafeNetworkTarget(
                f"Resolver returned an invalid address for {hostname}"
            ) from exc
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise UnsafeNetworkTarget(f"Host resolved to no addresses: {hostname}")
    return tuple(addresses)


def validate_outbound_url(
    url: str,
    *,
    policy: NetworkPolicy = DEFAULT_NETWORK_POLICY,
    resolver: Resolver = socket.getaddrinfo,
) -> urllib.parse.ParseResult:
    """Validate an outbound URL and return its parsed representation.

    Validation is intentionally performed immediately before opening a
    connection.  A redirect handler must call the same function for every new
    target, otherwise a public URL could redirect to a private address.
    """

    if not isinstance(url, str) or not url.strip():
        raise UnsafeNetworkTarget("URL is required")

    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeNetworkTarget("URL contains an invalid port") from exc

    scheme = parsed.scheme.lower()
    if scheme not in policy.allowed_schemes:
        raise UnsafeNetworkTarget(f"Unsupported URL scheme: {scheme or '<missing>'}")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeNetworkTarget("URL user-info is not allowed")
    if not parsed.hostname:
        raise UnsafeNetworkTarget("URL hostname is required")

    hostname = _normalise_hostname(parsed.hostname)
    if hostname in _BLOCKED_HOSTS:
        raise UnsafeNetworkTarget(f"Blocked metadata hostname: {hostname}")
    if policy.allowed_hosts and hostname not in {
        _normalise_hostname(item) for item in policy.allowed_hosts
    }:
        raise UnsafeNetworkTarget(f"Host is not allowed by policy: {hostname}")

    if not policy.resolve_dns:
        return parsed

    effective_port = port or (443 if scheme == "https" else 80)
    addresses = _resolved_addresses(hostname, effective_port, resolver=resolver)
    if not policy.allow_private:
        blocked = [str(address) for address in addresses if _is_disallowed_ip(address)]
        if blocked:
            raise UnsafeNetworkTarget(
                f"Host resolves to a non-public address: {hostname} ({', '.join(blocked)})"
            )
    return parsed


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib redirect handler that revalidates each destination."""

    def __init__(self, policy: NetworkPolicy = DEFAULT_NETWORK_POLICY):
        super().__init__()
        self._policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        absolute = urllib.parse.urljoin(req.full_url, newurl)
        validate_outbound_url(absolute, policy=self._policy)
        return super().redirect_request(req, fp, code, msg, headers, absolute)


def build_safe_url_opener(
    policy: NetworkPolicy = DEFAULT_NETWORK_POLICY,
) -> urllib.request.OpenerDirector:
    """Build an urllib opener with fail-closed redirect validation."""

    return urllib.request.build_opener(SafeRedirectHandler(policy))


def service_token_path(runtime_root: os.PathLike[str] | str, service: str) -> Path:
    safe_service = "".join(ch for ch in service.lower() if ch.isalnum() or ch in "-_")
    if not safe_service:
        raise ValueError("service name must contain an alphanumeric character")
    return Path(runtime_root) / "auth" / f"{safe_service}.token"


def load_or_create_service_token(
    service: str,
    *,
    runtime_root: os.PathLike[str] | str = "_runtime",
    env: Mapping[str, str] | None = None,
) -> str:
    """Return a daemon bearer token without ever printing it.

    ``AGENT_FORGE_<SERVICE>_TOKEN`` overrides the on-disk token.  Otherwise a
    256-bit random token is atomically persisted with best-effort owner-only
    permissions.  Existing empty or suspiciously short token files are
    rejected rather than silently accepted.
    """

    environment = os.environ if env is None else env
    env_name = f"AGENT_FORGE_{service.upper().replace('-', '_')}_TOKEN"
    supplied = environment.get(env_name, "").strip()
    if supplied:
        if len(supplied) < 32:
            raise ValueError(f"{env_name} must contain at least 32 characters")
        return supplied

    path = service_token_path(runtime_root, service)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError(f"Daemon token file is invalid: {path}")
        return token

    token = secrets.token_urlsafe(32)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows ACLs do not map directly to chmod.  The file still lives
            # under the user's private runtime directory.
            pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return token


def bearer_token_matches(authorization: str | None, expected_token: str) -> bool:
    """Validate an HTTP Bearer header using constant-time comparison."""

    if not expected_token or not authorization:
        return False
    scheme, separator, candidate = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not candidate:
        return False
    return hmac.compare_digest(candidate.strip(), expected_token)


def token_fingerprint(token: str) -> str:
    """Return a short non-secret fingerprint suitable for diagnostics."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "DEFAULT_NETWORK_POLICY",
    "NetworkPolicy",
    "SafeRedirectHandler",
    "UnsafeNetworkTarget",
    "bearer_token_matches",
    "build_safe_url_opener",
    "load_or_create_service_token",
    "service_token_path",
    "token_fingerprint",
    "validate_outbound_url",
]
