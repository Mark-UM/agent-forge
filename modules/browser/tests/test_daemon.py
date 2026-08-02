from pathlib import Path

import pytest

from modules.browser import daemon


def test_validate_url_accepts_http_and_https():
    assert daemon._validate_url("https://example.com/path") == "https://example.com/path"
    assert daemon._validate_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000"


@pytest.mark.parametrize(
    "value", ["", "example.com", "file:///etc/passwd", "https://user:pass@example.com"]
)
def test_validate_url_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        daemon._validate_url(value)


def test_screenshot_path_is_confined(monkeypatch, tmp_path):
    allowed = tmp_path / "allowed"
    monkeypatch.setattr(daemon, "_screenshot_allowed_dirs", lambda: (allowed.resolve(),))

    accepted = daemon._validate_screenshot_path(str(allowed / "nested" / "shot.png"))
    assert Path(accepted).parent.is_dir()

    with pytest.raises(ValueError, match="outside allowed"):
        daemon._validate_screenshot_path(str(tmp_path / "allowed-evil" / "shot.png"))


def test_screenshot_path_rejects_non_image(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_screenshot_allowed_dirs", lambda: (tmp_path.resolve(),))
    with pytest.raises(ValueError, match="must use"):
        daemon._validate_screenshot_path(str(tmp_path / "report.md"))


def test_browser_launch_defaults_to_local_runtime_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_FORGE_BROWSER_PROFILE", raising=False)
    monkeypatch.delenv("AGENT_FORGE_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.delenv("AGENT_FORGE_BROWSER_HEADLESS", raising=False)
    monkeypatch.setattr(daemon, "DEFAULT_PROFILE_DIR", tmp_path / "profile")
    profile, options = daemon.browser_launch_options()

    assert profile == (tmp_path / "profile").resolve()
    assert profile.is_dir()
    assert options == {"headless": False, "timeout": 30000}


def test_browser_engine_defaults_to_chromium_and_rejects_invalid(monkeypatch):
    monkeypatch.delenv("AGENT_FORGE_BROWSER_ENGINE", raising=False)
    assert daemon._browser_engine() == "chromium"

    monkeypatch.setenv("AGENT_FORGE_BROWSER_ENGINE", "netscape")
    with pytest.raises(ValueError, match="chromium, firefox, or webkit"):
        daemon._browser_engine()


def test_get_or_start_browser_reuses_open_page(monkeypatch):
    class Page:
        def is_closed(self):
            return False

    page = Page()
    monkeypatch.setattr(daemon, "_page", page)
    assert daemon.get_or_start_browser() is page
