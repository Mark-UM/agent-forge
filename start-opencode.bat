@echo off
setlocal

pushd "%~dp0"
set "PROJECT_ROOT=%CD%"
set "SECRETS_FILE=%PROJECT_ROOT%\markconfig\secrets.json"
if defined AGENT_FORGE_PYTHON (
    set "PYTHON=%AGENT_FORGE_PYTHON%"
    for %%I in ("%AGENT_FORGE_PYTHON%") do set "PATH=%%~dpI;%PATH%"
) else (
    set "PYTHON=python"
)
set "VENDOR_LIBS=%PROJECT_ROOT%\vendor\python-libs"

if exist "%VENDOR_LIBS%" (
    if defined PYTHONPATH (
        set "PYTHONPATH=%VENDOR_LIBS%;%PYTHONPATH%"
    ) else (
        set "PYTHONPATH=%VENDOR_LIBS%"
    )
)

"%PYTHON%" --version >nul 2>nul
if errorlevel 1 (
    echo [agent-forge] Python was not found on PATH.
    popd
    exit /b 1
)

"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
if errorlevel 1 (
    echo [agent-forge] Python 3.11 is required. Set AGENT_FORGE_PYTHON to its executable path.
    popd
    exit /b 1
)

"%PYTHON%" -m modules.bootstrap.dependencies check >nul
if errorlevel 1 (
    echo [agent-forge] Local dependencies are missing or incompatible.
    echo [agent-forge] Run: "%PYTHON%" -m modules.bootstrap.dependencies install
    popd
    exit /b 1
)

if not exist "%SECRETS_FILE%" (
    echo [agent-forge] Missing markconfig\secrets.json. Copy secrets.example.json first.
    popd
    exit /b 1
)

"%PYTHON%" -m modules.bootstrap.secrets_env >nul
if errorlevel 1 (
    echo [agent-forge] Failed to load markconfig\secrets.json.
    popd
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in (`"%PYTHON%" -m modules.bootstrap.secrets_env`) do set "%%A=%%B"

set "DEEPSEEK_BASE_URL=https://api.deepseek.com"

echo [agent-forge] Composing AGENTS_COMPOSED.md...
"%PYTHON%" -m modules.prompt.composer --pre-session
if errorlevel 1 (
    echo [agent-forge] Prompt composition failed; using AGENTS.md as fallback.
    copy /Y "%PROJECT_ROOT%\AGENTS.md" "%PROJECT_ROOT%\AGENTS_COMPOSED.md" >nul
)

echo [agent-forge] Launching OpenCode. MCP and plugin state is defined in opencode.json.
opencode %*
set "EXIT_CODE=%ERRORLEVEL%"

popd
endlocal & exit /b %EXIT_CODE%
