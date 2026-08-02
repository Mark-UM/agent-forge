#!/usr/bin/env python3
"""浏览器控制守护进程 — Playwright Firefox 持久化 HTTP API。

Features:
- 保留 Firefox 登录态（持久化 profile）
- 端口 9223，HTTP API
- 输入验证 + 路径白名单防 RCE
"""
import sys
import os
import json
import time
import tempfile
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── 配置 ────────────────────────────────────────────────────────
FIREFOX_EXE = r"C:\Program Files\Mozilla Firefox\firefox.exe"
FIREFOX_PROFILE = os.path.expandvars(
    r"%APPDATA%\Mozilla\Firefox\Profiles\cqe4w54g.default-release"
)
DAEMON_PORT = 9223
MODULE_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 截图保存白名单目录（防止任意路径写入）
SCREENSHOT_ALLOWED_DIRS = [
    MODULE_DIR,
    PROJECT_ROOT,
    os.path.join(os.path.expanduser("~"), "Pictures"),
    os.path.join(os.path.expanduser("~"), "Desktop"),
    tempfile.gettempdir(),
]

# URL scheme 白名单（防止 file:// 等敏感协议）
URL_SCHEME_WHITELIST = {"http", "https"}

# JS 代码长度限制（防止超大 payload）
MAX_JS_CODE_LENGTH = 100_000

# 全局状态
_playwright = None
_browser = None
_page = None


def get_or_start_browser():
    global _playwright, _browser, _page
    if _browser is None or not _browser.is_connected():
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.firefox.launch_persistent_context(
            FIREFOX_PROFILE,
            headless=False,
            executable_path=FIREFOX_EXE,
            args=["--remote-debugging-port=0"],
        )
        if _browser.pages:
            _page = _browser.pages[0]
        else:
            _page = _browser.new_page()
        _page.bring_to_front()
    return _page


def _validate_url(url):
    """验证 URL scheme 是否在白名单内。"""
    if not isinstance(url, str) or not url:
        raise ValueError("URL 不能为空")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in URL_SCHEME_WHITELIST:
        raise ValueError(f"不允许的 URL scheme: {parsed.scheme}（仅允许 {URL_SCHEME_WHITELIST}）")
    return url


def _validate_screenshot_path(filepath):
    """验证截图保存路径是否在白名单目录内。

    B3 fix: 原实现用 str.startswith 检查路径前缀，存在 path traversal
    风险（如允许 `MODULE_DIR` 时，`MODULE_DIR_evil/x.png` 也会通过）。
    改用 os.path.realpath + os.path.commonpath 做严格的目录归属检查。
    commonpath 是规范推荐的路径包含判断方法，避免前缀匹配的歧义。
    """
    if not filepath:
        return os.path.join(MODULE_DIR, "screenshot.png")
    # realpath 解析符号链接、`.`、`..` 等相对引用
    abs_path = os.path.realpath(filepath)
    for allowed in SCREENSHOT_ALLOWED_DIRS:
        allowed_real = os.path.realpath(allowed)
        # 必须严格等于白名单目录，或是其子路径
        if abs_path == allowed_real:
            return abs_path
        # 用 os.path.commonpath 做严格目录归属判断
        # commonpath([a, b]) == allowed_real 意味着 allowed_real 是 abs_path 的祖先目录
        try:
            common = os.path.commonpath([abs_path, allowed_real])
            if common == allowed_real:
                return abs_path
        except ValueError:
            # 跨盘符 (Windows) 或不同根目录 — 一定不在白名单内
            continue
    raise ValueError(f"截图路径不在允许的目录内: {abs_path}")


def _validate_selector(selector):
    """基本验证 CSS 选择器。"""
    if not isinstance(selector, str) or not selector:
        raise ValueError("选择器不能为空")
    if len(selector) > 1000:
        raise ValueError("选择器过长")
    return selector


