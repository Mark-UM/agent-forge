# Mark AI 基础设施 · OpenCode 架构文档

> 项目路径：`E:/system_folder/.claude/.claude`
> 最后更新：2026-07-17
> 所有者：`韩铭洋（Mark）`
> AI Agent：`OpenCode` + `DeepSeek V4 Pro / V4 Flash`

---

## 目录

1. [架构总览](#1-架构总览)
2. [配置层级](#2-配置层级)
3. [扩展点系统](#3-扩展点系统)
4. [3 阶段代码审查管道](#4-3-阶段代码审查管道)
5. [自定义工具](#5-自定义工具)
6. [Python 模块层](#6-python-模块层)
7. [记忆与上下文系统](#7-记忆与上下文系统)
8. [启动流程](#8-启动流程)
9. [常用命令速查](#9-常用命令速查)
10. [关键文件索引](#10-关键文件索引)

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenCode CLI / TUI                        │
│           (终端 + IDE 模式 + headless run)                   │
└─────────────────────┬───────────────────────────────────────┘
                      │ opencode.json (provider + permission)
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              Provider 层（AI SDK 75+ 厂商）                  │
│                                                             │
│   DeepSeek V4 Pro    →  主脑（Build agent）                  │
│   DeepSeek V4 Flash  →  审查/小型任务（Plan + Subagents）    │
│   SiliconFlow        →  Qwen3-VL-Plus 视觉模型               │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                  扩展层（.opencode/）                        │
│                                                             │
│   tools/*.ts       →  自定义工具（vision, browser）         │
│   agents/*.md      →  Subagents（review-code/structure/risk）│
│   commands/*.md    →  Slash commands（/doctor, /review...）  │
│   skills/*/SKILL.md →  Skills（memory-context）              │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                  Python 模块层（modules/）                   │
│                                                             │
│   vision/recognize.py  →  图像/PDF 识别（Qwen3-VL-Plus）    │
│   browser/daemon.py    →  Playwright Firefox Daemon (9223)  │
└─────────────────────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                  配置与记忆层                                │
│                                                             │
│   markconfig/         →  API Keys + 个人画像 + 路径         │
│   _data/memory/       →  用户记忆文件（profile/career/...） │
│   _runtime/handoff/   →  上下文交接包（LATEST.md）          │
│   AGENTS.md           →  OpenCode 全局行为约束              │
└─────────────────────────────────────────────────────────────┘
```

### 核心原则

| 原则 | 实现 |
|------|------|
| 单一主脑 | DeepSeek V4 Pro 作为 Build agent，全工具访问 |
| 审查分权 | Flash 模型独立审查，无编辑权限（deny edit/write/bash） |
| 工具延伸 | Python 模块通过 TypeScript tool 包装暴露给 OpenCode |
| 配置分层 | global → env → project → .opencode 内联覆盖 |
| 上下文持久 | handoff 包 + memory 文件 + AGENTS.md 引用 |

---

## 2. 配置层级

OpenCode 配置从高到低合并（高优先级覆盖低优先级）：

| 层级 | 路径 | 用途 |
|------|------|------|
| Remote | 服务端推送 | 团队共享配置（暂未启用） |
| Global | `C:\Users\mingy\.config\opencode\opencode.json` | 全局 provider + API Key + 默认模型 |
| Env | `OPENCODE_CONFIG` 环境变量 | 临时覆盖 |
| Project | `E:\system_folder\.claude\.claude\opencode.json` | 项目级覆盖 |
| Inline | `.opencode/` 目录 | 工具、agent、command、skill 定义 |

### Global Config 关键字段

```json
{
  "model": "deepseek/deepseek-v4-pro",
  "small_model": "deepseek/deepseek-v4-flash",
  "provider": {
    "deepseek": {
      "options": { "apiKey": "{env:DEEPSEEK_API_KEY}" }
    }
  },
  "permission": { "edit": "allow", "bash": "ask", "write": "allow" },
  "compaction": { "auto": true, "prune": false, "reserved": 10000 },
  "instructions": ["E:/system_folder/.claude/.claude/AGENTS.md"]
}
```

### Project Config 关键字段

```json
{
  "model": "deepseek/deepseek-v4-pro",
  "small_model": "deepseek/deepseek-v4-flash",
  "instructions": ["AGENTS.md", "markconfig/profile.md"],
  "watcher": {
    "ignore": ["node_modules/**", "plugins/cache/**", "_state/**", "_runtime/**", "__pycache__/**"]
  }
}
```

### 变量替换

- `{env:VAR_NAME}` — 读取环境变量
- `{file:path}` — 读取文件内容（不支持字段提取）

---

## 3. 扩展点系统

### 3.1 Custom Tools（`.opencode/tools/*.ts`）

TypeScript 编写，通过 `@opencode-ai/plugin` 的 `tool()` 函数注册。

```typescript
import { tool } from "@opencode-ai/plugin"
import path from "path"

export default tool({
  description: "...",
  args: { ... },
  async execute(args, context) {
    // context.worktree 是项目根目录
    return result
  },
})
```

### 3.2 Subagents（`.opencode/agents/*.md`）

Markdown frontmatter 定义元数据，正文是 system prompt。

```yaml
---
description: Reviews code for quality
mode: subagent
model: deepseek/deepseek-v4-flash
temperature: 0.1
permission:
  edit: deny
  write: deny
  bash: deny
---
You are a code reviewer...
```

调用方式：`@review-code 检查这个文件` 或由主 agent 自动调用。

### 3.3 Commands（`.opencode/commands/*.md`）

Slash 命令，frontmatter 指定执行 agent。

```yaml
---
description: Run health diagnostics
agent: build
---
Run a health check...
```

### 3.4 Skills（`.opencode/skills/*/SKILL.md`）

按需加载的领域知识包。

---

## 4. 3 阶段代码审查管道

替代原 Claude Code 的 Flash 1/2/4 系统，由 3 个 Subagent 串联执行。

```
用户触发 /review 或主 agent 主动调用
    │
    ▼
Stage 1: @review-code      (Flash 1 等价)
  - 逻辑错误 / Bug / 边界 / 命名 / 异常处理
  - 输出: PASS/FAIL + Score(1-10) + Findings
    │
    ▼
Stage 2: @review-structure (Flash 2 等价)
  - 文件组织 / 导入依赖 / 模块结构 / 命名一致性
  - 输出: PASS/FAIL + Score + Findings
    │
    ▼
Stage 3: @review-risk      (Flash 4 等价)
  - 安全漏洞 / 危险操作 / 敏感信息 / 权限提升
  - 输出: PASS/FAIL + Risk Level + Findings
```

### Subagent 配置统一约束

| 字段 | 值 |
|------|-----|
| `mode` | `subagent` |
| `model` | `deepseek/deepseek-v4-flash` |
| `temperature` | `0.1` |
| `permission.edit` | `deny` |
| `permission.write` | `deny` |
| `permission.bash` | `deny` |

### 触发方式

1. **手动**：用户输入 `/review`
2. **自动**：AGENTS.md 强约束 — 主 agent 完成代码修改后必须调用三个 subagent
3. **单独**：`@review-code` / `@review-structure` / `@review-risk` 单独调用

---

## 5. 自定义工具

### 5.1 vision 工具

**文件**：`.opencode/tools/vision.ts`
**后端**：`modules/vision/recognize.py`

调用 SiliconFlow Qwen3-VL-Plus 模型识别图片或 PDF。

```typescript
// OpenCode 内调用
vision({ files: ["screenshot.png"], prompt: "描述这张图" })
```

**铁律**：AGENTS.md 强制规定 — 任何 image/PDF 文件必须用 vision 工具，禁止用 Read。

### 5.2 browser 工具组

**文件**：`.opencode/tools/browser.ts`
**后端**：`modules/browser/daemon.py` (HTTP API on port 9223)

9 个工具：

| 工具 | 端点 | 用途 |
|------|------|------|
| `browser_navigate` | POST /navigate | 导航到 URL |
| `browser_click` | POST /click | 点击 CSS 选择器 |
| `browser_type` | POST /type | 输入文本 |
| `browser_screenshot` | POST /screenshot | 全页截图 |
| `browser_page_text` | GET /text | 提取页面文本 |
| `browser_page_content` | GET /content | 提取 HTML |
| `browser_execute_js` | POST /js | 执行 JavaScript |
| `browser_status` | GET /status | 查询当前 URL/标题 |
| `browser_scroll` | POST /scroll | 滚动页面 |

**启动**：`/browser` 命令或 `python modules/browser/daemon.py`

---

## 6. Python 模块层

仅保留与 OpenCode 工具直接对接的最小集合。

### 6.1 modules/vision/

| 文件 | 用途 |
|------|------|
| `recognize.py` | 主识别脚本，直接调用 SiliconFlow API |
| `clipboard.py` | 剪贴板图片识别（调用 recognize.py） |
| `manifest.json` | 模块清单 |

**依赖**：
- Python 3.11+（路径：`C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe`）
- PyMuPDF（PDF 识别）
- 环境变量：`SILICONFLOW_API_KEY`

**日志**：`~/.claude/_state/vision_usage.jsonl`

### 6.2 modules/browser/

| 文件 | 用途 |
|------|------|
| `daemon.py` | Playwright Firefox HTTP Daemon（端口 9223） |

**特性**：
- 使用 Firefox 持久化 profile，保留所有登录态
- 自动检测并关闭旧实例
- HTTP API，无 SDK 依赖

**依赖**：
- Playwright + Firefox
- Firefox 可执行文件：`C:\Program Files\Mozilla Firefox\firefox.exe`
- Firefox profile：`%APPDATA%\Mozilla\Firefox\Profiles\cqe4w54g.default-release`

---

## 7. 记忆与上下文系统

### 7.1 用户记忆（`_data/memory/`）

| 文件 | 内容 |
|------|------|
| `MEMORY.md` | 核心记忆汇总（身份/联系/教育/技术栈/偏好） |
| `user-profile.md` | 完整个人画像 |
| `user-career.md` | 职业规划 |
| `user-personality.md` | 性格特征 |
| `user-real-life.md` | 家庭/财务/生活 |
| `user-reply-preferences.md` | AI 回复偏好 |
| `user-tech-stack.md` | 技术栈详情 |
| `README-INIT.md` | 记忆系统初始化说明 |

### 7.2 上下文交接（`_runtime/handoff/`）

| 文件 | 用途 |
|------|------|
| `LATEST.md` | 最新交接包（任务摘要 + 进度 + 下一步） |

通过 `/handoff save` / `/handoff list` / `/handoff clean` 管理。

### 7.3 AGENTS.md

OpenCode 全局行为约束，被 global opencode.json 通过 `instructions` 字段加载。

包含：
- Top Priority Directive（用户意图优先）
- Response Style（英文为主，中文解释复杂概念）
- Workflow（TodoWrite + 并行工具调用）
- Code Review Architecture（3 阶段强制审查）
- Vision/PDF 识别铁律
- Web Search Permission
- Context Management
- Personal Profile Summary

### 7.4 markconfig/

| 文件 | 内容 |
|------|------|
| `secrets.json` | API Keys（DEEPSEEK, SILICONFLOW, ANTHROPIC_AUTH_TOKEN） |
| `profile.md` | Mark 的完整个人画像 |
| `paths.json` | 系统路径（Python, Claude Home, ChatGPT Export） |
| `README.md` | markconfig 目录说明 |

---

## 8. 启动流程

```
用户执行 start-opencode.bat
    │
    ▼
1. 从 markconfig/secrets.json 读取 API Keys
    │
    ▼
2. 设置环境变量：
   - DEEPSEEK_API_KEY
   - SILICONFLOW_API_KEY
   - PATH += E:\npm-global
    │
    ▼
3. 启动 opencode（在项目根目录）
    │
    ▼
4. OpenCode 加载配置层级：
   - Global: ~/.config/opencode/opencode.json
   - Project: ./opencode.json
   - 加载 AGENTS.md 作为 instructions
    │
    ▼
5. 注册扩展点：
   - .opencode/tools/*.ts → 自定义工具
   - .opencode/agents/*.md → Subagents
   - .opencode/commands/*.md → Slash 命令
   - .opencode/skills/*/SKILL.md → Skills
    │
    ▼
6. 主 agent (Build) 就绪，使用 DeepSeek V4 Pro
```

---

## 9. 常用命令速查

### Slash Commands

```bash
/doctor        # 健康诊断
/review        # 触发 3 阶段代码审查
/handoff       # 上下文交接管理
/browser       # 启动浏览器 Daemon
```

### Subagent 调用

```bash
@review-code 检查 auth.py
@review-structure 评估 modules/ 目录组织
@review-risk 扫描 secrets 泄露
```

### 终端直接调用

```bash
# OpenCode headless 测试
opencode run "测试 DeepSeek 连通性"

# Python 模块直接调用
python modules/vision/recognize.py screenshot.png
python modules/vision/recognize.py document.pdf
python modules/browser/daemon.py
```

### 启动

```bash
# Windows
start-opencode.bat

# 或手动
set DEEPSEEK_API_KEY=...
set SILICONFLOW_API_KEY=...
opencode
```

---

## 10. 关键文件索引

### 配置

| 文件 | 用途 |
|------|------|
| `~/.config/opencode/opencode.json` | 全局 OpenCode 配置 |
| `opencode.json` | 项目级 OpenCode 配置 |
| `AGENTS.md` | OpenCode 行为约束（instructions） |
| `markconfig/secrets.json` | API Keys |
| `markconfig/profile.md` | 个人画像 |
| `markconfig/paths.json` | 系统路径 |

### 扩展

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
| `.opencode/skills/memory-context/SKILL.md` | 记忆索引 skill |

### Python 后端

| 路径 | 用途 |
|------|------|
| `modules/vision/recognize.py` | 图像/PDF 识别（SiliconFlow Qwen3-VL-Plus） |
| `modules/vision/clipboard.py` | 剪贴板图片识别 |
| `modules/browser/daemon.py` | Playwright Firefox HTTP Daemon |

### 记忆与运行时

| 路径 | 用途 |
|------|------|
| `_data/memory/MEMORY.md` | 核心记忆汇总 |
| `_data/memory/user-*.md` | 分类用户记忆 |
| `_runtime/handoff/LATEST.md` | 最新交接包 |
| `_state/vision_usage.jsonl` | 视觉识别调用日志 |

### 启动

| 文件 | 用途 |
|------|------|
| `start-opencode.bat` | Windows 启动脚本（加载 secrets + 启动 opencode） |

---

## 附录：架构对比（Claude Code → OpenCode）

| 维度 | Claude Code (旧) | OpenCode (新) |
|------|------------------|---------------|
| 配置 | `_config/settings.json` (hooks/statusLine) | `opencode.json` 分层配置 |
| 行为约束 | `CLAUDE.md` | `AGENTS.md` (instructions 字段) |
| 工具扩展 | Python hooks + bridge | TypeScript tools + Python 后端 |
| 审查系统 | Flash 1/2 (PostToolUse) + Dynamic Flash (Stop) | 3 subagents (review-code/structure/risk) |
| 上下文交接 | handoff hook (自动 90%) | `/handoff` 命令（手动 + AGENTS.md 约束） |
| 健康诊断 | `modules/monitoring/doctor.py` (17 项) | `/doctor` 命令（LLM 自检） |
| 后台服务 | bridge_server (11440) + tool_registry (11442) | 无（OpenCode 原生集成） |
| 状态行 | `combined_status.py` (3 秒刷新) | OpenCode TUI 内置 |
| 模型 | DeepSeek V4 Pro + Flash 1/2 (独立 Key) | DeepSeek V4 Pro + Flash (统一 Key) |
| 视觉 | `api.registry.get_vision()` | `recognize.py` 直接调用 SiliconFlow |
| 浏览器 | `browser.py` (Marionette) + daemon.py | `daemon.py` (HTTP API) + browser.ts 工具组 |
