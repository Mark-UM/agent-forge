# Technical Documentation

> **项目路径**：`E:/system_folder/.claude/.claude`
> **文档定位**：技术架构、实现细节、数据流、安全模型
> **受众**：开发者 / 维护者
> **最后更新**：2026-07-19

---

## 目录

1. [技术栈](#1-技术栈)
2. [架构总览](#2-架构总览)
3. [配置系统实现](#3-配置系统实现)
4. [扩展点机制](#4-扩展点机制)
5. [MCP 集成架构](#5-mcp-集成架构)
6. [Python 后端实现](#6-python-后端实现)
7. [数据流](#7-数据流)
8. [安全模型](#8-安全模型)
9. [性能特性](#9-性能特性)
10. [已知限制](#10-已知限制)

---

## 1. 技术栈

| 层 | 技术 | 版本 | 用途 |
|----|------|------|------|
| AI Agent | OpenCode | latest | CLI/TUI 主体 |
| 主模型 | DeepSeek V4 Pro | — | 编程/推理 |
| 辅助模型 | DeepSeek V4 Flash | — | 审查 subagent |
| 视觉模型 | SiliconFlow Qwen3-VL-Plus | — | 图像/PDF 识别 |
| 工具运行时 | Bun | latest | TypeScript 工具执行 |
| Python 后端 | CPython | 3.11+ | recognize.py, daemon.py |
| 浏览器自动化 | Playwright | latest | Firefox 持久化 context |
| MCP 协议 | JSON-RPC over stdio/SSE | 1.0+ | 13 个 MCP server 集成 |
| 配置格式 | JSON / Markdown / YAML | — | 分层配置 |
| 包管理 | npm (global) / pip | — | Node.js / Python 包 |

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│                     用户交互层                                        │
│        OpenCode TUI (终端)  |  opencode run (headless)               │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  OpenCode Core                                                        │
│  ├─ Provider Manager (AI SDK 75+ vendors)                            │
│  │   ├─ deepseek/deepseek-v4-pro (Build agent)                       │
│  │   └─ deepseek/deepseek-v4-flash (Subagents)                       │
│  ├─ Instruction Loader (AGENTS.md + profile.md)                      │
│  ├─ Watcher (file system monitor, respects ignore patterns)          │
│  ├─ Permission Manager (edit/write/bash)                             │
│  └─ Plugin Loader (3 plugins)                                        │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼───────┐  ┌─────────▼─────────┐  ┌──────▼──────┐
│  MCP Layer    │  │  Extension Layer  │  │ Plugin Layer│
│  (13 servers) │  │  (.opencode/)     │  │ (3 plugins) │
└───────────────┘  └───────────────────┘  └─────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼───────┐  ┌─────────▼─────────┐  ┌──────▼──────┐
│ tools/*.ts    │  │ agents/*.md       │  │ commands/*  │
│ (10 tools)    │  │ (3 subagents)     │  │ (4 commands)│
└───────────────┘  └───────────────────┘  └─────────────┘
                             │
                   ┌─────────▼─────────┐
                   │ skills/ (67)      │
                   └───────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  Python Backend Layer                                                 │
│  ├─ modules/vision/recognize.py  (SiliconFlow API via urllib)        │
│  ├─ modules/vision/clipboard.py  (PIL + subprocess)                  │
│  └─ modules/browser/daemon.py    (Playwright HTTP API on :9223)      │
└──────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│  Storage Layer                                                        │
│  ├─ markconfig/secrets.json   (API keys, encrypted at rest via OS)   │
│  ├─ _data/memory/*.md         (user profile, 7 files)                │
│  ├─ _runtime/handoff/*.md     (context handoff packets)              │
│  ├─ _runtime/mcp-memory.json  (Memory MCP data)                      │
│  ├─ _runtime/mcp-sqlite.db    (SQLite MCP data)                      │
│  └─ _runtime/vision_usage.jsonl (vision API usage log)               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. 配置系统实现

### 3.1 配置加载顺序（高优先级覆盖低优先级）

```
1. Remote config      (服务端推送，未启用)
2. Global config      (~/.config/opencode/opencode.json)
3. OPENCODE_CONFIG    (环境变量指向的配置文件)
4. Project config     (opencode.json)
5. .opencode/ inline (tools/agents/commands/skills 定义)
```

### 3.2 变量替换机制

OpenCode 支持两种变量替换：

| 语法 | 解析时机 | 用途 |
|------|---------|------|
| `{env:VAR_NAME}` | 配置加载时 | 读取进程环境变量 |
| `{file:path}` | 配置加载时 | 读取文件内容（整体读取，不支持 JSON 字段提取） |

**设计决策**：由于 `{file:path}` 不支持 JSON 字段提取，API keys 通过 `start-opencode.bat` 从 `markconfig/secrets.json` 读取并设置为环境变量，MCP 配置中通过进程环境变量传递。

### 3.3 Project Config 结构

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "deepseek/deepseek-v4-pro",
  "small_model": "deepseek/deepseek-v4-flash",
  "permission": { "edit": "allow", "bash": "ask", "write": "allow" },
  "instructions": ["AGENTS.md", "markconfig/profile.md"],
  "watcher": { "ignore": [...] },
  "plugin": [...],
  "mcp": { ... }
}
```

### 3.4 Instruction 加载

`instructions` 字段指定的文件会被注入到 system prompt：
- `AGENTS.md` — 行为约束、审查规则、视觉铁律
- `markconfig/profile.md` — 用户画像

文件内容按顺序拼接，前者优先级更高。

---

## 4. 扩展点机制

### 4.1 Custom Tools (`.opencode/tools/*.ts`)

**运行时**：Bun（OpenCode 内置）
**SDK**：`@opencode-ai/plugin` v1.18.3
**注册方式**：每个 `.ts` 文件 `export default tool({...})` 或 `export const xxx = tool({...})`

**vision.ts 实现细节**：
```typescript
export default tool({
  description: "...",
  args: {
    files: tool.schema.array(tool.schema.string()),
    prompt: tool.schema.string().optional(),
  },
  async execute(args, context) {
    const script = path.join(context.worktree, "modules/vision/recognize.py")
    // 数组传参避免 shell 注入
    const result = await Bun.$`${PYTHON} ${script} ${[...args.files, prompt]}`.text()
    return result.trim()
  },
})
```

**关键点**：
- `context.worktree` 是项目根目录
- `Bun.$` 模板字符串支持数组展开，自动转义参数
- 多个 `export const` 会注册为多个独立工具

### 4.2 Subagents (`.opencode/agents/*.md`)

**Frontmatter 格式**：
```yaml
---
description: <subagent 描述，用于主 agent 调用决策>
mode: subagent
model: deepseek/deepseek-v4-flash
temperature: 0.1
permission:
  edit: deny
  write: deny
  bash: deny
---
<system prompt body>
```

**权限隔离**：subagent 的 `permission` 独立于主 agent。审查 subagent 全部 deny，确保只读审查。

### 4.3 Commands (`.opencode/commands/*.md`)

Slash 命令，Markdown 格式。支持 `subtask: true` 触发子任务。

### 4.4 Skills (`.opencode/skills/`)

**发现规则**：OpenCode 扫描 `.opencode/skills/<name>/SKILL.md`（一层深度）

**Junction 方案**：克隆的仓库结构是三层深度（`<repo>/skills/<name>/SKILL.md`），通过 Windows directory junction 暴露到顶层。

**重新生成**：
```powershell
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1
```

---

## 5. MCP 集成架构

### 5.1 MCP Server 类型

| 类型 | 通信方式 | 启动方式 | 示例 |
|------|---------|---------|------|
| `local` (npx) | stdio (JSON-RPC) | `npx -y <package>` | filesystem, github, etc. |
| `local` (python) | stdio (JSON-RPC) | `python -m <module>` | git, duckduckgo |
| `remote` | SSE (Server-Sent Events) | 连接 URL | context7 |

### 5.2 配置字段

```json
"<mcp-name>": {
  "type": "local" | "remote",
  "command": ["...", "..."],          // local only
  "url": "https://...",                // remote only
  "environment": { "KEY": "VALUE" },   // local only, 进程环境变量
  "enabled": true,
  "timeout": 30000                     // 毫秒
}
```

### 5.3 启动流程

1. OpenCode 启动 → 读取 `opencode.json` 的 `mcp` 字段
2. 对每个 `enabled: true` 的 MCP server：
   - `local`：fork 子进程，通过 stdio 通信
   - `remote`：建立 SSE 连接
3. OpenCode 通过 JSON-RPC 调用 MCP server 暴露的工具
4. 主 agent 在工具列表中看到所有 MCP 工具

### 5.4 环境变量传递链

```
markconfig/secrets.json
    ↓ (start-opencode.bat 读取)
进程环境变量 (DEEPSEEK_API_KEY, etc.)
    ↓ (OpenCode 继承)
MCP server 子进程
    ↓ (MCP server 读取自己的环境变量)
GitHub MCP → GITHUB_PERSONAL_ACCESS_TOKEN
```

---

## 6. Python 后端实现

### 6.1 recognize.py — 视觉识别

**架构**：单文件脚本，零外部 HTTP 依赖（使用 `urllib.request`）

**流程**：
```
argv → _parse_args()
         ├─ 文件参数 → 验证扩展名 → PDF? → _convert_pdf_to_pngs() → 临时 PNG
         │                                  → 普通? → 直接使用
         └─ 非文件参数 → 视为 prompt
       ↓
       _call_vision_api(images, prompt)
         ├─ _encode_image() → base64 data URL
         ├─ 构造 payload (OpenAI Chat Completions 格式)
         ├─ urllib.request.urlopen() → SiliconFlow API
         └─ 返回 (text, usage)
       ↓
       _log_usage(usage) → _runtime/vision_usage.jsonl
       ↓
       print(text)
       ↓ (finally)
       清理临时 PDF PNG 文件
```

**安全特性**：
- 扩展名白名单：`.png .jpg .jpeg .webp .bmp .gif .pdf`
- API key 从环境变量读取，不硬编码
- PDF 转换失败时清理已生成的临时文件
- HTTP 错误使用 `errors='replace'` 解码，避免崩溃

### 6.2 daemon.py — 浏览器 HTTP API

**架构**：单线程 HTTP server + Playwright 持久化 context

**安全特性**（本次审计新增）：
- **URL scheme 白名单**：仅允许 `http`/`https`，阻止 `file://`、`chrome://` 等
- **截图路径白名单**：仅允许 MODULE_DIR、PROJECT_ROOT、~/Pictures、~/Desktop、tempdir
- **CSS 选择器长度限制**：1000 字符
- **JS 代码长度限制**：100,000 字符
- **Body 大小限制**：1MB
- **滚动坐标范围限制**：±1,000,000
- **CORS 限制**：`Access-Control-Allow-Origin: http://127.0.0.1:*`（仅本地）
- **绑定地址**：`127.0.0.1`（仅本地访问）

**Playwright 持久化**：
- 使用 `launch_persistent_context(FIREFOX_PROFILE)`，保留所有登录态
- Firefox 可执行文件路径显式指定
- `args=["--remote-debugging-port=0"]` 避免端口冲突

### 6.3 clipboard.py — 剪贴板识别

**架构**：PIL ImageGrab → 临时文件 → subprocess 调用 recognize.py

**改进**：
- 使用 `sys.executable` 而非硬编码 `python3`
- 使用列表传参（`subprocess.run([PYTHON, ...])`）避免 shell 注入
- 临时文件删除包裹在 try/except 中

---

## 7. 数据流

### 7.1 编程任务数据流

```
用户输入 "修复 bug in auth.py"
    ↓
OpenCode Build agent (DeepSeek V4 Pro)
    ├─ 读取 AGENTS.md → 加载行为约束
    ├─ 读取 markconfig/profile.md → 加载用户画像
    ├─ 分析请求 → 制定计划
    ├─ 调用 edit/read 工具 → 修改代码
    └─ AGENTS.md 规则触发 → 调用 @review-code subagent
                            ↓
                            DeepSeek V4 Flash (deny edit/write/bash)
                            ├─ 读取修改后的代码
                            ├─ 分析逻辑/边界/异常
                            └─ 返回 PASS/FAIL + Findings
    ↓
    (FAIL → 修复 → 重审，最多 2 轮)
    ↓
    调用 @review-structure → 同上流程
    ↓
    调用 @review-risk → 同上流程
    ↓
    全部 PASS → 返回结果给用户
```

### 7.2 视觉识别数据流

```
用户输入 "识别 image.png"
    ↓
OpenCode (DeepSeek V4 Pro)
    ├─ AGENTS.md 视觉铁律触发
    └─ 调用 vision 工具
        ↓
        vision.ts (Bun runtime)
        ├─ 构造命令：[PYTHON, recognize.py, image.png, prompt]
        └─ Bun.$`...`.text() 执行
            ↓
            recognize.py (CPython 3.11)
            ├─ 读取 SILICONFLOW_API_KEY 环境变量
            ├─ _encode_image() → base64
            ├─ urllib.request → SiliconFlow API
            │   POST https://api.siliconflow.cn/v1/chat/completions
            │   Body: {model, messages: [{role, content: [text, image_url]}]}
            └─ 返回 text + usage
                ↓
                写入 _runtime/vision_usage.jsonl
                ↓
                stdout 输出结果
            ↓
            Bun 捕获 stdout
        ↓
        返回给 OpenCode
    ↓
    OpenCode 返回给用户
```

### 7.3 MCP 调用数据流

```
用户输入 "搜索 OpenCode 特性"
    ↓
OpenCode (DeepSeek V4 Pro)
    └─ 决定调用 duckduckgo MCP
        ↓
        OpenCode MCP Client
        ├─ 查找 duckduckgo server 进程
        ├─ JSON-RPC 请求: tools/call {name: "search", args: {query: "..."}}
        ↓
        duckduckgo_mcp_server (Python)
        ├─ 接收 stdio JSON-RPC
        ├─ 调用 DuckDuckGo API
        └─ 返回搜索结果
            ↓
            JSON-RPC response → OpenCode
        ↓
        OpenCode 整合结果
    ↓
    返回给用户
```

---

## 8. 安全模型

### 8.1 API Key 保护

| Key | 存储位置 | 访问方式 | 日志暴露 |
|-----|---------|---------|---------|
| DEEPSEEK_API_KEY | markconfig/secrets.json | 环境变量 | `[REDACTED]` |
| SILICONFLOW_API_KEY | markconfig/secrets.json | 环境变量 | 不输出 |
| GITHUB_PERSONAL_ACCESS_TOKEN | markconfig/secrets.json | 环境变量 | 仅输出 "loaded" |

**.gitignore 排除**：`markconfig/secrets.json` 不被版本控制追踪。

### 8.2 权限模型

| Agent | edit | write | bash | 用途 |
|-------|------|-------|------|------|
| 主 agent (Build) | allow | allow | ask | 编程、文件操作 |
| review-code | deny | deny | deny | 只读代码审查 |
| review-structure | deny | deny | deny | 只读结构审查 |
| review-risk | deny | deny | deny | 只读风险审查 |

### 8.3 网络安全

- **浏览器 Daemon**：绑定 `127.0.0.1:9223`，仅本地访问
- **CORS**：`Access-Control-Allow-Origin: http://127.0.0.1:*`
- **URL scheme 白名单**：浏览器仅允许 `http`/`https`
- **MCP remote**：仅 context7 使用 SSE，其余均为本地 stdio

### 8.4 输入验证（本次审计新增）

| 端点 | 验证规则 |
|------|---------|
| `/navigate` | URL scheme 白名单 |
| `/click` `/type` `/wait` | CSS 选择器非空 + 长度 ≤ 1000 |
| `/type` | text 长度 ≤ 100,000 |
| `/keys` | key 长度 ≤ 100 |
| `/screenshot` | 路径在白名单目录内 |
| `/js` | code 非空 + 长度 ≤ 100,000 |
| `/scroll` | 坐标范围 ±1,000,000 |
| 所有 POST | Body ≤ 1MB |

### 8.5 临时文件管理

- PDF 转换的临时 PNG 在 `finally` 块中清理
- 剪贴板临时文件在 `finally` 块中清理
- 清理失败不影响主流程（`try/except OSError: pass`）

---

## 9. 性能特性

### 9.1 启动性能

| 阶段 | 耗时 | 说明 |
|------|------|------|
| 首次启动 | 1-2 分钟 | npx 下载所有 MCP server npm 包 |
| 后续启动 | < 3 秒 | 依赖缓存完成 |
| Plugin 安装 | 首次 10-30 秒 | npm 下载 plugin 包 |

### 9.2 运行时性能

- **MCP 通信**：stdio JSON-RPC，无网络开销（local 类型）
- **Python 工具调用**：subprocess fork，每次 ~100ms 启动开销
- **浏览器 Daemon**：长驻进程，HTTP 调用 < 10ms
- **视觉 API**：单次调用 2-10 秒（取决于图片大小和数量）

### 9.3 上下文优化

- **opencode-dcp plugin**：动态裁剪上下文，节省 token
- **watcher.ignore**：排除 `node_modules`、`skills`、`plugins`、`_runtime` 等
- **small_model**：审查 subagent 使用 Flash 模型，成本和延迟均低于 Pro

---

## 10. 已知限制

### 10.1 平台限制

- **仅支持 Windows**：`start-opencode.bat`、Firefox 路径、Python 路径均为 Windows 特定
- **C 盘空间敏感**：npm 全局包安装到 `E:\npm-global`，避免 C 盘占用
- **长路径**：Windows MAX_PATH 限制，`skills/` 目录曾因 5700+ 文件删除缓慢

### 10.2 网络限制

- **国内访问**：DuckDuckGo 和 Google 需代理，SearXNG 公共实例 `searx.be` 通常可访问
- **马来西亚访问**：所有 MCP server 无障碍
- **SiliconFlow API**：国内可直连

### 10.3 功能限制

- **OpenCode 无 IDE 集成**：当前仅 CLI/TUI 模式
- **MCP server 首次下载**：npx 首次运行可能超时，需手动预安装
- **Subagent 串行**：3 个审查 subagent 串行执行，非并行
- **Skill 发现深度**：仅扫描一层，依赖 junction 机制暴露深层 skills

### 10.4 依赖风险

- **OpenCode 版本**：使用 latest tag，可能引入 breaking change
- **Plugin 版本**：3 个 plugin 均使用 latest，同上
- **MCP server 版本**：npx `-y` 自动安装最新版，同上

**缓解**：可锁定具体版本号（如 `opencode-ai@1.18.3`），但会增加维护成本。

---

**End of Technical Documentation**
