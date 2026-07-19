# System Documentation

> **项目路径**：`E:/system_folder/.claude/.claude`
> **文档定位**：系统组件、运行时行为、运维管理
> **受众**：系统管理员 / 运维 / 二次维护者
> **最后更新**：2026-07-19

---

## 目录

1. [系统组件清单](#1-系统组件清单)
2. [运行时架构](#2-运行时架构)
3. [进程模型](#3-进程模型)
4. [端口与网络](#4-端口与网络)
5. [文件系统布局](#5-文件系统布局)
6. [状态管理](#6-状态管理)
7. [日志系统](#7-日志系统)
8. [启动流程](#8-启动流程)
9. [关闭流程](#9-关闭流程)
10. [备份与恢复](#10-备份与恢复)
11. [健康检查](#11-健康检查)

---

## 1. 系统组件清单

### 核心进程

| 组件 | 类型 | 启动方式 | 运行模式 |
|------|------|---------|---------|
| OpenCode | Node.js CLI | `opencode` 命令 | 前台 TUI / headless |
| Browser Daemon | Python | `python modules/browser/daemon.py` | 后台长驻 |
| MCP Servers (13) | Node.js / Python | OpenCode 自动 fork | 子进程，随 OpenCode 退出 |

### 静态组件

| 组件 | 位置 | 加载时机 |
|------|------|---------|
| AGENTS.md | 项目根 | OpenCode 启动时注入 system prompt |
| opencode.json | 项目根 | OpenCode 启动时读取 |
| markconfig/secrets.json | markconfig/ | start-opencode.bat 读取 |
| markconfig/profile.md | markconfig/ | OpenCode 启动时注入 system prompt |
| _data/memory/*.md | _data/memory/ | memory-context skill 按需加载 |

---

## 2. 运行时架构

```
┌─────────────────────────────────────────────────────────┐
│  start-opencode.bat                                      │
│  ├─ 读取 markconfig/secrets.json                         │
│  ├─ 设置环境变量 (DEEPSEEK_API_KEY, etc.)                │
│  ├─ PATH += E:\npm-global                                │
│  └─ exec opencode                                        │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  OpenCode Process (Node.js)                              │
│  ├─ 加载 opencode.json                                    │
│  ├─ 注入 AGENTS.md + profile.md 到 system prompt         │
│  ├─ 启动 Plugin Loader                                   │
│  │   ├─ 加载 oh-my-opencode                              │
│  │   ├─ 加载 @tarquinen/opencode-dcp                     │
│  │   └─ 加载 opencode-antigravity-auth                   │
│  ├─ 启动 MCP Manager                                     │
│  │   ├─ fork filesystem MCP (npx)                        │
│  │   ├─ fork github MCP (npx)                            │
│  │   ├─ connect context7 MCP (SSE)                       │
│  │   ├─ fork sequential-thinking MCP (npx)               │
│  │   ├─ fork memory MCP (npx)                            │
│  │   ├─ fork playwright MCP (npx)                        │
│  │   ├─ fork fetch MCP (npx)                             │
│  │   ├─ fork sqlite MCP (npx)                            │
│  │   ├─ fork time MCP (npx)                              │
│  │   ├─ fork git MCP (python)                            │
│  │   ├─ fork duckduckgo MCP (python)                     │
│  │   ├─ fork searxng MCP (npx)                           │
│  │   └─ fork g-search MCP (npx)                          │
│  ├─ 加载 .opencode/tools/*.ts (Bun runtime)              │
│  ├─ 注册 .opencode/agents/*.md (3 subagents)             │
│  ├─ 注册 .opencode/commands/*.md (4 commands)            │
│  ├─ 扫描 .opencode/skills/*/SKILL.md (67 skills)         │
│  └─ 启动 Watcher (文件系统监控)                           │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│  User Interaction                                        │
│  ├─ TUI 输入                                             │
│  ├─ 工具调用 (edit/read/bash/vision/browser/...)         │
│  ├─ Subagent 调用 (@review-code 等)                      │
│  ├─ Slash 命令 (/doctor /review /handoff /browser)      │
│  └─ MCP 工具调用                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 进程模型

### 3.1 主进程

```
opencode.exe (Node.js)
├─ PID: 主进程
├─ 子进程: 13 个 MCP servers (stdio)
├─ 子进程: Bun runtime (工具执行时按需启动)
├─ 子进程: Python (vision 工具调用时启动)
└─ 线程: Watcher (文件系统监控)
```

### 3.2 Browser Daemon 进程

独立进程，通过 `/browser` 命令或浏览器工具调用时启动：

```
python.exe modules/browser/daemon.py
├─ PID: daemon 主进程
├─ 子进程: firefox.exe (Playwright 启动)
└─ 线程: HTTPServer (端口 9223)
```

**生命周期**：
- 启动：`/browser` 命令或首次调用浏览器工具
- 关闭：`/close` 端点、Ctrl+C、或 OpenCode 退出后手动关闭
- 重启：调用 `/close` 后再次调用浏览器工具会自动重启

### 3.3 进程退出顺序

```
OpenCode 收到退出信号 (Ctrl+C / /exit)
    ↓
关闭所有 MCP server 子进程 (发送 SIGTERM)
    ↓
保存会话状态
    ↓
主进程退出
    ↓
（Browser Daemon 不会自动退出，需手动关闭或下次 /browser 时重启）
```

---

## 4. 端口与网络

### 4.1 端口占用

| 端口 | 服务 | 绑定地址 | 访问范围 |
|------|------|---------|---------|
| 9223 | Browser Daemon HTTP API | 127.0.0.1 | 仅本地 |

### 4.2 外部连接

| 目标 | 协议 | 用途 | 认证 |
|------|------|------|------|
| api.deepseek.com | HTTPS | DeepSeek V4 Pro/Flash API | Bearer token |
| api.siliconflow.cn | HTTPS | Qwen3-VL-Plus 视觉 API | Bearer token |
| api.github.com | HTTPS | GitHub MCP | Bearer token |
| mcp.context7.com | HTTPS/SSE | Context7 MCP | 无 |
| searx.be | HTTPS | SearXNG 公共实例 | 无 |
| duckduckgo.com | HTTPS | DuckDuckGo 搜索 | 无 |
| google.com | HTTPS | g-search (Playwright) | 无 |

### 4.3 防火墙要求

- **出站**：允许 HTTPS (443) 到上述所有域名
- **入站**：无需开放任何端口（Browser Daemon 仅绑定 127.0.0.1）

---

## 5. 文件系统布局

### 5.1 目录权限

| 目录 | 读 | 写 | 说明 |
|------|----|----|------|
| `E:/system_folder/.claude/.claude/` | ✓ | ✓ | 项目根 |
| `C:/Users/mingy/.config/opencode/` | ✓ | ✓ | 全局配置 |
| `E:/npm-global/` | ✓ | ✓ | npm 全局包 |
| `C:/Users/mingy/AppData/Local/Programs/Python/Python311/` | ✓ | ✗ | Python 解释器 |
| `%APPDATA%/Mozilla/Firefox/Profiles/` | ✓ | ✓ | Firefox profile |
| `~/Pictures`, `~/Desktop` | ✓ | ✓ | 截图白名单目录 |

### 5.2 文件大小监控

| 文件 | 增长方式 | 建议上限 | 清理方式 |
|------|---------|---------|---------|
| `_runtime/vision_usage.jsonl` | 每次视觉调用 +1 行 | 10MB | 手动删除 |
| `_runtime/mcp-memory.json` | Memory MCP 写入 | 无上限 | 手动清理 |
| `_runtime/mcp-sqlite.db` | SQLite MCP 写入 | 无上限 | 手动清理 |
| `_runtime/handoff/*.md` | `/handoff save` | 7 天 | `/handoff clean` |
| `~/.config/opencode/log/` | OpenCode 日志 | 100MB | 自动轮转 |

### 5.3 大目录警告

以下目录可能占用大量空间：

| 目录 | 大小 | 说明 |
|------|------|------|
| `.opencode/skills/*/` | ~200MB | 4 个 GitHub 仓库克隆 |
| `~/.npm/_npx/` | ~500MB | npx 缓存的 MCP server 包 |
| `~/.cache/opencode/` | ~100MB | OpenCode 缓存 |

---

## 6. 状态管理

### 6.1 会话状态

OpenCode 会话状态在内存中，不持久化。退出后丢失。

**持久化方案**：`/handoff save` 命令将关键上下文写入 `_runtime/handoff/LATEST.md`。

### 6.2 用户记忆状态

| 文件 | 内容 | 更新方式 |
|------|------|---------|
| `_data/memory/MEMORY.md` | 核心记忆汇总 | 手动 |
| `_data/memory/user-*.md` | 7 个分类记忆 | 手动 |
| `markconfig/profile.md` | 个人画像 | 手动 |

### 6.3 MCP 状态

| MCP | 状态存储 | 持久化 |
|-----|---------|--------|
| memory | `_runtime/mcp-memory.json` | ✓ |
| sqlite | `_runtime/mcp-sqlite.db` | ✓ |
| filesystem | 无状态 | — |
| github | 无状态（token 在环境变量） | — |
| 其他 | 无状态 | — |

---

## 7. 日志系统

### 7.1 日志位置

| 日志 | 位置 | 格式 | 用途 |
|------|------|------|------|
| OpenCode 日志 | `~/.config/opencode/log/` | text | 主进程行为 |
| 视觉 API 日志 | `_runtime/vision_usage.jsonl` | JSONL | token 使用量 |
| Browser Daemon | stdout（不持久化） | text | HTTP 请求 |
| MCP 通信 | 无日志 | — | stdio JSON-RPC |

### 7.2 视觉日志格式

```json
{
  "model": "Qwen/Qwen3-VL-Plus",
  "input_tokens": 1234,
  "output_tokens": 567,
  "timestamp": "2026-07-19T10:30:00.123456"
}
```

---

## 8. 启动流程

### 8.1 详细启动序列

```
1. 用户执行 start-opencode.bat
   ├─ 设置 CLAUDE_HOME=E:\system_folder\.claude\.claude
   ├─ 设置 SECRETS_FILE=%CLAUDE_HOME%\markconfig\secrets.json
   ├─ 设置 PYTHON=C:\Users\mingy\...\python.exe
   ├─ Python 读取 secrets.json:
   │   ├─ DEEPSEEK_API_KEY = secrets['ANTHROPIC_AUTH_TOKEN']
   │   ├─ SILICONFLOW_API_KEY = secrets['SILICONFLOW_API_KEY']
   │   └─ GITHUB_PERSONAL_ACCESS_TOKEN = secrets.get(...)
   ├─ PATH += E:\npm-global
   └─ exec opencode %*

2. OpenCode 启动
   ├─ 加载 ~/.config/opencode/opencode.json (global)
   ├─ 加载 E:\...\.claude\opencode.json (project, 覆盖 global)
   ├─ 解析 model: deepseek/deepseek-v4-pro
   ├─ 解析 permission: {edit: allow, bash: ask, write: allow}
   ├─ 加载 instructions:
   │   ├─ AGENTS.md
   │   └─ markconfig/profile.md
   ├─ 初始化 Watcher (应用 ignore patterns)
   ├─ Plugin Loader:
   │   ├─ 安装 oh-my-opencode@latest (首次)
   │   ├─ 安装 @tarquinen/opencode-dcp (首次)
   │   └─ 安装 opencode-antigravity-auth@latest (首次)
   ├─ MCP Manager:
   │   ├─ 启动 filesystem MCP (npx -y @modelcontextprotocol/server-filesystem)
   │   ├─ 启动 github MCP (npx -y @modelcontextprotocol/server-github)
   │   ├─ 连接 context7 MCP (SSE)
   │   ├─ ... (其余 10 个 MCP)
   │   └─ 等待所有 MCP ready (最多 timeout)
   ├─ Tool Loader:
   │   ├─ 加载 .opencode/tools/vision.ts
   │   └─ 加载 .opencode/tools/browser.ts (9 个工具)
   ├─ Agent Loader:
   │   ├─ 注册 review-code subagent
   │   ├─ 注册 review-structure subagent
   │   └─ 注册 review-risk subagent
   ├─ Command Loader:
   │   ├─ 注册 /doctor
   │   ├─ 注册 /review
   │   ├─ 注册 /handoff
   │   └─ 注册 /browser
   └─ Skill Scanner:
       └─ 扫描 .opencode/skills/*/SKILL.md (67 个)

3. TUI 显示，等待用户输入
```

### 8.2 首次启动 vs 后续启动

| 步骤 | 首次启动 | 后续启动 |
|------|---------|---------|
| secrets.json 读取 | ~200ms | ~200ms |
| Plugin 安装 | 10-30s (npm 下载) | <1s (缓存) |
| MCP server 启动 | 30-60s (npx 下载) | <3s (缓存) |
| 工具/agent/command 加载 | <1s | <1s |
| Skill 扫描 | <1s | <1s |
| **总计** | **1-2 分钟** | **< 5 秒** |

---

## 9. 关闭流程

### 9.1 正常关闭

```
用户 Ctrl+C 或 /exit
    ↓
OpenCode 发送 SIGTERM 给所有 MCP 子进程
    ↓
MCP servers 清理并退出
    ↓
OpenCode 保存会话元数据
    ↓
OpenCode 主进程退出
    ↓
（Browser Daemon 仍在运行，需手动关闭）
```

### 9.2 异常关闭

```
OpenCode 崩溃 / kill -9
    ↓
MCP 子进程成为孤儿（可能被 init 收养）
    ↓
Browser Daemon 仍在运行
    ↓
下次启动时:
    ├─ OpenCode 重新启动所有 MCP
    └─ Browser Daemon 检测到已有实例，发 /close 后重启
```

### 9.3 清理残留进程

```powershell
# 检查残留 MCP 进程
Get-Process | Where-Object { $_.ProcessName -match "node|python" } | Where-Object { $_.CommandLine -match "mcp" }

# 检查 Browser Daemon
netstat -ano | findstr :9223

# 强制关闭
Stop-Process -Id <PID> -Force
```

---

## 10. 备份与恢复

### 10.1 需要备份的内容

| 内容 | 路径 | 优先级 | 频率 |
|------|------|--------|------|
| API Keys | `markconfig/secrets.json` | 高 | 变更时 |
| 个人画像 | `markconfig/profile.md` | 高 | 变更时 |
| 用户记忆 | `_data/memory/*.md` | 高 | 变更时 |
| 配置文件 | `opencode.json`, `AGENTS.md` | 中 | 变更时 |
| 扩展点 | `.opencode/` | 中 | 变更时 |
| Python 后端 | `modules/` | 中 | 变更时 |
| 交接包 | `_runtime/handoff/` | 低 | 每周 |
| MCP 数据 | `_runtime/mcp-*.json/db` | 低 | 每周 |

### 10.2 不需要备份

- `.opencode/skills/*/`（GitHub 仓库，可重新克隆）
- `~/.npm/_npx/`（npm 缓存，可重新下载）
- `_runtime/vision_usage.jsonl`（日志，非关键数据）

### 10.3 恢复流程

```
1. 重装 OpenCode: npm install -g opencode-ai
2. 重装 Python 依赖: pip install -r requirements.txt
3. 恢复 markconfig/、_data/、_runtime/、.opencode/
4. 重新克隆 skills:
   powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1
5. 启动: start-opencode.bat
```

---

## 11. 健康检查

### 11.1 /doctor 命令

`/doctor` 执行 6 项检查：

| # | 检查项 | 通过条件 | 失败处理 |
|---|--------|---------|---------|
| 1 | DeepSeek API | `opencode run "ping"` 返回正常 | 检查 DEEPSEEK_API_KEY |
| 2 | Python 可用 | `python --version` 输出 3.11+ | 检查 PATH |
| 3 | Vision 模块 | `modules/vision/recognize.py` 存在 | 检查文件 |
| 4 | Browser 模块 | `modules/browser/daemon.py` 存在 | 检查文件 |
| 5 | markconfig 完整 | secrets.json + profile.md 存在 | 检查目录 |
| 6 | AGENTS.md | 文件存在且非空 | 检查文件 |

### 11.2 手动健康检查

```powershell
# 1. OpenCode 可用性
opencode run "Reply with exactly: PING"

# 2. DeepSeek API 连通
curl -H "Authorization: Bearer %DEEPSEEK_API_KEY%" https://api.deepseek.com/v1/models

# 3. Python 依赖
python -c "import fitz; print('PyMuPDF OK')"
python -c "import PIL; print('Pillow OK')"
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
python -c "import mcp_server_git; print('mcp_server_git OK')"
python -c "import duckduckgo_mcp_server; print('duckduckgo_mcp_server OK')"

# 4. Browser Daemon
curl http://127.0.0.1:9223/ping

# 5. 文件完整性
Test-Path E:\system_folder\.claude\.claude\AGENTS.md
Test-Path E:\system_folder\.claude\.claude\opencode.json
Test-Path E:\system_folder\.claude\.claude\markconfig\secrets.json
Test-Path E:\system_folder\.claude\.claude\markconfig\profile.md
Test-Path E:\system_folder\.claude\.claude\modules\vision\recognize.py
Test-Path E:\system_folder\.claude\.claude\modules\browser\daemon.py
```

---

**End of System Documentation**
