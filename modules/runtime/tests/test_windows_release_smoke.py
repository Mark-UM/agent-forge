from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSER_COMMAND = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path (Get-Location) "scripts\windows-release-smoke.ps1"),
    [ref]$tokens,
    [ref]$errors
) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }
    exit 1
}
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 only")
def test_windows_release_smoke_parses_in_windows_powershell() -> None:
    powershell = shutil.which("powershell.exe")
    assert powershell is not None, "powershell.exe is required on Windows"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            PARSER_COMMAND,
        ],
        check=False,
        capture_output=True,
        cwd=PROJECT_ROOT,
        text=True,
    )

    assert result.returncode == 0, result.stderr
