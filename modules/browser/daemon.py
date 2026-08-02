#!/usr/bin/env python3
"""Loopback-only Playwright browser daemon used by the OpenCode tools.

The daemon owns a Chromium process and exposes a deliberately small HTTP API on
127.0.0.1. Cookies and local storage survive clean shutdowns via a project-local
storage-state file; browser state and screenshots remain local and ignored.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from modules.bootstrap.dependencies import activate_vendor_path

activate_vendor_path()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "_runtime" / "browser"
DEFAULT_PROFILE_DIR = RUNTIME_DIR / "profile"
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(os.environ.get("AGENT_FORGE_BROWSER_PORT", "9223"))
URL_SCHEME_WHITELIST = {"http", "https"}
MAX_BODY_BYTES = 1_000_000
MAX_JS_CODE_LENGTH = 100_000
BROWSER_ENDPOINTS = frozenset(
    {
        "/navigate",
        "/click",
        "/type",
        "/keys",
        "/screenshot",
        "/content",
        "/text",
        "/js",
        "/title",
        "/url",
        "/back",
        "/forward",
        "/refresh",
        "/wait",
        "/scroll",
        "/newtab",
    }
)

_playwright = None
_browser = None
_context = None
_page = None


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


def _browser_engine() -> str:
    engine = os.environ.get("AGENT_FORGE_BROWSER_ENGINE", "chromium").strip().lower()
    if engine not in {"chromium", "firefox", "webkit"}:
        raise ValueError("AGENT_FORGE_BROWSER_ENGINE must be chromium, firefox, or webkit")
    return engine


def browser_launch_options() -> tuple[Path, dict]:
    """Return the state directory and bounded Playwright launch options."""
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
    # Playwright's bundled browser is the supported default. A system browser
    # build is accepted only as an explicit operator override.
    executable = os.environ.get("AGENT_FORGE_BROWSER_EXECUTABLE", "").strip()
    if executable:
        executable_path = Path(executable).expanduser().resolve()
        if not executable_path.is_file():
            raise FileNotFoundError(
                f"Browser executable not found: {executable_path}"
            )
        options["executable_path"] = str(executable_path)
    return profile, options


def _page_is_usable(page) -> bool:
    if page is None:
        return False
    try:
        return not page.is_closed()
    except Exception:
        return False


def get_or_start_browser():
    """Start the configured browser with restorable state or return its page."""
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
    context_options = {"storage_state": str(storage_state)} if storage_state.is_file() else {}
    _context = _browser.new_context(**context_options)
    _page = _context.pages[0] if _context.pages else _context.new_page()
    if not options["headless"]:
        _page.bring_to_front()
    return _page


def close_browser() -> None:
    """Persist web state and close all browser resources."""
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


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must not be empty")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in URL_SCHEME_WHITELIST:
        raise ValueError("URL scheme must be http or https")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    return url.strip()


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
    """Resolve a screenshot path and enforce directory-boundary checks."""
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
        self.end_headers()
        self.wfile.write(payload)

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
                    }
                )
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            return
        self._send_json({"error": "unknown endpoint"}, 404)

    def do_POST(self):
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
                url = _validate_url(body.get("url", ""))
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
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
                result = {"ok": True, "result": page.evaluate(_validate_js_code(body.get("code")))}
            elif path == "/title":
                result = {"ok": True, "title": page.title()}
            elif path == "/url":
                result = {"ok": True, "url": page.url}
            elif path == "/back":
                page.go_back()
                result = {"ok": True}
            elif path == "/forward":
                page.go_forward()
                result = {"ok": True}
            elif path == "/refresh":
                page.reload(wait_until="domcontentloaded")
                result = {"ok": True}
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
        except (TypeError, ValueError) as exc:
            self._send_json({"ok": False, "error": f"input validation failed: {exc}"}, 400)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, 500)


def _find_existing_daemon() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{DAEMON_HOST}:{DAEMON_PORT}/ping", timeout=1
        ) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("pong"))
    except Exception:
        return False


def _send_http(method: str, path: str, data: dict | None = None) -> dict:
    url = f"http://{DAEMON_HOST}:{DAEMON_PORT}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(data or {}).encode("utf-8") if method == "POST" else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    if _find_existing_daemon():
        print("An existing browser daemon is running; requesting shutdown...")
        _send_http("POST", "/close")
        time.sleep(1)

    get_or_start_browser()
    server = HTTPServer((DAEMON_HOST, DAEMON_PORT), Handler)
    server.running = True
    server.timeout = 0.5
    print(f"Browser daemon ready: http://{DAEMON_HOST}:{DAEMON_PORT}")
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
