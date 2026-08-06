#!/usr/bin/env python3
"""Authenticated, loopback-only Playwright browser daemon.

URL validation has two explicit layers:

* ``_validate_url`` preserves the historical credential-free HTTP(S) syntax
  contract used by library callers;
* ``_validate_public_url`` additionally applies the fail-closed DNS/IP policy.

All real navigation and every Playwright subresource request use the public
network boundary. Service workers and WebSockets are blocked by default, and
all HTTP API endpoints require a bearer token.
"""
from __future__ import annotations

from collections import deque
import json
import os
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from modules.bootstrap.dependencies import activate_vendor_path
from modules.common.security import (
    UnsafeNetworkTarget,
    bearer_token_matches,
    load_or_create_service_token,
    token_fingerprint,
    validate_outbound_url,
)

activate_vendor_path()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "_runtime" / "browser"
DEFAULT_PROFILE_DIR = RUNTIME_DIR / "profile"
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(os.environ.get("AGENT_FORGE_BROWSER_PORT", "9223"))
MAX_BODY_BYTES = 1_000_000
MAX_JS_CODE_LENGTH = 100_000
SAFE_LOCAL_RESOURCE_SCHEMES = frozenset({"about", "blob", "data"})
BROWSER_ENDPOINTS = frozenset(
    {
        "/navigate", "/click", "/type", "/keys", "/screenshot",
        "/content", "/text", "/js", "/title", "/url", "/back",
        "/forward", "/refresh", "/wait", "/scroll", "/newtab",
    }
)

_playwright = None
_browser = None
_context = None
_page = None
_service_token: str | None = None
_blocked_requests: deque[dict] = deque(maxlen=100)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _get_service_token() -> str:
    global _service_token
    if _service_token is None:
        _service_token = load_or_create_service_token(
            "browser", runtime_root=PROJECT_ROOT / "_runtime"
        )
    return _service_token


def _browser_engine() -> str:
    engine = os.environ.get("AGENT_FORGE_BROWSER_ENGINE", "chromium").strip().lower()
    if engine not in {"chromium", "firefox", "webkit"}:
        raise ValueError("AGENT_FORGE_BROWSER_ENGINE must be chromium, firefox, or webkit")
    return engine


def browser_launch_options() -> tuple[Path, dict]:
    profile = Path(
        os.environ.get("AGENT_FORGE_BROWSER_PROFILE", str(DEFAULT_PROFILE_DIR))
    ).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)
    try:
        timeout = int(os.environ.get("AGENT_FORGE_BROWSER_TIMEOUT_MS", "30000"))
    except ValueError as exc:
        raise ValueError("AGENT_FORGE_BROWSER_TIMEOUT_MS must be an integer") from exc
    if not 1_000 <= timeout <= 120_000:
        raise ValueError("AGENT_FORGE_BROWSER_TIMEOUT_MS must be between 1000 and 120000")
    options: dict = {
        "headless": _env_bool("AGENT_FORGE_BROWSER_HEADLESS", False),
        "timeout": timeout,
    }
    executable = os.environ.get("AGENT_FORGE_BROWSER_EXECUTABLE", "").strip()
    if executable:
        executable_path = Path(executable).expanduser().resolve()
        if not executable_path.is_file():
            raise FileNotFoundError(f"Browser executable not found: {executable_path}")
        options["executable_path"] = str(executable_path)
    return profile, options


def _page_is_usable(page) -> bool:
    if page is None:
        return False
    try:
        return not page.is_closed()
    except Exception:
        return False


def _validate_url(url: str) -> str:
    """Validate only credential-free absolute HTTP(S) syntax.

    This intentionally accepts literal loopback hosts for backward-compatible
    parsing. Real browser traffic must call ``_validate_public_url`` or pass
    through ``_guard_network_route`` before a connection is made.
    """

    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must not be empty")
    candidate = url.strip()
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL scheme must be http or https")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed")
    return candidate


def _validate_public_url(url: str) -> str:
    candidate = _validate_url(url)
    validate_outbound_url(candidate)
    return candidate


def _record_blocked_request(url: str, reason: str) -> None:
    parsed = urlparse(url)
    safe_target = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        safe_target += f":{parsed.port}"
    _blocked_requests.append(
        {
            "target": safe_target,
            "reason": reason[:500],
            "blocked_at": time.time(),
        }
    )


def _guard_network_route(route, request) -> None:  # noqa: ANN001
    url = request.url
    parsed = urlparse(url)
    if parsed.scheme.lower() in SAFE_LOCAL_RESOURCE_SCHEMES:
        route.continue_()
        return
    try:
        _validate_public_url(url)
    except (UnsafeNetworkTarget, ValueError) as exc:
        _record_blocked_request(url, str(exc))
        route.abort("blockedbyclient")
        return
    route.continue_()


