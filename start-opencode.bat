@echo off
setlocal

REM ============================================================
REM  OpenCode Startup Script for Mark
REM  Reads API keys from markconfig/secrets.json and launches opencode
REM  Supports: DeepSeek API, SiliconFlow Vision, GitHub MCP
REM  Search MCPs: duckduckgo, searxng, g-search (all free, no API key)
REM ============================================================

set CLAUDE_HOME=E:\system_folder\.claude\.claude
set SECRETS_FILE=%CLAUDE_HOME%\markconfig\secrets.json

REM Extract API keys from secrets.json using Python
set PYTHON=C:\Users\mingy\AppData\Local\Programs\Python\Python311\python.exe

for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%')); print(d['ANTHROPIC_AUTH_TOKEN'])"') do set DEEPSEEK_API_KEY=%%i
for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%')); print(d['SILICONFLOW_API_KEY'])"') do set SILICONFLOW_API_KEY=%%i
for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%')); print(d.get('GITHUB_PERSONAL_ACCESS_TOKEN',''))"') do set GITHUB_PERSONAL_ACCESS_TOKEN=%%i

REM Set DeepSeek base URL (OpenCode native provider handles this, but keep for reference)
set DEEPSEEK_BASE_URL=https://api.deepseek.com

echo [start-opencode] DEEPSEEK_API_KEY loaded: [REDACTED]
if defined GITHUB_PERSONAL_ACCESS_TOKEN (
    if not "%GITHUB_PERSONAL_ACCESS_TOKEN%"=="" echo [start-opencode] GITHUB_PERSONAL_ACCESS_TOKEN loaded
) else (
    echo [start-opencode] GITHUB_PERSONAL_ACCESS_TOKEN empty - GitHub MCP will fail
)

echo [start-opencode] Launching OpenCode...
echo [start-opencode] Plugins: oh-my-opencode, opencode-dcp, opencode-antigravity-auth
echo [start-opencode] MCP servers: filesystem, github, context7, sequential-thinking, memory, playwright, fetch, sqlite, time, git
echo [start-opencode] Search MCPs: duckduckgo, searxng, g-search (all free, no API key required)
echo [start-opencode] Skills: 67 junctions from 4 repos (anthropics, obra, mattpocock, vercel)

REM Ensure npm-global is in PATH
set PATH=E:\npm-global;%PATH%

opencode %*

endlocal