def _validate_js_code(code):
    """验证 JS 代码长度。"""
    if not isinstance(code, str) or not code:
        raise ValueError("JS 代码不能为空")
    if len(code) > MAX_JS_CODE_LENGTH:
        raise ValueError(f"JS 代码过长（>{MAX_JS_CODE_LENGTH} 字符）")
    return code


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0 or length > 1_000_000:  # 1MB 限制
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("无效的 JSON body")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            try:
                self._send_json({
                    "ok": True,
                    "url": _page.url if _page else "",
                    "title": _page.title() if _page else "",
                })
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
        elif path == "/ping":
            self._send_json({"pong": True})
        else:
            self._send_json({"error": "unknown"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            page = get_or_start_browser()

            if path == "/navigate":
                url = _validate_url(body.get("url", ""))
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                self._send_json({"ok": True, "url": page.url, "title": page.title()})

            elif path == "/click":
                selector = _validate_selector(body.get("selector"))
                page.click(selector, timeout=10000)
                self._send_json({"ok": True, "clicked": selector})

            elif path == "/type":
                selector = _validate_selector(body.get("selector"))
                text = body.get("text", "")
                if not isinstance(text, str) or len(text) > 100_000:
                    raise ValueError("文本无效或过长")
                page.fill(selector, text, timeout=10000)
                self._send_json({"ok": True, "typed": len(text)})

            elif path == "/keys":
                key = body.get("key", "")
                if not isinstance(key, str) or len(key) > 100:
                    raise ValueError("按键无效")
                page.keyboard.press(key)
                self._send_json({"ok": True, "key": key})

            elif path == "/screenshot":
                filepath = _validate_screenshot_path(body.get("path"))
                page.screenshot(path=filepath, full_page=True)
                self._send_json({"ok": True, "path": filepath})

            elif path == "/content":
                self._send_json({"ok": True, "content": page.content()})

            elif path == "/text":
                self._send_json({"ok": True, "text": page.inner_text("body")})

            elif path == "/js":
                code = _validate_js_code(body.get("code"))
                result = page.evaluate(code)
                self._send_json({"ok": True, "result": result})

            elif path == "/title":
                self._send_json({"ok": True, "title": page.title()})

            elif path == "/url":
                self._send_json({"ok": True, "url": page.url})

            elif path == "/back":
                page.go_back()
                self._send_json({"ok": True})

            elif path == "/forward":
                page.go_forward()
                self._send_json({"ok": True})

            elif path == "/refresh":
                page.reload(wait_until="domcontentloaded")
                self._send_json({"ok": True})

            elif path == "/wait":
                selector = _validate_selector(body.get("selector"))
                timeout = min(int(body.get("timeout", 30000)), 60000)
                page.wait_for_selector(selector, timeout=timeout)
                self._send_json({"ok": True, "found": selector})

            elif path == "/scroll":
                x = int(body.get("x", 0))
                y = int(body.get("y", 0))
                if abs(x) > 1_000_000 or abs(y) > 1_000_000:
                    raise ValueError("滚动坐标超出范围")
                page.evaluate(f"window.scrollTo({int(x)}, {int(y)})")
                self._send_json({"ok": True, "scrolled": [x, y]})

            elif path == "/newtab":
                new_page = _browser.new_page()
                self._send_json({"ok": True, "url": new_page.url})

            elif path == "/close":
                self._send_json({"ok": True, "msg": "daemon shutting down"})
                self.server.running = False

            else:
                self._send_json({"error": f"unknown endpoint: {path}"}, 404)

        except ValueError as e:
            self._send_json({"ok": False, "error": f"输入验证失败: {e}"}, 400)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)


def _find_existing_daemon():
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{DAEMON_PORT}/ping")
        with urllib.request.urlopen(req, timeout=1) as resp:
            if json.loads(resp.read().decode('utf-8')).get("pong"):
                return True
    except Exception:
        pass
    return False


def _send_http(method, path, data=None):
    url = f"http://127.0.0.1:{DAEMON_PORT}{path}"
    if method == "POST":
        req = urllib.request.Request(
            url,
            data=json.dumps(data or {}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def main():
    if _find_existing_daemon():
        print("Daemon 已在运行，发送 stop...")
        _send_http("POST", "/close")
        time.sleep(1)

    print("启动 Firefox + Daemon...")
    get_or_start_browser()

    server = HTTPServer(("127.0.0.1", DAEMON_PORT), Handler)
    server.running = True
    print(f"Daemon 就绪: http://127.0.0.1:{DAEMON_PORT}")

    try:
        while server.running:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        print("关闭...")
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()


if __name__ == "__main__":
    main()
