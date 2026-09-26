"""Importing Vision adapters must not read a user's clipboard."""
from __future__ import annotations

import subprocess
import sys


def test_clipboard_adapter_import_has_no_side_effects():
    result = subprocess.run(
        [sys.executable, "-c", "import modules.vision.clipboard"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_clipboard_cli_help_is_available_without_an_image():
    result = subprocess.run(
        [sys.executable, "-m", "modules.vision.clipboard", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode == 0
    assert "clipboard" in result.stdout.lower()
