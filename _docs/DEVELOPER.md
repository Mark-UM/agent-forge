# Developer Guide

> **项目路径**：`E:/system_folder/.claude/.claude`
> **文档定位**：开发规范、扩展开发、贡献指南
> **受众**：扩展开发者 / 二次开发者
> **最后更新**：2026-07-19

---

## 目录

1. [开发环境搭建](#1-开发环境搭建)
2. [项目结构详解](#2-项目结构详解)
3. [扩展开发指南](#3-扩展开发指南)
4. [代码规范](#4-代码规范)
5. [测试策略](#5-测试策略)
6. [调试技巧](#6-调试技巧)
7. [常见扩展场景](#7-常见扩展场景)
8. [版本管理与发布](#8-版本管理与发布)

---

## 1. 开发环境搭建

### 1.1 必需工具

| 工具 | 版本 | 安装命令 | 用途 |
|------|------|---------|------|
| Node.js | 18+ | 官网下载 | OpenCode 运行时 |
| Python | 3.11+ | 官网下载 | Python 后端 |
| Bun | latest | `npm install -g bun` | TypeScript 工具运行时 |
| OpenCode | latest | `npm install -g opencode-ai` | 主体 |
| Git | latest | 官网下载 | 版本控制 |

### 1.2 Python 依赖

```bash
pip install PyMuPDF Pillow playwright mcp-server-git duckduckgo-mcp-server
playwright install firefox
```

### 1.3 验证环境

```bash
opencode --version
python --version
bun --version

# 验证 OpenCode + DeepSeek
opencode run "Reply with exactly: DEV_OK"
```

### 1.4 获取代码

本项目不使用 Git 版本控制（个人项目），所有文件直接在 `E:/system_folder/.claude/.claude/` 维护。

Skills 仓库通过 clone 获取：

```powershell
cd .opencode/skills
git clone https://ghfast.top/https://github.com/anthropics/skills.git anthropics-skills
git clone https://ghfast.top/https://github.com/obra/superpowers.git obra-superpowers
git clone https://ghfast.top/https://github.com/mattpocock/skills.git mattpocock-skills
git clone https://ghfast.top/https://github.com/vercel-labs/agent-skills.git vercel-agent-skills

# 生成 junctions
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1
```

---

## 2. 项目结构详解

```
E:/system_folder/.claude/.claude/
│
├── 配置层 ─────────────────────────────────────────
├── opencode.json              # 项目级 OpenCode 配置
├── AGENTS.md                  # 行为约束（被 instructions 加载）
├── start-opencode.bat         # 启动脚本
│
├── 扩展层 ─────────────────────────────────────────
├── .opencode/
│   ├── tools/                 # TypeScript 自定义工具
│   │   ├── vision.ts          # 视觉识别工具
│   │   └── browser.ts         # 浏览器工具组（9 个）
│   ├── agents/                # Markdown subagents
│   │   ├── review-code.md
│   │   ├── review-structure.md
│   │   └── review-risk.md
│   ├── commands/              # Slash 命令
│   │   ├── doctor.md
│   │   ├── review.md
│   │   ├── handoff.md
│   │   └── browser.md
│   ├── skills/                # Skills（67 个 junctions）
│   └── package.json           # @opencode-ai/plugin 依赖
│
├── 后端层 ─────────────────────────────────────────
├── modules/
│   ├── vision/
│   │   ├── recognize.py       # SiliconFlow 视觉 API
│   │   ├── clipboard.py       # 剪贴板识别
│   │   └── manifest.json
│   └── browser/
│       └── daemon.py          # Playwright HTTP Daemon
│
├── 数据层 ─────────────────────────────────────────
├── markconfig/
│   ├── secrets.json           # API Keys
│   ├── profile.md             # 用户画像
│   ├── paths.json             # 系统路径
│   └── README.md
├── _data/memory/              # 用户记忆（8 个文件）
│
├── 运行时 ─────────────────────────────────────────
├── _runtime/
│   ├── handoff/               # 上下文交接包
│   ├── mcp-memory.json        # Memory MCP 数据
│   ├── mcp-sqlite.db          # SQLite MCP 数据
│   ├── vision_usage.jsonl     # 视觉 API 日志
│   ├── create-skill-junctions.ps1
│   └── cleanup-legacy.ps1
│
└── 文档层 ─────────────────────────────────────────
    ├── README.md              # 项目总览
    ├── INIT_REPORT.md         # 迁移报告
    └── _docs/
        ├── TECHNICAL.md       # 技术文档
        ├── SYSTEM.md          # 系统文档
        ├── DEVELOPER.md       # 本文件
        └── PROJECT_DOC.md     # 历史架构文档
```

---

## 3. 扩展开发指南

### 3.1 新增 Custom Tool

**步骤**：

1. 在 `.opencode/tools/` 创建 `<tool-name>.ts`
2. 使用 `@opencode-ai/plugin` 的 `tool()` 函数
3. 重启 OpenCode

**模板**：

```typescript
import { tool } from "@opencode-ai/plugin"
import path from "path"

const PYTHON = "C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe"

export default tool({
  description: "Tool description. Be specific about when to use this tool.",
  args: {
    input: tool.schema.string().describe("Description of this argument"),
    optional_arg: tool.schema.number().optional().describe("Optional arg"),
  },
  async execute(args, context) {
    // context.worktree 是项目根目录
    const script = path.join(context.worktree, "modules/your_module/script.py")

    // 安全: 使用数组传参避免 shell 注入
    const result = await Bun.$`${PYTHON} ${script} ${args.input}`.text()
    return result.trim()
  },
})
```

**关键点**：
- `description` 要清晰，主 agent 根据此决定是否调用
- `args` 使用 schema 描述类型和是否可选
- `Bun.$` 模板字符串中的 `${variable}` 会自动转义
- 多个 `export const` 会注册为多个独立工具

### 3.2 新增 Subagent

**步骤**：

1. 在 `.opencode/agents/` 创建 `<name>.md`
2. Frontmatter 设置 `mode: subagent`
3. 正文写 system prompt

**模板**：

```markdown
---
description: Reviews code for <specific concern>. Use this when <trigger condition>.
mode: subagent
model: deepseek/deepseek-v4-flash
temperature: 0.1
permission:
  edit: deny
  write: deny
  bash: deny
---

You are a <specialized> reviewer.

## Your Checklist
1. Check for <issue type 1>
2. Check for <issue type 2>

## Output Format
\`\`\`
## <Agent Name> Result
**Verdict**: PASS / FAIL
**Score**: x/10
**Findings**:
1. [SEVERITY: critical/major/minor] Description
   - File: path:line
   - Suggestion: ...
\`\`\`
```

**关键点**：
- 审查类 subagent 必须 `deny edit/write/bash`
- `model` 可用 `deepseek/deepseek-v4-flash` 节省成本
- `temperature` 低（0.1-0.3）保证一致性

### 3.3 新增 Slash Command

**步骤**：

1. 在 `.opencode/commands/` 创建 `<name>.md`
2. 正文写命令行为说明

**模板**：

```markdown
---
description: Brief description of what this command does
---

# /your-command

Execute the following steps:

1. Step 1: <action>
2. Step 2: <action>

Use the `write` tool to create output at `<path>`.
```

### 3.4 新增 Skill

**原生 Skill**（推荐）：

1. 在 `.opencode/skills/<skill-name>/` 创建目录
2. 创建 `SKILL.md`

**模板**：

```markdown
---
description: When to use this skill
---

# Skill Name

## When to Use
- Scenario 1
- Scenario 2

## Procedure
1. Step 1
2. Step 2

## Examples
\`\`\`code
example
\`\`\`
```

**外部仓库 Skill**：

1. Clone 仓库到 `.opencode/skills/<repo-name>/`
2. 运行 `powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1`

### 3.5 新增 MCP Server

**步骤**：

1. 在 `opencode.json` 的 `mcp` 字段添加配置
2. 如需 API key，在 `markconfig/secrets.json` 添加
3. 在 `start-opencode.bat` 添加环境变量加载
4. 重启 OpenCode

**Local MCP 模板**（npx）：

```json
"<mcp-name>": {
  "type": "local",
  "command": ["npx", "-y", "<npm-package>"],
  "environment": {
    "API_KEY": "{env:YOUR_API_KEY}"
  },
  "enabled": true,
  "timeout": 30000
}
```

**Local MCP 模板**（Python）：

```json
"<mcp-name>": {
  "type": "local",
  "command": ["C:/Users/mingy/.../python.exe", "-m", "<module>"],
  "enabled": true,
  "timeout": 30000
}
```

**Remote MCP 模板**（SSE）：

```json
"<mcp-name>": {
  "type": "remote",
  "url": "https://mcp.example.com/sse",
  "enabled": true
}
```

### 3.6 新增 Plugin

在 `opencode.json` 的 `plugin` 数组添加 npm 包名：

```json
"plugin": [
  "oh-my-opencode@latest",
  "@tarquinen/opencode-dcp",
  "opencode-antigravity-auth@latest",
  "<new-plugin>@latest"
]
```

重启 OpenCode 后自动安装。

---

## 4. 代码规范

### 4.1 Python

- **风格**：PEP 8
- **类型注解**：可选，公共函数推荐
- **docstring**：模块级必须有，函数级推荐
- **依赖**：优先使用标准库，减少外部依赖
- **错误处理**：具体异常，不要裸 `except Exception`
- **临时文件**：必须在 `finally` 块中清理

**示例**（recognize.py 模式）：

```python
def _call_api(args):
    """调用 API，返回 (result, usage)。"""
    try:
        # ...
        return result, usage
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API 错误 {e.code}: {e.read().decode('utf-8', errors='replace')}")
```

### 4.2 TypeScript

- **风格**：ESLint 推荐
- **类型**：严格模式，但避免过度复杂泛型
- **导入**：使用 ES module `import`
- **异步**：所有 I/O 操作用 `async/await`
- **安全**：`Bun.$` 模板字符串传参，不要拼接字符串

**示例**（vision.ts 模式）：

```typescript
async execute(args, context) {
  const script = path.join(context.worktree, "modules/your/script.py")
  // 数组传参，自动转义
  const result = await Bun.$`${PYTHON} ${script} ${[...args.files, prompt]}`.text()
  return result.trim()
}
```

### 4.3 Markdown（配置文件）

- Frontmatter 使用 YAML
- 保持缩进一致
- 示例代码用三反引号 + 语言标签

### 4.4 JSON

- 2 空格缩进
- 无注释（标准 JSON）
- 数组/对象尾部无逗号

---

## 5. 测试策略

### 5.1 手动测试清单

每次修改后执行：

```bash
# 1. 配置语法
python -c "import json; json.load(open('opencode.json')); print('JSON_OK')"

# 2. Python 语法
python -m py_compile modules/vision/recognize.py
python -m py_compile modules/vision/clipboard.py
python -m py_compile modules/browser/daemon.py

# 3. OpenCode 启动
opencode run "Reply with exactly: PING"

# 4. 视觉工具
# (需要测试图片)
python modules/vision/recognize.py test.png

# 5. Browser Daemon
python modules/browser/daemon.py
# 另一终端:
curl http://127.0.0.1:9223/ping
```

### 5.2 集成测试

**MCP 连通性**：

```bash
# 启动 OpenCode 后，逐个测试 MCP
opencode run "Use the filesystem MCP to list files in E:/"
opencode run "Use the github MCP to search for 'opencode' repositories"
opencode run "Use the context7 MCP to fetch React useEffect docs"
opencode run "Use the duckduckgo MCP to search 'OpenCode'"
```

### 5.3 回归测试

修改扩展点后，运行对应命令：

| 修改内容 | 测试命令 |
|---------|---------|
| review subagent | `/review` |
| doctor command | `/doctor` |
| handoff command | `/handoff save` |
| browser command | `/browser` |
| vision tool | 提供图片路径给 OpenCode |
| browser tool | OpenCode 中调用 `navigate` |

---

## 6. 调试技巧

### 6.1 OpenCode 调试

**查看加载的配置**：

```bash
opencode run "Print your current model, permissions, and loaded instructions"
```

**查看注册的工具**：

```bash
opencode run "List all available tools"
```

**查看注册的 subagents**：

```bash
opencode run "List all available subagents"
```

### 6.2 Python 调试

**recognize.py 单步**：

```bash
# 直接调用，绕过 OpenCode
python modules/vision/recognize.py test.png "描述图片"

# 启用详细输出
python -c "
import sys
sys.argv = ['recognize.py', 'test.png', '描述图片']
exec(open('modules/vision/recognize.py').read())
"
```

**daemon.py 调试**：

```bash
# 启动 daemon
python modules/browser/daemon.py

# 另一终端测试端点
curl http://127.0.0.1:9223/ping
curl -X POST http://127.0.0.1:9223/navigate -H "Content-Type: application/json" -d '{"url":"https://example.com"}'
curl http://127.0.0.1:9223/status
```

### 6.3 MCP 调试

**查看 MCP server 日志**：

MCP server 通过 stdio 通信，日志不直接可见。可通过以下方式调试：

```bash
# 手动启动 MCP server 测试
npx -y @modelcontextprotocol/server-filesystem E:/

# 发送 JSON-RPC 请求
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | npx -y @modelcontextprotocol/server-filesystem E:/
```

### 6.4 日志分析

```bash
# 视觉 API 使用情况
Get-Content _runtime\vision_usage.jsonl | Select-Object -Last 10

# OpenCode 日志
Get-Content ~/.config/opencode/log/*.log -Tail 50
```

---

## 7. 常见扩展场景

### 7.1 添加新的代码审查维度

**场景**：添加性能审查 subagent

1. 创建 `.opencode/agents/review-perf.md`：

```markdown
---
description: Reviews code for performance issues. Use after review-code for performance-critical code.
mode: subagent
model: deepseek/deepseek-v4-flash
temperature: 0.1
permission:
  edit: deny
  write: deny
  bash: deny
---

You are a performance reviewer.

## Checklist
1. Algorithm complexity (O(n) vs O(n²))
2. Database query efficiency
3. Memory usage
4. I/O operations
5. Caching opportunities

## Output Format
## Review-Perf Result
**Verdict**: PASS / FAIL
**Score**: x/10
**Findings**:
1. [IMPACT: high/medium/low] Description
   - File: path:line
   - Suggestion: ...
```

2. 更新 `AGENTS.md` 的 Code Review Architecture 添加第 4 阶段
3. 测试：`@review-perf 检查 performance-critical.py`

### 7.2 添加新的视觉能力

**场景**：添加 OCR 识别

1. 创建 `modules/vision/ocr.py`：

```python
#!/usr/bin/env python3
"""OCR 识别 — 使用 SiliconFlow OCR 模型。"""
import sys, os, json, base64, urllib.request

# ... 类似 recognize.py 的结构
```

2. 创建 `.opencode/tools/ocr.ts`：

```typescript
import { tool } from "@opencode-ai/plugin"
import path from "path"

const PYTHON = "C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe"

export default tool({
  description: "Extract text from images using OCR...",
  args: {
    file: tool.schema.string().describe("Image file path"),
  },
  async execute(args, context) {
    const script = path.join(context.worktree, "modules/vision/ocr.py")
    const result = await Bun.$`${PYTHON} ${script} ${args.file}`.text()
    return result.trim()
  },
})
```

3. 重启 OpenCode

### 7.3 添加新的 MCP

**场景**：添加 Notion MCP

1. 在 `markconfig/secrets.json` 添加：

```json
"NOTION_API_KEY": "secret_xxx"
```

2. 在 `start-opencode.bat` 添加：

```bat
for /f "delims=" %%i in ('%PYTHON% -c "import json; d=json.load(open(r'%SECRETS_FILE%')); print(d.get('NOTION_API_KEY',''))"') do set NOTION_API_KEY=%%i
```

3. 在 `opencode.json` 的 `mcp` 添加：

```json
"notion": {
  "type": "local",
  "command": ["npx", "-y", "@modelcontextprotocol/server-notion"],
  "enabled": true,
  "timeout": 30000
}
```

4. 重启 OpenCode

### 7.4 添加新的用户记忆文件

**场景**：添加用户偏好记忆

1. 创建 `_data/memory/user-preferences.md`：

```markdown
# User Preferences

## Coding Preferences
- 偏好函数式风格
- 变量命名用 snake_case (Python) / camelCase (TS)
- ...
```

2. 更新 `.opencode/skills/memory-context/SKILL.md` 添加索引
3. 在 AGENTS.md 的 File Paths 部分提及

---

## 8. 版本管理与发布

### 8.1 版本管理策略

本项目不使用 Git 版本控制。**关键变更建议手动备份**：

```powershell
# 变更前备份
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -Path "E:\system_folder\.claude\.claude" -Destination "E:\backup\claude_$timestamp" -Recurse
```

### 8.2 变更清单模板

每次重大变更记录在 `INIT_REPORT.md`：

```markdown
## YYYY-MM-DD 变更记录

### 变更内容
- 新增 xxx
- 修改 yyy
- 删除 zzz

### 影响范围
- 配置文件: opencode.json
- 扩展点: .opencode/tools/xxx.ts
- 后端: modules/xxx/xxx.py

### 验证结果
- JSON_OK
- SYNTAX_OK
- PING (OpenCode 启动正常)
```

### 8.3 发布检查清单

重大变更后执行：

- [ ] `opencode run "Reply with exactly: PING"` 返回 PING
- [ ] `python -m py_compile` 所有 Python 文件通过
- [ ] `python -c "import json; json.load(open('opencode.json'))"` 通过
- [ ] `/doctor` 6 项全 PASS
- [ ] 视觉工具测试（提供测试图片）
- [ ] 浏览器工具测试（`/browser` + navigate）
- [ ] MCP 测试（至少测试 github + filesystem + 一个搜索 MCP）
- [ ] Skills 数量正确（`Get-ChildItem .opencode\skills -Directory | Measure-Object`）

---

**End of Developer Guide**