def _install_browser_network_guards(context) -> None:  # noqa: ANN001
    context.route("**/*", _guard_network_route)
    if not _env_bool("AGENT_FORGE_BROWSER_ALLOW_WEBSOCKETS", False):
        context.add_init_script(
            """
            (() => {
              const deny = () => { throw new DOMException('WebSockets disabled by Agent Forge policy', 'SecurityError'); };
              Object.defineProperty(globalThis, 'WebSocket', {value: deny, configurable: false, writable: false});
            })();
            """
        )


def get_or_start_browser():
    global _playwright, _browser, _context, _page
    if _page_is_usable(_page):
        return _page

    from playwright.sync_api import sync_playwright

    state_dir, options = browser_launch_options()
    if _playwright is None:
        _playwright = sync_playwright().start()
    browser_type = getattr(_playwright, _browser_engine())
    _browser = browser_type.launch(**options)
    storage_state = state_dir / "storage-state.json"
    context_options: dict = {"service_workers": "block"}
    if storage_state.is_file():
        context_options["storage_state"] = str(storage_state)
    _context = _browser.new_context(**context_options)
    _context.set_default_timeout(options["timeout"])
    _install_browser_network_guards(_context)
    _page = _context.pages[0] if _context.pages else _context.new_page()
    if not options["headless"]:
        _page.bring_to_front()
    return _page


def close_browser() -> None:
    global _playwright, _browser, _context, _page
    if _context is not None:
        try:
            state_dir, _ = browser_launch_options()
            state = _context.storage_state()
            temporary = state_dir / "storage-state.json.tmp"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            temporary.replace(state_dir / "storage-state.json")
        except Exception:
            pass
        try:
            _context.close()
        except Exception:
            pass
    if _browser is not None:
        try:
            _browser.close()
        except Exception:
            pass
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
    _page = None
    _context = None
    _browser = None
    _playwright = None


def _screenshot_allowed_dirs() -> tuple[Path, ...]:
    directories = (
        MODULE_DIR,
        PROJECT_ROOT / "_runtime",
        RUNTIME_DIR / "screenshots",
        Path(tempfile.gettempdir()),
        Path.home() / "Pictures",
        Path.home() / "Desktop",
    )
    return tuple(path.resolve() for path in directories)


def _validate_screenshot_path(filepath: str | None) -> str:
    candidate = Path(filepath) if filepath else RUNTIME_DIR / "screenshots" / "latest.png"
    candidate = candidate.expanduser().resolve()
    if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Screenshot path must use .png, .jpg, or .jpeg")
    for allowed in _screenshot_allowed_dirs():
        try:
            candidate.relative_to(allowed)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except ValueError:
            continue
    raise ValueError(f"Screenshot path is outside allowed directories: {candidate}")


def _validate_selector(selector: str) -> str:
    if not isinstance(selector, str) or not selector:
        raise ValueError("Selector must not be empty")
    if len(selector) > 1_000:
        raise ValueError("Selector is too long")
    return selector


