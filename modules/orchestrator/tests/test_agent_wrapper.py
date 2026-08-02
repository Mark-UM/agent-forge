"""Tests for modules/orchestrator/agent_wrapper.py — v1.8 Phase B1."""
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def temp_output_path(tmp_path):
    """Provide a temp output path for reports."""
    return str(tmp_path / "report.md")


# ── _check_browser_use tests ───────────────────────────────────


def test_check_browser_use_returns_bool():
    """_check_browser_use should return a boolean."""
    from modules.orchestrator import agent_wrapper

    # Reset cache
    agent_wrapper._BROWSER_USE_AVAILABLE = None
    result = agent_wrapper._check_browser_use()
    assert isinstance(result, bool)


def test_check_browser_use_caches():
    """_check_browser_use should cache result."""
    from modules.orchestrator import agent_wrapper

    agent_wrapper._BROWSER_USE_AVAILABLE = True
    assert agent_wrapper._check_browser_use() is True

    agent_wrapper._BROWSER_USE_AVAILABLE = False
    assert agent_wrapper._check_browser_use() is False


# ── _call_browser_daemon tests ─────────────────────────────────


def test_call_browser_daemon_unreachable():
    """_call_browser_daemon should return error dict when daemon unreachable."""
    from modules.orchestrator.agent_wrapper import _call_browser_daemon

    result = _call_browser_daemon("/status")
    assert "error" in result
    assert "unreachable" in result["error"].lower() or "connection" in result["error"].lower()


def test_call_browser_daemon_with_mock():
    """_call_browser_daemon should parse JSON response."""
    from modules.orchestrator import agent_wrapper
    import urllib.request

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"status": "ok"}).encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = agent_wrapper._call_browser_daemon("/status")
        assert result == {"status": "ok"}


# ── _recognize_screenshot tests ────────────────────────────────


def test_recognize_screenshot_success():
    """_recognize_screenshot should return recognized text via subprocess."""
    from modules.orchestrator import agent_wrapper
    import subprocess

    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = "Recognized text content"
    mock_completed.stderr = ""

    with patch("subprocess.run", return_value=mock_completed):
        result = agent_wrapper._recognize_screenshot("/fake/path.png")
        assert result["text"] == "Recognized text content"
        assert result["success"] is True


def test_recognize_screenshot_failure():
    """_recognize_screenshot should handle CLI failure gracefully."""
    from modules.orchestrator import agent_wrapper
    import subprocess

    mock_completed = MagicMock()
    mock_completed.returncode = 1
    mock_completed.stdout = ""
    mock_completed.stderr = "API key not set"

    with patch("subprocess.run", return_value=mock_completed):
        result = agent_wrapper._recognize_screenshot("/fake/path.png")
        assert result["success"] is False
        assert "API key not set" in result["error"]


def test_recognize_screenshot_timeout():
    """_recognize_screenshot should handle timeout."""
    from modules.orchestrator import agent_wrapper
    import subprocess

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 120)):
        result = agent_wrapper._recognize_screenshot("/fake/path.png")
        assert result["success"] is False
        assert "timeout" in result["error"].lower()


# ── _summarize_content tests ───────────────────────────────────


def test_summarize_content_with_aggregator():
    """_summarize_content should use aggregator when available."""
    from modules.orchestrator import agent_wrapper

    with patch("modules.search.aggregator.aggregate_results",
               return_value={"markdown": "# Summary\n\nContent"}):
        result = agent_wrapper._summarize_content("test content", query="test")
        assert "# Summary" in result


def test_summarize_content_fallback():
    """_summarize_content should fallback when aggregator unavailable."""
    from modules.orchestrator import agent_wrapper

    with patch.dict("sys.modules", {"modules.search.aggregator": None}):
        result = agent_wrapper._summarize_content("test content", query="test")
        assert "test content" in result
        assert "Query" in result


# ── _atomic_write tests ────────────────────────────────────────


