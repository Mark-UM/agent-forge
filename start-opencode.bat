@echo off
setlocal

pushd "%~dp0"
set "PROJECT_ROOT=%CD%"
set "SECRETS_FILE=%PROJECT_ROOT%\markconfig\secrets.json"
set "PYTHON=python"

where %PYTHON% >nul 2>nul
if errorlevel 1 (
    echo [agent-forge] Python was not found on PATH.
    popd
    exit /b 1
)

if not exist "%SECRETS_FILE%" (
    echo [agent-forge] Missing markconfig\secrets.json. Copy secrets.example.json first.
    popd
    exit /b 1
)

for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%', encoding='utf-8')); print(d.get('ANTHROPIC_AUTH_TOKEN',''))"') do set "DEEPSEEK_API_KEY=%%i"
for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%', encoding='utf-8')); print(d.get('SILICONFLOW_API_KEY',''))"') do set "SILICONFLOW_API_KEY=%%i"
for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%', encoding='utf-8')); print(d.get('GITHUB_PERSONAL_ACCESS_TOKEN',''))"') do set "GITHUB_PERSONAL_ACCESS_TOKEN=%%i"
for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%', encoding='utf-8')); print(d.get('SERPER_API_KEY',''))"') do set "SERPER_API_KEY=%%i"

set "DEEPSEEK_BASE_URL=https://api.deepseek.com"

echo [agent-forge] Composing AGENTS_COMPOSED.md...
%PYTHON% -m modules.prompt.composer --pre-session
if errorlevel 1 (
    echo [agent-forge] Prompt composition failed; using AGENTS.md as fallback.
    copy /Y "%PROJECT_ROOT%\AGENTS.md" "%PROJECT_ROOT%\AGENTS_COMPOSED.md" >nul
)

echo [agent-forge] Launching OpenCode. MCP and plugin state is defined in opencode.json.
opencode %*
set "EXIT_CODE=%ERRORLEVEL%"

popd
endlocal & exit /b %EXIT_CODE%
