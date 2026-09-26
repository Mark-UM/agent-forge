"""Public CLI entrypoints remain callable after the 2.0 compatibility split."""
from __future__ import annotations

import subprocess
import sys


def test_search_cli_help_uses_compatibility_parser():
    result = subprocess.run(
        [sys.executable, "-m", "modules.search.search", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode == 0
    assert "service-health" in result.stdout


def test_search_service_health_cli_formats_provider_states():
    result = subprocess.run(
        [sys.executable, "-m", "modules.search.search", "service-health"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode in (0, 1)
    assert "cache_dir:" in result.stdout
    assert "TypeError" not in result.stderr