def test_atomic_write_creates_file(tmp_path):
    """_atomic_write should create the file with correct content."""
    from modules.orchestrator.agent_wrapper import _atomic_write

    filepath = str(tmp_path / "test.md")
    _atomic_write(filepath, "Hello World")

    assert os.path.exists(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        assert f.read() == "Hello World"


def test_atomic_write_no_tmp_files(tmp_path):
    """_atomic_write should not leave .tmp files."""
    from modules.orchestrator.agent_wrapper import _atomic_write

    filepath = str(tmp_path / "test.md")
    _atomic_write(filepath, "content")

    tmp_files = list(Path(tmp_path).glob("*.tmp"))
    assert len(tmp_files) == 0


def test_atomic_write_creates_parent_dir(tmp_path):
    """_atomic_write should create parent directories."""
    from modules.orchestrator.agent_wrapper import _atomic_write

    filepath = str(tmp_path / "subdir" / "test.md")
    _atomic_write(filepath, "content")

    assert os.path.exists(filepath)


def test_atomic_write_supports_current_directory(tmp_path, monkeypatch):
    from modules.orchestrator.agent_wrapper import _atomic_write

    monkeypatch.chdir(tmp_path)
    _atomic_write("report.md", "content")

    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "content"


def test_run_with_browser_use_writes_agent_result(temp_output_path):
    from modules.orchestrator import agent_wrapper

    history = MagicMock()
    history.final_result.return_value = "Extracted by autonomous browser"
    agent = MagicMock()
    with patch.object(agent_wrapper, "_create_browser_use_agent", return_value=agent), \
         patch.object(agent_wrapper, "_run_browser_use_agent", return_value=history):
        result = agent_wrapper._run_with_browser_use(
            "https://example.com", "collect facts", temp_output_path
        )

    assert result["success"] is True
    assert result["backend"] == "browser_use"
    assert "Extracted by autonomous browser" in Path(temp_output_path).read_text(encoding="utf-8")


def test_create_browser_use_agent_requires_key(monkeypatch):
    from modules.orchestrator import agent_wrapper

    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="API_KEY"):
        agent_wrapper._create_browser_use_agent("https://example.com", "collect")


def test_run_with_fetch_success(temp_output_path):
    from modules.orchestrator import agent_wrapper

    fetched = {"content": "Static page content", "url": "https://example.com"}
    with patch("modules.mcp.fetch_mcp.fetch_url", return_value=fetched):
        result = agent_wrapper._run_with_fetch(
            "https://example.com", "collect", temp_output_path
        )

    assert result["success"] is True
    assert result["backend"] == "fetch"
    assert "Static page content" in Path(temp_output_path).read_text(encoding="utf-8")


# ── _run_with_browser_daemon tests ─────────────────────────────


def test_run_with_browser_daemon_navigate_error(temp_output_path):
    """_run_with_browser_daemon should handle navigate error."""
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_call_browser_daemon",
                      return_value={"error": "connection refused"}):
        result = agent_wrapper._run_with_browser_daemon(
            "https://example.com", "test task", temp_output_path
        )
        assert result["success"] is False
        assert "error" in result
        assert len(result["steps"]) == 1  # Only navigate step


def test_run_with_browser_daemon_screenshot_error(temp_output_path):
    """_run_with_browser_daemon should handle screenshot error."""
    from modules.orchestrator import agent_wrapper

    def mock_daemon(endpoint, method="GET", data=None):
        if endpoint == "/navigate":
            return {"success": True, "url": data["url"]}
        elif endpoint == "/screenshot":
            return {"error": "screenshot failed"}
        return {}

    with patch.object(agent_wrapper, "_call_browser_daemon", side_effect=mock_daemon):
        with patch("time.sleep"):  # Skip actual waiting
            result = agent_wrapper._run_with_browser_daemon(
                "https://example.com", "test task", temp_output_path
            )
            assert result["success"] is False
            assert "screenshot" in result["error"].lower() or "error" in result


def test_run_with_browser_daemon_no_text_recognized(temp_output_path):
    """_run_with_browser_daemon should handle empty OCR result."""
    from modules.orchestrator import agent_wrapper

    def mock_daemon(endpoint, method="GET", data=None):
        if endpoint == "/navigate":
            return {"success": True}
        elif endpoint == "/screenshot":
            return {"path": "/fake/screenshot.png"}
        return {}

    with patch.object(agent_wrapper, "_call_browser_daemon", side_effect=mock_daemon), \
         patch.object(agent_wrapper, "_recognize_screenshot",
                      return_value={"text": "", "success": False}), \
         patch("time.sleep"):
        result = agent_wrapper._run_with_browser_daemon(
            "https://example.com", "test task", temp_output_path
        )
        assert result["success"] is False
        assert "no text" in result["error"].lower()


def test_run_with_browser_daemon_success(temp_output_path):
    """_run_with_browser_daemon should complete pipeline successfully."""
    from modules.orchestrator import agent_wrapper

    def mock_daemon(endpoint, method="GET", data=None):
        if endpoint == "/navigate":
            return {"success": True}
        elif endpoint == "/screenshot":
            return {"path": "/fake/screenshot.png"}
        return {}

    with patch.object(agent_wrapper, "_call_browser_daemon", side_effect=mock_daemon), \
         patch.object(agent_wrapper, "_recognize_screenshot",
                      return_value={"text": "Recognized content here", "success": True}), \
         patch.object(agent_wrapper, "_summarize_content",
                      return_value="# Summary\n\nGenerated summary"), \
         patch("time.sleep"):
        result = agent_wrapper._run_with_browser_daemon(
            "https://example.com", "test task", temp_output_path
        )
        assert result["success"] is True
        assert result["backend"] == "browser_daemon"
        assert os.path.exists(temp_output_path)

        # Verify file content
        with open(temp_output_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "Collection Report" in content
            assert "Generated summary" in content


# ── run_collection_pipeline tests ──────────────────────────────


def test_run_collection_pipeline_fallback(temp_output_path):
    """Pipeline should fallback to daemon when browser-use unavailable."""
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_check_browser_use", return_value=False), \
         patch.object(agent_wrapper, "_run_with_browser_daemon",
                      return_value={"success": True, "backend": "browser_daemon"}):
        result = agent_wrapper.run_collection_pipeline(
            "https://example.com", temp_output_path
        )
        assert result["success"] is True
        assert result["backend"] == "browser_daemon"


