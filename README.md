# Mark AI Infrastructure — OpenCode Edition

> **项目路径**：`E:/system_folder/.claude/.claude`
> **所有者**：韩铭洋（Mark）
> **AI Agent**：OpenCode + DeepSeek V4 Pro / V4 Flash
> **最后更新**：2026-07-19
> **架构**：OpenCode 原生扩展点 + 13 MCP Servers + 3 Plugins + 67 Skills + Python 后端 + 用户记忆系统

---

## 目录

1. [项目简介](#1-项目简介)
2. [快速启动](#2-快速启动)
3. [核心能力总览](#3-核心能力总览)
4. [目录结构](#4-目录结构)
5. [配置系统](#5-配置系统)
6. [MCP Servers（13 个）](#6-mcp-servers13-个)
7. [Plugins（3 个）](#7-plugins3-个)
8. [Skills 框架（67 个）](#8-skills-框架67-个)
9. [扩展点详解](#9-扩展点详解)
10. [Python 后端模块](#10-python-后端模块)
11. [Slash 命令手册](#11-slash-命令手册)
12. [Subagent 调用手册](#12-subagent-调用手册)
13. [用户记忆系统](#13-用户记忆系统)
14. [工作流示例](#14-工作流示例)
15. [环境依赖](#15-环境依赖)
16. [故障排查](#16-故障排查)
17. [架构决策记录](#17-架构决策记录)
18. [文件索引](#18-文件索引)
19. [附录：架构图](#19-附录架构图)
20. [附录：迁移前后对比](#20-附录迁移前后对比)

---

## 文档体系

本项目包含 5 份文档，按受众和用途分类：

| 文档 | 路径 | 受众 | 内容 |
|------|------|------|------|
| **README.md** | `README.md` | 所有人 | 项目总览、快速启动、全貌索引 |
| **TECHNICAL.md** | `_docs/TECHNICAL.md` | 开发者 | 技术架构、实现细节、数据流、安全模型 |
| **SYSTEM.md** | `_docs/SYSTEM.md` | 运维 | 系统组件、进程模型、端口、备份恢复 |
| **DEVELOPER.md** | `_docs/DEVELOPER.md` | 扩展开发者 | 开发环境、扩展开发指南、代码规范、调试 |
| **USER_GUIDE.md** | `_docs/USER_GUIDE.md` | 用户（Mark） | 日常使用、命令手册、工作流、维护 |

**推荐阅读顺序**：
- 新手 → README → USER_GUIDE
- 开发者 → README → TECHNICAL → DEVELOPER
- 运维 → README → SYSTEM

---

## 1. 项目简介

本项目是 Mark 的个人 AI 基础设施，基于 **OpenCode**（开源 AI coding agent，186k stars）构建。

### 模型层

| 角色 | 模型 | 用途 |
|------|------|------|
| 主脑 | DeepSeek V4 Pro | 编程、推理、复杂任务（1M context, thinking mode） |
| 审查 | DeepSeek V4 Flash | 3 阶段代码审查 Subagent |
| 视觉 | SiliconFlow Qwen3-VL-Plus | 图像/PDF 识别 |

### 扩展生态

| 类型 | 数量 | 说明 |
|------|------|------|
| MCP Servers | 13 | 官方 + 社区 MCP，覆盖文件/GitHub/搜索/数据库/浏览器/记忆 |
| Plugins | 3 | OpenCode 原生 plugin，agent 编排 + 上下文优化 |
| Skills | 67 | 4 个 GitHub 仓库的精选 skills（anthropics/obra/mattpocock/vercel） |
| Custom Tools | 10 | TypeScript 工具（vision + 9 个 browser 工具） |
| Subagents | 3 | 代码/结构/风险审查 |
| Commands | 4 | /doctor /review /handoff /browser |

### 架构特点

- **零后台服务**：OpenCode 原生集成所有工具调用，无 bridge_server / tool_registry
- **配置分层**：global → env → project → `.opencode/` 内联覆盖
- **三层搜索架构**：DuckDuckGo + SearXNG 元搜索 + Google 直查（全免费、无 API key）
- **TypeScript 工具 + Python 后端**：OpenCode 工具用 TS，复用稳定 Python 模块
- **审查分权**：审查 Subagent 使用 Flash 模型 + `deny edit/write/bash` 权限

---

## 2. 快速启动

### 首次启动

```bash
# Windows
E:\system_folder\.claude\.claude\start-opencode.bat
```

启动脚本自动完成：
1. 从 `markconfig/secrets.json` 读取 API keys（DeepSeek + SiliconFlow + GitHub Token）
2. 设置环境变量 `DEEPSEEK_API_KEY`、`SILICONFLOW_API_KEY`、`GITHUB_PERSONAL_ACCESS_TOKEN`
3. 将 `E:\npm-global` 加入 PATH（OpenCode 安装位置）
4. 启动 `opencode`

### 首次启动注意事项

- **首次启动会较慢**（1-2 分钟）：OpenCode 会通过 npx 自动下载所有 MCP server 的 npm 包
- **Plugin 自动安装**：3 个 plugins 会自动从 npm 下载
- **后续启动秒级**：依赖缓存完成后启动 < 3 秒

### 验证启动

```
# 进入 OpenCode TUI 后输入：
/doctor

# 应返回 6 项检查全部 PASS
```

### 验证 DeepSeek 连通性

```bash
opencode run "Reply with exactly: OPENCODE_OK"
# 应返回：OPENCODE_OK
```

---

## 3. 核心能力总览

| 能力 | 实现 |
|------|------|
| AI 编程助手 | OpenCode + DeepSeek V4 Pro（1M context, thinking mode） |
| 3 阶段代码审查 | review-code → review-structure → review-risk（Flash 模型） |
| 图像/PDF 识别 | SiliconFlow Qwen3-VL-Plus（多图 base64，PDF 自动转 PNG） |
| 浏览器自动化 | Playwright Firefox Daemon + Playwright MCP（双引擎） |
| 三层网络搜索 | DuckDuckGo + SearXNG 元搜索 + Google 直查 |
| GitHub 操作 | 仓库管理、PR、Issue、代码搜索 |
| 实时文档查询 | Context7 MCP（解决模型知识截止问题） |
| 结构化多步推理 | Sequential Thinking MCP |
| 持久化知识图谱 | Memory MCP（JSON 存储） |
| 文件系统操作 | Filesystem MCP（跨目录操作） |
| 数据库直连 | SQLite MCP |
| 上下文交接 | `/handoff` 命令 |
| 用户个性化 | 7 个分类记忆文件 + AGENTS.md 强约束 |
| 健康诊断 | `/doctor` 命令 |

---

## 4. 目录结构

```
E:/system_folder/.claude/.claude/
├── AGENTS.md                    # OpenCode 行为约束（被 instructions 加载）
├── opencode.json                # 项目级 OpenCode 配置（13 MCP + 3 plugin）
├── start-opencode.bat           # Windows 启动脚本
├── INIT_REPORT.md               # 迁移完成报告
├── README.md                    # 本文件
│
├── .opencode/                   # OpenCode 扩展点
│   ├── tools/                   # 自定义工具（TypeScript）
│   │   ├── vision.ts            # 视觉识别工具
│   │   └── browser.ts           # 浏览器工具组（9 个工具）
│   ├── agents/                  # Subagents（Markdown）
│   │   ├── review-code.md       # 代码质量审查
│   │   ├── review-structure.md  # 文件结构审查
│   │   └── review-risk.md       # 安全风险审查
│   ├── commands/                # Slash 命令
│   │   ├── doctor.md            # /doctor 健康诊断
│   │   ├── review.md            # /review 审查管道
│   │   ├── handoff.md           # /handoff 上下文交接
│   │   └── browser.md           # /browser 启动浏览器
│   ├── skills/                  # 67 个 skills（4 个仓库 + junctions）
│   │   ├── memory-context/      # 原生 skill：用户记忆索引
│   │   ├── anthropics-skills/   # anthropics/skills 仓库（17 skills）
│   │   ├── obra-superpowers/    # obra/superpowers 仓库（14 skills）
│   │   ├── mattpocock-skills/   # mattpocock/skills 仓库（25 skills）
│   │   ├── vercel-agent-skills/ # vercel-labs/agent-skills 仓库（9 skills）
│   │   └── <prefix>-<skill>/    # 67 个 directory junctions（OpenCode 发现用）
│   ├── package.json             # @opencode-ai/plugin v1.18.3 依赖
│   └── .gitignore
│
├── markconfig/                  # Mark 的个人配置
│   ├── secrets.json             # API Keys
│   ├── profile.md               # 个人画像（被 instructions 加载）
│   ├── paths.json               # 系统路径
│   └── README.md
│
├── modules/                     # Python 后端模块
│   ├── vision/
│   │   ├── recognize.py         # SiliconFlow Qwen3-VL-Plus 调用
│   │   ├── clipboard.py         # 剪贴板图片识别
│   │   └── manifest.json
│   └── browser/
│       └── daemon.py            # Playwright Firefox HTTP Daemon（urllib 实现）
│
├── _data/
│   └── memory/                  # 用户记忆文件
│       ├── MEMORY.md            # 核心记忆汇总
│       ├── user-profile.md
│       ├── user-personality.md
│       ├── user-tech-stack.md
│       ├── user-career.md
│       ├── user-real-life.md
│       ├── user-reply-preferences.md
│       └── README-INIT.md
│
├── _runtime/
│   ├── handoff/                 # 上下文交接包
│   │   ├── LATEST.md
│   │   └── context_handoff_*.md
│   ├── mcp-memory.json          # Memory MCP 数据
│   ├── mcp-sqlite.db            # SQLite MCP 数据库
│   ├── vision_usage.jsonl       # 视觉模型使用日志
│   ├── create-skill-junctions.ps1  # junction 生成脚本
│   └── cleanup-legacy.ps1       # 清理脚本
│
└── _docs/
    └── PROJECT_DOC.md           # 详细架构文档
```

---

## 5. 配置系统

### 5.1 配置层级（高优先级覆盖低优先级）

| 层级 | 路径 | 用途 |
|------|------|------|
| Remote | 服务端推送 | 团队共享（未启用） |
| **Global** | `C:\Users\mingy\.config\opencode\opencode.json` | 全局 provider + API Key + 默认模型 |
| Env | `OPENCODE_CONFIG` 环境变量 | 临时覆盖 |
| **Project** | `E:\system_folder\.claude\.claude\opencode.json` | 项目级覆盖（13 MCP + 3 plugin） |
| Inline | `.opencode/` 目录 | 工具、agent、command、skill 定义 |

### 5.2 Project Config（`opencode.json`）

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "deepseek/deepseek-v4-pro",
  "small_model": "deepseek/deepseek-v4-flash",
  "permission": { "edit": "allow", "bash": "ask", "write": "allow" },
  "instructions": ["AGENTS.md", "markconfig/profile.md"],
  "watcher": {
    "ignore": [
      "node_modules/**", "plugins/**", "skills/**", "workspace/**",
      "_state/**", "_runtime/**", "__pycache__/**",
      ".opencode/skills/**/node_modules/**", ".opencode/skills/**/.git/**"
    ]
  },
  "plugin": [
    "oh-my-opencode@latest",
    "@tarquinen/opencode-dcp",
    "opencode-antigravity-auth@latest"
  ],
  "mcp": {
    /* 13 个 MCP servers — 见第 6 节 */
  }
}
```

### 5.3 变量替换

- `{env:VAR_NAME}` — 读取环境变量
- `{file:path}` — 读取文件内容（不支持 JSON 字段提取，故 API keys 走环境变量）

### 5.4 AGENTS.md（行为约束）

通过 `instructions` 字段加载，包含：
- Top Priority Directive（用户意图优先）
- Response Style（英文为主，中文解释复杂概念）
- Code Review Architecture（3 阶段强制审查）
- Vision/PDF 识别铁律（必须用 vision 工具）
- Web Search Permission
- Context Management
- File Paths
- Personal Profile Summary

---

## 6. MCP Servers（13 个）

全部配置在 [opencode.json](file:///e:/system_folder/.claude/.claude/opencode.json#L22) 的 `mcp` 字段。

### 6.1 官方 MCP Servers（10 个）

| MCP | 类型 | 命令 | 用途 | API Key |
|-----|------|------|------|---------|
| **filesystem** | local npx | `@modelcontextprotocol/server-filesystem` | 跨目录文件操作（E:/ 全盘） | 无 |
| **github** | local npx | `@modelcontextprotocol/server-github` | 仓库管理、PR、Issue、代码搜索 | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| **context7** | remote SSE | `https://mcp.context7.com/sse` | 实时拉取最新官方文档 | 无 |
| **sequential-thinking** | local npx | `@modelcontextprotocol/server-sequential-thinking` | 结构化多步推理 | 无 |
| **memory** | local npx | `@modelcontextprotocol/server-memory` | 持久化知识图谱（JSON 存储） | 无 |
| **playwright** | local npx | `@playwright/mcp@latest` | 官方浏览器自动化 | 无 |
| **fetch** | local npx | `@modelcontextprotocol/server-fetch` | URL 抓取转 markdown | 无 |
| **sqlite** | local npx | `@modelcontextprotocol/server-sqlite` | SQLite 数据库直连 | 无 |
| **time** | local npx | `@modelcontextprotocol/server-time` | 时间和时区查询 | 无 |
| **git** | local python | `python -m mcp_server_git` | Git 仓库操作 | 无 |

### 6.2 搜索 MCP Servers（3 个，全免费）

三层搜索架构，按质量递增：

| MCP | 搜索源 | 命令 | API Key | 国内可用 |
|-----|--------|------|---------|----------|
| **duckduckgo** | DuckDuckGo | `python -m duckduckgo_mcp_server` | 无 | 需代理 |
| **searxng** | 70+ 元搜索 | `npx -y mcp-searxng`（用 `searx.be` 公共实例） | 无 | 部分可访问 |
| **g-search** | Google 直查 | `npx -y g-search-mcp` | 无 | 需代理 |

### 6.3 环境变量传递

API keys 通过 `start-opencode.bat` 从 `markconfig/secrets.json` 加载到环境变量，MCP 配置中无需明文：

```json
"github": {
  "type": "local",
  "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
  "enabled": true,
  "timeout": 30000
}
```

GitHub MCP 启动时自动从进程环境变量读取 `GITHUB_PERSONAL_ACCESS_TOKEN`。

---

## 7. Plugins（3 个）

配置在 [opencode.json](file:///e:/system_folder/.claude/.claude/opencode.json#L17) 的 `plugin` 字段。首次启动自动安装。

| Plugin | npm 包 | 功能 |
|--------|--------|------|
| **Oh-My-OpenCode** | `oh-my-opencode@latest` | Agent 编排层，异步子 agent、多模型协作、类 Claude Code 协调能力 |
| **Dynamic Context Pruning** | `@tarquinen/opencode-dcp` | 动态裁剪上下文，节省 token、提升长会话稳定性 |
| **Antigravity Auth** | `opencode-antigravity-auth@latest` | 增强认证流，接入需要 OAuth 的模型供应商 |

---

## 8. Skills 框架（67 个）

### 8.1 仓库来源

| 仓库 | 路径 | Skills 数 | 内容 |
|------|------|-----------|------|
| [anthropics/skills](https://github.com/anthropics/skills) | `.opencode/skills/anthropics-skills/` | 17 | PDF/Excel/Word/PPT 操作、MCP builder 等 |
| [obra/superpowers](https://github.com/obra/superpowers) | `.opencode/skills/obra-superpowers/` | 14 | TDD、brainstorming、systematic-debugging 等 |
| [mattpocock/skills](https://github.com/mattpocock/skills) | `.opencode/skills/mattpocock-skills/` | 25 | code-review、tdd、domain-modeling 等 |
| [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) | `.opencode/skills/vercel-agent-skills/` | 9 | react-best-practices、vercel-optimize 等 |

### 8.2 Skill 发现机制

OpenCode 默认扫描 `.opencode/skills/<skill-name>/SKILL.md`（一层深度）。克隆的仓库结构是 `<repo>/skills/<skill-name>/SKILL.md`（三层深度）。

**解决方案**：[_runtime/create-skill-junctions.ps1](file:///e:/system_folder/.claude/.claude/_runtime/create-skill-junctions.ps1) 为每个有效 skill 创建 directory junction 到顶层，命名格式 `<prefix>-<skill-name>`：

```
.opencode/skills/
├── anthropics-pdf/            → anthropics-skills/skills/pdf/
├── anthropics-excel/          → anthropics-skills/skills/excel/
├── obra-test-driven-development/  → obra-superpowers/skills/test-driven-development/
├── matt-code-review/          → mattpocock-skills/skills/code-review/
├── vercel-react-best-practices/   → vercel-agent-skills/skills/react-best-practices/
└── ... (67 个 junctions)
```

重新生成 junctions：
```powershell
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1
```

### 8.3 原生 Skill

- **memory-context** — 用户记忆文件索引（`.opencode/skills/memory-context/SKILL.md`）

---

## 9. 扩展点详解

### 9.1 Custom Tools（`.opencode/tools/*.ts`）

TypeScript 编写，通过 `@opencode-ai/plugin` 的 `tool()` 函数注册。

**vision.ts** — 视觉识别工具
```typescript
export default tool({
  description: "Recognize image or PDF content using SiliconFlow Qwen3-VL model...",
  args: {
    files: tool.schema.array(tool.schema.string()),
    prompt: tool.schema.string().optional(),
  },
  async execute(args, context) {
    const script = path.join(context.worktree, "modules/vision/recognize.py")
    const python = "C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe"
    const result = await Bun.$`${python} ${script} ${args.files} ${prompt}`.text()
    return result.trim()
  },
})
```

**browser.ts** — 9 个浏览器工具

| 工具 | 端点 | 用途 |
|------|------|------|
| `navigate` | POST /navigate | 导航到 URL |
| `click` | POST /click | 点击 CSS 选择器 |
| `type` | POST /type | 输入文本 |
| `screenshot` | POST /screenshot | 全页截图 |
| `page_text` | GET /text | 提取页面文本 |
| `page_content` | GET /content | 提取 HTML |
| `execute_js` | POST /js | 执行 JavaScript |
| `browser_status` | GET /status | 查询 URL/标题 |
| `scroll` | POST /scroll | 滚动页面 |

### 9.2 Subagents（`.opencode/agents/*.md`）

Markdown frontmatter 定义元数据，正文是 system prompt。

```yaml
---
description: Reviews code for logic errors, bugs, exception handling...
mode: subagent
model: deepseek/deepseek-v4-flash
temperature: 0.1
permission:
  edit: deny
  write: deny
  bash: deny
---
You are a code quality reviewer...
```

| Subagent | 职责 |
|----------|------|
| `review-code` | 逻辑错误 / Bug / 边界 / 命名 / 异常处理 |
| `review-structure` | 文件组织 / 导入依赖 / 模块结构 / 命名一致性 |
| `review-risk` | 安全漏洞 / 危险操作 / 敏感信息 / 权限提升 |

### 9.3 Commands（`.opencode/commands/*.md`）

| 命令 | 用途 |
|------|------|
| `/doctor` | 6 项健康诊断 |
| `/review` | 触发 3 阶段审查管道 |
| `/handoff [save\|list\|clean]` | 上下文交接管理 |
| `/browser` | 启动浏览器 Daemon |

---

## 10. Python 后端模块

### 10.1 modules/vision/ — 视觉识别

**`recognize.py`** — 主识别脚本
- 直接调用 SiliconFlow API（`https://api.siliconflow.cn/v1/chat/completions`）
- 模型：`Qwen/Qwen3-VL-Plus`
- 支持 PNG/JPG/WEBP/BMP/GIF
- PDF 自动转 PNG（依赖 PyMuPDF）
- 多图 base64 编码
- 使用标准库 `urllib.request`（零外部 HTTP 依赖）
- 日志：`_runtime/vision_usage.jsonl`

**`clipboard.py`** — 剪贴板图片识别

**依赖**：
- Python 3.11+
- PyMuPDF（PDF 识别）
- 环境变量：`SILICONFLOW_API_KEY`

### 10.2 modules/browser/ — 浏览器自动化

**`daemon.py`** — Playwright Firefox HTTP Daemon
- 端口：9223
- 使用 Firefox 持久化 profile（保留所有登录态）
- 自动检测并关闭旧实例
- 使用标准库 `urllib.request`（零外部 HTTP 依赖）
- HTTP API，无 SDK 依赖

**Firefox 配置**：
- 可执行文件：`C:\Program Files\Mozilla Firefox\firefox.exe`
- Profile：`%APPDATA%\Mozilla\Firefox\Profiles\cqe4w54g.default-release`

**API 端点**：
| 端点 | 方法 | 用途 |
|------|------|------|
| `/ping` | GET | 健康检查 |
| `/status` | GET | 当前 URL/标题 |
| `/navigate` | POST | 导航 |
| `/click` | POST | 点击 |
| `/type` | POST | 输入 |
| `/screenshot` | POST | 截图 |
| `/text` | GET | 页面文本 |
| `/content` | GET | 页面 HTML |
| `/js` | POST | 执行 JS |
| `/scroll` | POST | 滚动 |
| `/close` | POST | 关闭 Daemon |

---

## 11. Slash 命令手册

### `/doctor` — 健康诊断

检查 6 项：
1. DeepSeek API 连通性
2. Python 可用性
3. Vision 模块存在性
4. Browser 模块存在性
5. markconfig/ 完整性
6. AGENTS.md 存在性

### `/review` — 3 阶段代码审查

触发 3 个 Subagent 串联执行：
1. `review-code` — 代码质量
2. `review-structure` — 文件结构
3. `review-risk` — 安全风险

输出汇总报告：PASS/FAIL + Score + Findings。

### `/handoff [save|list|clean]` — 上下文交接

- `/handoff save`（默认）— 生成交接包到 `_runtime/handoff/`
- `/handoff list` — 列出所有历史交接包
- `/handoff clean` — 清理 7 天前的交接包

### `/browser` — 启动浏览器 Daemon

后台启动 `python modules/browser/daemon.py`，等待 2 秒后验证 `http://127.0.0.1:9223/ping`。

---

## 12. Subagent 调用手册

### `@review-code` — 代码质量审查

检查：逻辑错误 / Bug / 异常处理 / 边界条件 / 命名 / 代码风格

输出格式：
```
## Review-Code Result
**Verdict**: PASS / FAIL
**Score**: x/10
**Findings**:
1. [SEVERITY: critical/major/minor] Description
   - File: path:line
   - Suggestion: ...
```

### `@review-structure` — 文件结构审查

检查：文件组织 / 导入依赖 / 模块耦合 / 配置一致性 / 命名规范

### `@review-risk` — 安全风险审查

检查：安全漏洞 / 危险操作 / 数据丢失 / 硬编码密钥 / 权限提升

输出包含 `Risk Level: LOW / MEDIUM / HIGH / CRITICAL`。CRITICAL 立即 FAIL。

### 调用示例

```
@review-code 检查 auth.py 的登录逻辑
@review-structure 评估 modules/ 目录的组织
@review-risk 扫描代码中的硬编码密钥
```

---

## 13. 用户记忆系统

### 13.1 自动加载（via instructions）

OpenCode 启动时自动加载：
- `AGENTS.md` — 行为约束
- `markconfig/profile.md` — 个人画像

### 13.2 按需加载（via memory-context skill）

| 文件 | 内容 |
|------|------|
| `_data/memory/MEMORY.md` | 核心记忆汇总 |
| `_data/memory/user-profile.md` | 完整个人画像 |
| `_data/memory/user-personality.md` | 性格特征 |
| `_data/memory/user-tech-stack.md` | 技术栈详情 |
| `_data/memory/user-career.md` | 职业规划 |
| `_data/memory/user-real-life.md` | 家庭/财务/生活 |
| `_data/memory/user-reply-preferences.md` | 回复风格偏好 |

### 13.3 用户画像摘要

- **姓名**：韩铭洋（Mark）
- **国籍**：中国
- **大学**：Monash University Malaysia，Bachelor of Computer Science
- **技术栈**：Python, Java, C++, TypeScript, Haskell
- **职业目标**：计算机及 AI 方向的互联网大厂
- **位置**：动态 — 假期珠海，学期马来西亚

---

## 14. 工作流示例

### 工作流 A：正常开发 + 自动审查

```
1. 用户："修复 auth.py 的登录 bug"
2. OpenCode (Build agent) 分析 + 修改代码
3. AGENTS.md 强约束 → 自动调用 @review-code
4. review-code 完成 → 自动调用 @review-structure
5. review-structure 完成 → 自动调用 @review-risk
6. 全部 PASS → 任务完成
7. 任何 FAIL → 修复后重审（最多 2 轮）
```

### 工作流 B：图像识别

```
1. 用户粘贴截图或提供图片路径
2. AGENTS.md 铁律 → OpenCode 必须调用 vision 工具
3. vision.ts 调用 modules/vision/recognize.py
4. recognize.py 调用 SiliconFlow Qwen3-VL-Plus
5. 返回图片描述
```

### 工作流 C：浏览器自动化

```
1. 用户："/browser" 启动 Daemon
2. OpenCode 调用 browser_navigate 工具
3. browser.ts 通过 HTTP API 调用 daemon.py
4. daemon.py 控制 Firefox 导航
5. 返回页面信息
6. 后续可调用 click / type / screenshot 等工具
```

### 工作流 D：三层搜索

```
1. 用户："搜索 OpenCode 最新特性"
2. OpenCode 调用 duckduckgo MCP（DuckDuckGo）
3. 质量不足 → 调用 searxng MCP（70+ 元搜索）
4. 需 Google 精确结果 → 调用 g-search MCP
5. 找到 URL → 调用 fetch MCP 抓取页面
6. 返回综合搜索结果
```

### 工作流 E：GitHub 操作

```
1. 用户："搜索 opencode 相关仓库"
2. OpenCode 调用 github MCP 的 search_repositories 工具
3. 返回仓库列表（stars、描述、URL）
4. 用户："clone 第一个"
5. OpenCode 调用 filesystem MCP 或 bash 执行 git clone
```

### 工作流 F：实时文档查询

```
1. 用户："React useEffect 最新用法"
2. OpenCode 调用 context7 MCP（解决 DeepSeek 知识截止问题）
3. context7 返回 React 官方文档实时内容
4. OpenCode 基于最新文档回答
```

### 工作流 G：长任务上下文交接

```
1. 任务进行中，上下文接近上限
2. 用户："/handoff save"
3. OpenCode 生成交接包到 _runtime/handoff/LATEST.md
4. 新会话开始 → OpenCode 读取 LATEST.md
5. 上下文自动续接，任务继续
```

### 工作流 H：headless 测试

```bash
# 终端直接测试 OpenCode + DeepSeek
opencode run "Reply with exactly: TEST_OK"

# 测试 Python 后端
python modules/vision/recognize.py test.png
python modules/vision/recognize.py document.pdf "描述这个 PDF 的内容"
```

---

## 15. 环境依赖

### 必需

| 组件 | 版本 | 路径 |
|------|------|------|
| OpenCode | latest | `E:\npm-global\opencode.cmd` |
| Node.js | 18+ | 系统安装 |
| Python | 3.11+ | `C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe` |
| Firefox | latest | `C:\Program Files\Mozilla Firefox\firefox.exe` |
| Bun | latest | OpenCode 自带（用于运行 TS 工具） |

### Python 包

| 包 | 用途 | 安装 |
|----|------|------|
| PyMuPDF | PDF 识别 | `pip install PyMuPDF` ✅ 已安装 |
| Pillow | 剪贴板图片 | `pip install Pillow` |
| playwright | 浏览器自动化 | `pip install playwright` + `playwright install firefox` |
| mcp-server-git | Git MCP | `pip install mcp-server-git` ✅ 已安装 |
| duckduckgo-mcp-server | DuckDuckGo MCP | `pip install duckduckgo-mcp-server` ✅ 已安装 |

### API Keys（`markconfig/secrets.json`）

| Key | 用途 | 状态 |
|-----|------|------|
| `ANTHROPIC_AUTH_TOKEN` | DeepSeek V4 Pro/Flash | ✅ 已配置 |
| `SILICONFLOW_API_KEY` | SiliconFlow Qwen3-VL-Plus 视觉 | ✅ 已配置 |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub MCP | ✅ 已配置 |
| `FLASH_1_API_KEY` / `FLASH_2_API_KEY` | 备用（旧 Flash Key） | 保留 |
| `TAVILY_API_KEY` | 备用（未使用，Tavily MCP 未启用） | 空 |

### 环境变量

| 变量 | 来源 | 用途 |
|------|------|------|
| `DEEPSEEK_API_KEY` | start-opencode.bat | OpenCode DeepSeek provider |
| `SILICONFLOW_API_KEY` | start-opencode.bat | vision 工具 |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | start-opencode.bat | GitHub MCP |
| `PATH` | start-opencode.bat 追加 `E:\npm-global` | 找到 opencode 命令 |

---

## 16. 故障排查

### 问题：`opencode` 命令未找到

```bash
# 检查 PATH
echo %PATH%

# 手动添加
set PATH=E:\npm-global;%PATH%

# 或重新安装
npm install -g opencode-ai
```

### 问题：DeepSeek API 连接失败

```bash
# 1. 检查 API Key
python -c "import json; print(json.load(open('markconfig/secrets.json'))['ANTHROPIC_AUTH_TOKEN'][:10])"

# 2. 测试连通性
opencode run "Reply with exactly: PING"

# 3. 检查环境变量
echo %DEEPSEEK_API_KEY%
```

### 问题：MCP server 加载失败

```bash
# 首次启动时 npx 会下载包，可能超时
# 手动预安装：
npm install -g @modelcontextprotocol/server-github
npm install -g @modelcontextprotocol/server-filesystem
npm install -g mcp-searxng
npm install -g g-search-mcp
npm install -g @playwright/mcp

# Python MCP：
pip install mcp-server-git duckduckgo-mcp-server
```

### 问题：GitHub MCP 认证失败

```bash
# 检查 token 是否有效
curl -H "Authorization: token ghp_xxx" https://api.github.com/user

# 检查环境变量是否设置
echo %GITHUB_PERSONAL_ACCESS_TOKEN%

# 重新生成 token：https://github.com/settings/tokens
```

### 问题：搜索 MCP 在国内不可用

- **duckduckgo**：需代理（DuckDuckGo 在国内被墙）
- **searxng**：使用 `searx.be` 公共实例，国内通常可访问
- **g-search**：需代理（Google 在国内被墙）
- **到马来西亚后**：三个搜索 MCP 全部无障碍可用

### 问题：vision 工具失败

```bash
# 1. 检查 SILICONFLOW_API_KEY
echo %SILICONFLOW_API_KEY%

# 2. 直接测试 Python 后端
python modules/vision/recognize.py test.png

# 3. 检查 PyMuPDF（PDF 识别）
python -c "import fitz; print(fitz.__version__)"
```

### 问题：浏览器 Daemon 无法启动

```bash
# 1. 检查端口占用
netstat -ano | findstr :9223

# 2. 检查 Firefox 路径
dir "C:\Program Files\Mozilla Firefox\firefox.exe"

# 3. 检查 Playwright
python -c "from playwright.sync_api import sync_playwright; print('OK')"

# 4. 手动启动
python modules/browser/daemon.py
```

### 问题：Skills 未被识别

```bash
# 重新生成 junctions
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1

# 验证 junction 数量
Get-ChildItem .opencode\skills -Directory | Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint } | Measure-Object
```

### 问题：审查 Subagent 不触发

- 检查 AGENTS.md 是否被加载（`/doctor` 验证）
- 检查 `.opencode/agents/*.md` 文件存在
- 手动触发：`/review` 或 `@review-code`

### 问题：C 盘空间不足

```bash
# 清理 npm 缓存
npm cache clean --force

# 检查 npm 全局位置
npm config get prefix
# 应为 E:\npm-global
```

---

## 17. 架构决策记录

### 决策 1：从 Claude Code 迁移到 OpenCode

**原因**：
- OpenCode 原生支持 75+ provider，无需自建 bridge_server
- 扩展点系统（tools/agents/commands/skills）替代自定义 hook + bridge
- 186k stars，社区活跃，长期维护有保障
- 支持 TUI + IDE 双模式

### 决策 2：保留 Python 后端

**原因**：
- `recognize.py` 已稳定，直接调用 SiliconFlow API
- `daemon.py` 使用 Playwright 持久化 Firefox profile，保留登录态
- 用 TypeScript 重写无收益，通过 `Bun.$` 调用 Python 即可
- 已用 `urllib.request` 替代 `requests`，零外部依赖

### 决策 3：3 Subagent 替代 Flash 1/2/4

**原因**：
- OpenCode Subagent 原生支持 `deny edit/write/bash` 权限
- 3 个 Subagent 独立调用 Flash 模型，互不干扰
- 通过 AGENTS.md 强约束触发，替代 PostToolUse hook

### 决策 4：API Key 通过环境变量加载

**原因**：
- OpenCode `{env:VAR}` 支持环境变量替换
- `{file:path}` 不支持 JSON 字段提取
- `start-opencode.bat` 从 secrets.json 读取并设置环境变量，兼顾安全与便利

### 决策 5：移除后台服务

**原因**：
- 原架构的 bridge_server（11440）+ tool_registry（11442）是 Claude Code 限制的变通方案
- OpenCode 原生集成工具调用，无需中间层
- 减少进程数，降低维护成本

### 决策 6：三层搜索架构（全免费）

**原因**：
- Brave Search 需付费 API key
- DuckDuckGo + SearXNG + Google 三层覆盖，质量递增
- 全部免费、无 API key、无需自建服务
- 国内外可用性已评估

### 决策 7：67 Skills 通过 Directory Junctions 暴露

**原因**：
- OpenCode 默认扫描 `.opencode/skills/<name>/SKILL.md`（一层深度）
- 克隆的仓库结构是三层深度
- Junction 是 Windows 原生机制，零性能损失，跨进程可见
- 脚本可重跑，新增 skill 自动发现

### 决策 8：移除 Brave Search，改用免费替代

**原因**：
- Brave Search API 每月有免费额度限制，超限付费
- 免费替代方案质量已足够：DuckDuckGo（1.3K stars）+ SearXNG（70+ 引擎元搜索）+ g-search（Google Playwright）
- 减少 API key 管理负担

---

## 18. 文件索引

### 核心配置

| 文件 | 用途 |
|------|------|
| `~/.config/opencode/opencode.json` | 全局 OpenCode 配置 |
| `opencode.json` | 项目级 OpenCode 配置（13 MCP + 3 plugin） |
| `AGENTS.md` | OpenCode 行为约束（instructions） |
| `start-opencode.bat` | Windows 启动脚本 |
| `markconfig/secrets.json` | API Keys |
| `markconfig/profile.md` | 个人画像 |
| `markconfig/paths.json` | 系统路径 |

### 扩展点

| 路径 | 用途 |
|------|------|
| `.opencode/tools/vision.ts` | 视觉识别工具 |
| `.opencode/tools/browser.ts` | 浏览器工具组（9 个工具） |
| `.opencode/agents/review-code.md` | 代码审查 subagent |
| `.opencode/agents/review-structure.md` | 结构审查 subagent |
| `.opencode/agents/review-risk.md` | 风险审查 subagent |
| `.opencode/commands/doctor.md` | 健康诊断命令 |
| `.opencode/commands/review.md` | 审查管道命令 |
| `.opencode/commands/handoff.md` | 交接管理命令 |
| `.opencode/commands/browser.md` | 浏览器启动命令 |
| `.opencode/skills/memory-context/SKILL.md` | 记忆索引 skill（原生） |
| `.opencode/skills/<prefix>-<skill>/` | 67 个 junction skills |

### Python 后端

| 路径 | 用途 |
|------|------|
| `modules/vision/recognize.py` | 图像/PDF 识别（SiliconFlow Qwen3-VL-Plus） |
| `modules/vision/clipboard.py` | 剪贴板图片识别 |
| `modules/vision/manifest.json` | 模块清单 |
| `modules/browser/daemon.py` | Playwright Firefox HTTP Daemon |

### 记忆与运行时

| 路径 | 用途 |
|------|------|
| `_data/memory/MEMORY.md` | 核心记忆汇总 |
| `_data/memory/user-*.md` | 分类用户记忆（7 个文件） |
| `_data/memory/README-INIT.md` | 记忆系统说明 |
| `_runtime/handoff/LATEST.md` | 最新交接包 |
| `_runtime/handoff/context_handoff_*.md` | 历史交接包 |
| `_runtime/mcp-memory.json` | Memory MCP 数据 |
| `_runtime/mcp-sqlite.db` | SQLite MCP 数据库 |
| `_runtime/vision_usage.jsonl` | 视觉模型使用日志 |
| `_runtime/create-skill-junctions.ps1` | junction 生成脚本 |
| `_runtime/cleanup-legacy.ps1` | 清理脚本 |

### 文档

| 路径 | 用途 |
|------|------|
| `README.md` | 本文件 |
| `INIT_REPORT.md` | 迁移完成报告 |
| `_docs/PROJECT_DOC.md` | 详细架构文档 |
| `markconfig/README.md` | markconfig 目录说明 |

---

## 19. 附录：架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                    OpenCode CLI / TUI                            │
│           (终端 + IDE 模式 + headless run)                       │
└─────────────────────┬───────────────────────────────────────────┘
                      │ opencode.json (provider + permission + 13 MCP + 3 plugin)
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│              Provider 层（AI SDK 75+ 厂商）                      │
│                                                                 │
│   DeepSeek V4 Pro    →  主脑（Build agent）                      │
│   DeepSeek V4 Flash  →  审查/小型任务（Subagents）                │
│   SiliconFlow        →  Qwen3-VL-Plus 视觉模型                   │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              MCP 层（13 个 Servers）                             │
│                                                                 │
│  ┌─ 官方 ─────────────────────────────────────────────────────┐ │
│  │ filesystem │ github │ context7 │ sequential-thinking        │ │
│  │ memory     │ playwright │ fetch │ sqlite │ time │ git       │ │
│  └─────────────────────────────────────────────────────────────┘ │
│  ┌─ 搜索（全免费）─────────────────────────────────────────────┐ │
│  │ duckduckgo (DuckDuckGo)                                     │ │
│  │ searxng (70+ 元搜索，用 searx.be 公共实例)                   │ │
│  │ g-search (Google 直查，Playwright 抓取)                      │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              Plugin 层（3 个）                                   │
│                                                                 │
│   oh-my-opencode      →  Agent 编排、多模型协作                  │
│   opencode-dcp        →  动态上下文裁剪                          │
│   antigravity-auth    →  增强认证流                              │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              扩展层（.opencode/）                                │
│                                                                 │
│   tools/*.ts         →  10 个工具（vision + browser×9）          │
│   agents/*.md        →  3 个 Subagents（review-code/structure/risk）│
│   commands/*.md      →  4 个 Slash commands                      │
│   skills/            →  67 个 skills（4 仓库 + junctions）       │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              Python 模块层（modules/）                           │
│                                                                 │
│   vision/recognize.py  →  图像/PDF 识别（Qwen3-VL-Plus）        │
│   browser/daemon.py    →  Playwright Firefox Daemon (9223)      │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│              配置与记忆层                                        │
│                                                                 │
│   markconfig/         →  API Keys + 个人画像 + 路径             │
│   _data/memory/       →  用户记忆文件（7 个分类文件）           │
│   _runtime/           →  交接包 + MCP 数据 + 日志               │
│   AGENTS.md           →  OpenCode 全局行为约束                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 20. 附录：迁移前后对比

| 维度 | Claude Code (旧) | OpenCode (新) |
|------|------------------|---------------|
| 主脑 | DeepSeek V4 Pro via bridge_server | DeepSeek V4 Pro via OpenCode provider |
| 审查 | Flash 1/2 (PostToolUse) + Dynamic Flash (Stop) | 3 subagents (review-code/structure/risk) |
| 触发 | 自动 hook | `/review` 命令 + AGENTS.md 约束 |
| 视觉 | `api.registry.get_vision()` | `recognize.py` 直接调用 SiliconFlow |
| 浏览器 | Marionette (2828) + Daemon (9223) | Daemon (9223) + 9 个 TS 工具 + Playwright MCP |
| 后台服务 | bridge_server + tool_registry | 无（OpenCode 原生） |
| MCP Servers | 0 | 13（filesystem, github, context7, 3 搜索, 等） |
| Plugins | 0 | 3（oh-my-opencode, dcp, antigravity-auth） |
| Skills | 0 | 67（4 个 GitHub 仓库） |
| 搜索能力 | 无内置 | 三层架构（DuckDuckGo + SearXNG + Google） |
| 状态行 | `combined_status.py` (3 秒) | OpenCode TUI 内置 |
| 配置 | `_config/settings.json` | `opencode.json` 分层 |
| 行为约束 | `CLAUDE.md` | `AGENTS.md` (instructions) |
| 扩展语言 | Python | TypeScript (tools) + Markdown (agents/commands) |
| Python 依赖 | requests + 多个自定义模块 | 标准库 urllib（零外部 HTTP 依赖） |

---

**End of README**
