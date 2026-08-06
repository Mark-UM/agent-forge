"""Prompt Composer package and compatibility installation."""
from __future__ import annotations

__version__ = "1.5.0"


def _install_cross_platform_path_compatibility() -> None:
    """Make basename-based detectors independent of the runner OS.

    ``pathlib.Path`` on Linux does not treat a Windows backslash as a separator.
    Normalizing before delegating preserves every existing detector pattern and
    fixes Windows paths when the test suite runs on Ubuntu.
    """

    from modules.prompt import context as context_module

    original = context_module.detect_test_file
    if getattr(original, "_agent_forge_cross_platform", False):
        return

    def detect_test_file(file_path: str) -> bool:
        normalized = file_path.replace("\\", "/") if isinstance(file_path, str) else file_path
        return original(normalized)

    detect_test_file._agent_forge_cross_platform = True
    context_module.detect_test_file = detect_test_file


_install_cross_platform_path_compatibility()