def test_run_collection_pipeline_browser_use_failure_fallback(temp_output_path):
    """Pipeline should fallback when browser-use fails."""
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_check_browser_use", return_value=True), \
         patch.object(agent_wrapper, "_run_with_browser_use",
                      return_value={"success": False, "error": "LLM not configured"}), \
         patch.object(agent_wrapper, "_run_with_browser_daemon",
                      return_value={"success": True, "backend": "browser_daemon"}):
        result = agent_wrapper.run_collection_pipeline(
            "https://example.com", temp_output_path
        )
        assert result["success"] is True
        assert result["backend"] == "browser_daemon"
        assert result.get("primary_error") == "LLM not configured"


def test_run_collection_pipeline_uses_fetch_after_daemon_failure(temp_output_path):
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_check_browser_use", return_value=False), \
         patch.object(
             agent_wrapper,
             "_run_with_browser_daemon",
             return_value={"success": False, "error": "daemon unavailable"},
         ), \
         patch.object(
             agent_wrapper,
             "_run_with_fetch",
             return_value={"success": True, "backend": "fetch"},
         ):
        result = agent_wrapper.run_collection_pipeline(
            "https://example.com", temp_output_path
        )

    assert result["success"] is True
    assert result["backend"] == "fetch"
    assert result["fallback_errors"] == ["daemon unavailable"]


def test_run_collection_pipeline_reports_every_backend_failure(temp_output_path):
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_check_browser_use", return_value=True), \
         patch.object(
             agent_wrapper,
             "_run_with_browser_use",
             return_value={"success": False, "error": "agent failed"},
         ), \
         patch.object(
             agent_wrapper,
             "_run_with_browser_daemon",
             return_value={"success": False, "error": "daemon failed"},
         ), \
         patch.object(
             agent_wrapper,
             "_run_with_fetch",
             return_value={"success": False, "error": "fetch failed"},
         ):
        result = agent_wrapper.run_collection_pipeline(
            "https://example.com", temp_output_path
        )

    assert result["success"] is False
    assert result["errors"] == ["agent failed", "daemon failed", "fetch failed"]


def test_run_collection_pipeline_default_output_path():
    """Pipeline should generate default output path when not provided."""
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_check_browser_use", return_value=False), \
         patch.object(agent_wrapper, "_run_with_browser_daemon",
                      return_value={"success": True, "backend": "browser_daemon", "output_path": "auto"}):
        result = agent_wrapper.run_collection_pipeline("https://example.com")
        # _run_with_browser_daemon should have been called with a generated path
        assert result["success"] is True


@pytest.mark.parametrize("target", ["", "example.com", "file:///etc/passwd"])
def test_run_collection_pipeline_rejects_non_http_targets(target, temp_output_path):
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_run_with_browser_daemon") as daemon:
        result = agent_wrapper.run_collection_pipeline(target, temp_output_path)

    assert result["success"] is False
    assert "HTTP(S)" in result["error"] or "empty" in result["error"]
    daemon.assert_not_called()


def test_run_collection_pipeline_rejects_output_over_source_file(tmp_path):
    from modules.orchestrator import agent_wrapper

    source_path = agent_wrapper._PROJECT_ROOT / "README.md"
    with patch.object(agent_wrapper, "_run_with_browser_daemon") as daemon:
        result = agent_wrapper.run_collection_pipeline(
            "https://example.com", str(source_path)
        )

    assert result["success"] is False
    assert "_runtime" in result["error"]
    daemon.assert_not_called()


# ── get_backend_status tests ───────────────────────────────────


def test_get_backend_status():
    """get_backend_status should return status dict."""
    from modules.orchestrator import agent_wrapper

    with patch.object(agent_wrapper, "_check_browser_use", return_value=False), \
         patch.object(agent_wrapper, "_call_browser_daemon",
                      return_value={"error": "unreachable"}):
        status = agent_wrapper.get_backend_status()
        assert "browser_use_available" in status
        assert "browser_daemon_reachable" in status
        assert status["browser_use_available"] is False
        assert status["browser_daemon_reachable"] is False