def _validate_js_code(code: str) -> str:
    if not _env_bool("AGENT_FORGE_BROWSER_ALLOW_JS", False):
        raise PermissionError(
            "Arbitrary JavaScript is disabled; set AGENT_FORGE_BROWSER_ALLOW_JS=1 "
            "only for a trusted local session"
        )
    if not isinstance(code, str) or not code:
        raise ValueError("JavaScript code must not be empty")
    if len(code) > MAX_JS_CODE_LENGTH:
        raise ValueError(f"JavaScript code exceeds {MAX_JS_CODE_LENGTH} characters")
    return code


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def _send_json(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _require_auth(self) -> bool:
        if bearer_token_matches(self.headers.get("Authorization"), _get_service_token()):
            return True
        payload = b'{"ok":false,"error":"authentication required"}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("WWW-Authenticate", 'Bearer realm="agent-forge-browser"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return False

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"Request body exceeds {MAX_BODY_BYTES} bytes")
        if length == 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def do_GET(self):
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        if path == "/ping":
            self._send_json({"pong": True})
            return
        if path == "/status":
            page = _page if _page_is_usable(_page) else None
            try:
                self._send_json(
                    {
                        "ok": True,
                        "browser_started": page is not None,
                        "url": page.url if page else "",
                        "title": page.title() if page else "",
                        "auth_required": True,
                        "token_fingerprint": token_fingerprint(_get_service_token()),
                        "javascript_enabled": _env_bool("AGENT_FORGE_BROWSER_ALLOW_JS", False),
                        "websockets_enabled": _env_bool("AGENT_FORGE_BROWSER_ALLOW_WEBSOCKETS", False),
                        "blocked_request_count": len(_blocked_requests),
                        "recent_blocked_requests": list(_blocked_requests)[-10:],
                    }
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        self._send_json({"error": "unknown endpoint"}, 404)

    def do_POST(self):
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/close":
                self._send_json({"ok": True, "message": "daemon shutting down"})
                self.server.running = False
                return
            if path not in BROWSER_ENDPOINTS:
                self._send_json({"error": f"unknown endpoint: {path}"}, 404)
                return

            page = get_or_start_browser()
            if path == "/navigate":
                target = _validate_public_url(body.get("url", ""))
                page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                _validate_public_url(page.url)
                result = {"ok": True, "url": page.url, "title": page.title()}
            elif path == "/click":
                selector = _validate_selector(body.get("selector"))
                page.click(selector, timeout=10_000)
                result = {"ok": True, "clicked": selector}
            elif path == "/type":
                selector = _validate_selector(body.get("selector"))
                text = body.get("text", "")
                if not isinstance(text, str) or len(text) > 100_000:
                    raise ValueError("Text must be a string of at most 100000 characters")
                page.fill(selector, text, timeout=10_000)
                result = {"ok": True, "typed": len(text)}
            elif path == "/keys":
                key = body.get("key", "")
                if not isinstance(key, str) or not key or len(key) > 100:
                    raise ValueError("Key must be a non-empty string of at most 100 characters")
                page.keyboard.press(key)
                result = {"ok": True, "key": key}
            elif path == "/screenshot":
                filepath = _validate_screenshot_path(body.get("path"))
                page.screenshot(path=filepath, full_page=True)
                result = {"ok": True, "path": filepath}
            elif path == "/content":
                result = {"ok": True, "content": page.content()}
            elif path == "/text":
                result = {"ok": True, "text": page.inner_text("body")}
            elif path == "/js":
                try:
                    code = _validate_js_code(body.get("code"))
                except PermissionError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, 403)
                    return
                result = {"ok": True, "result": page.evaluate(code)}
            elif path == "/title":
                result = {"ok": True, "title": page.title()}
            elif path == "/url":
                result = {"ok": True, "url": page.url}
            elif path == "/back":
                page.go_back(wait_until="domcontentloaded")
                if page.url and page.url != "about:blank":
                    _validate_public_url(page.url)
                result = {"ok": True, "url": page.url}
            elif path == "/forward":
                page.go_forward(wait_until="domcontentloaded")
                if page.url and page.url != "about:blank":
                    _validate_public_url(page.url)
                result = {"ok": True, "url": page.url}
            elif path == "/refresh":
                page.reload(wait_until="domcontentloaded")
                if page.url and page.url != "about:blank":
                    _validate_public_url(page.url)
                result = {"ok": True, "url": page.url}
            elif path == "/wait":
                selector = _validate_selector(body.get("selector"))
                timeout = int(body.get("timeout", 30_000))
                if not 0 <= timeout <= 60_000:
                    raise ValueError("Timeout must be between 0 and 60000 milliseconds")
                page.wait_for_selector(selector, timeout=timeout)
                result = {"ok": True, "found": selector}
            elif path == "/scroll":
                x, y = int(body.get("x", 0)), int(body.get("y", 0))
                if abs(x) > 1_000_000 or abs(y) > 1_000_000:
                    raise ValueError("Scroll coordinates exceed the allowed range")
                page.evaluate("([x, y]) => window.scrollTo(x, y)", [x, y])
                result = {"ok": True, "scrolled": [x, y]}
            elif path == "/newtab":
                global _page
                _page = _context.new_page()
                result = {"ok": True, "url": _page.url}
            self._send_json(result)
        except (TypeError, ValueError, UnsafeNetworkTarget) as exc:
            self._send_json(
                {"ok": False, "error": f"input or network policy failed: {exc}"},
                400,
            )
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)


def _authorization_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_service_token()}",
        "Content-Type": "application/json",
    }


def _find_existing_daemon() -> bool:
    try:
        request = urllib.request.Request(
            f"http://{DAEMON_HOST}:{DAEMON_PORT}/ping",
            headers=_authorization_headers(),
        )
        with urllib.request.urlopen(request, timeout=1) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("pong"))
    except Exception:
        return False


def _send_http(method: str, path: str, data: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"http://{DAEMON_HOST}:{DAEMON_PORT}{path}",
        data=json.dumps(data or {}).encode("utf-8") if method == "POST" else None,
        headers=_authorization_headers(),
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    token = _get_service_token()
    if _find_existing_daemon():
        print("An authenticated browser daemon is running; requesting shutdown...")
        _send_http("POST", "/close")
        time.sleep(1)

    get_or_start_browser()
    server = HTTPServer((DAEMON_HOST, DAEMON_PORT), Handler)
    server.running = True
    server.timeout = 0.5
    print(f"Browser daemon ready: http://{DAEMON_HOST}:{DAEMON_PORT}")
    print(f"Authentication required; token fingerprint: {token_fingerprint(token)}")
    try:
        while server.running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        close_browser()


if __name__ == "__main__":
    main()
