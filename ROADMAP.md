# AgentForge — Roadmap

> **项目愿景**：从单一 Agent 工具进化为多 Agent 协同操作系统，最终成为个人 AI 工作站
> **当前版本**：v1.0（OpenCode + 13 MCP + 3 Plugins + 67 Skills）
> **目标版本**：v3.0（Agent 集群 + 本地应用自动化 + 自进化提示词工程）
> **最后更新**：2026-07-19

---

## 目录

1. [现状评估](#1-现状评估)
2. [v1.5 — 提示词工程深化](#2-v15--提示词工程深化)
3. [v2.0 — Agent 集群架构](#3-v20--agent-集群架构)
4. [v2.5 — 本地应用自动化](#4-v25--本地应用自动化)
5. [v3.0 — 自进化系统](#5-v30--自进化系统)
6. [技术栈演进路线](#6-技术栈演进路线)
7. [里程碑与优先级](#7-里程碑与优先级)
8. [风险评估](#8-风险评估)

---

## 1. 现状评估

### 1.1 当前能力矩阵

| 能力 | 等级 | 现状 |
|------|------|------|
| 单 Agent 编程 | ★★★★★ | DeepSeek V4 Pro + 1M context |
| 代码审查 | ★★★★☆ | 3 subagent 串联（code/structure/risk） |
| 视觉识别 | ★★★★☆ | Qwen3-VL-Plus + PDF 转换 |
| 浏览器自动化 | ★★★☆☆ | Playwright + 自定义 Daemon |
| MCP 生态 | ★★★★★ | 13 个 MCP servers |
| Skills 库 | ★★★★☆ | 67 个 skills（4 个仓库） |
| 上下文管理 | ★★★☆☆ | `/handoff` 手动 + DCP 自动裁剪 |
| 用户个性化 | ★★★★★ | 7 个记忆文件 + AGENTS.md |
| Agent 协作 | ★★☆☆☆ | 仅主 agent + 审查 subagent |
| 本地应用控制 | ★☆☆☆☆ | 仅浏览器 |
| 提示词工程 | ★★★☆☆ | AGENTS.md 静态约束 |
| 自我进化 | ★☆☆☆☆ | 无 |

### 1.2 核心瓶颈

1. **Agent 单体架构**：所有任务由主 agent 串行处理，无法并行
2. **提示词静态化**：AGENTS.md 写死，无法根据任务类型动态调整
3. **本地能力受限**：仅能操作浏览器，无法控制其他桌面应用
4. **记忆被动检索**：用户记忆文件需手动调用，无主动关联
5. **审查后置**：代码写完才审查，无法实时干预

---

## 2. v1.5 — 提示词工程深化

**目标**：从静态 AGENTS.md 进化为动态、可组合、可学习的提示词系统
**预计周期**：1-2 个月

### 2.1 动态提示词组装器（Prompt Composer）

**当前**：`instructions: ["AGENTS.md", "markconfig/profile.md"]` 静态加载

**目标**：根据任务类型动态组装 system prompt

**实现方案**：

```
.opencode/prompts/
├── base.md                  # 基础约束（替代 AGENTS.md 通用部分）
├── tasks/
│   ├── coding.md            # 编程任务提示词
│   ├── review.md            # 审查任务提示词
│   ├── research.md          # 研究任务提示词
│   ├── writing.md           # 写作任务提示词
│   └── automation.md        # 自动化任务提示词
├── profiles/
│   ├── terse.md             # 简洁模式
│   ├── detailed.md          # 详尽模式
│   └── socratic.md          # 苏格拉底模式（引导思考）
└── composer.md              # 组装规则
```

**触发机制**：
- 主 agent 识别任务类型 → 自动加载对应 prompts/tasks/*.md
- 用户可显式指定：`/mode coding`、`/mode research`
- AGENTS.md 保留为最高优先级约束

### 2.2 Few-Shot 示例库

**目标**：为常见任务提供高质量示例，提升输出一致性

**实现**：

```
.opencode/examples/
├── python/
│   ├── refactor-extract-method.md
│   ├── bug-fix-pattern.md
│   └── test-driven.md
├── typescript/
│   ├── react-component.md
│   └── api-endpoint.md
└── review/
    ├── security-checklist.md
    └── performance-pattern.md
```

**集成方式**：在 prompts/tasks/coding.md 中引用：

```markdown
## Reference Examples
- Refactoring: @examples/python/refactor-extract-method.md
- TDD: @examples/python/test-driven.md
```

### 2.3 提示词版本化与 A/B 测试

**目标**：量化提示词效果，持续优化

**实现**：
- 每次修改 AGENTS.md 或 prompts/ 时，记录版本号
- 通过 `/review` 输出的 Score 作为质量指标
- 建立 `prompt_experiments.jsonl` 记录版本 + 任务 + Score + Findings

```
_runtime/prompt_experiments.jsonl
{"version":"v1.0","task":"refactor","score":8.2,"findings":2,"timestamp":"..."}
{"version":"v1.1","task":"refactor","score":8.5,"findings":1,"timestamp":"..."}
```

### 2.4 Context-Aware 提示词注入

**当前**：所有任务加载相同 instructions

**目标**：根据上下文动态注入

| 触发条件 | 注入内容 |
|---------|---------|
| 修改 Python 文件 | Python 风格指南 + 项目惯例 |
| 修改 .ts 文件 | TypeScript 严格模式规则 |
| 涉及 markconfig/ | 安全警告 + 不修改 secrets.json |
| 涉及 _data/memory/ | 记忆文件更新规则 |
| 长会话（>50 轮） | 上下文裁剪策略 + handoff 提示 |
| 跨目录操作 | filesystem MCP 使用提示 |

---

## 3. v2.0 — Agent 集群架构

**目标**：从单 agent + 审查 subagent 进化为多 agent 协同集群
**预计周期**：3-4 个月

### 3.1 Agent 角色定义

```
┌─────────────────────────────────────────────────────────┐
│                  Orchestrator Agent                      │
│  (主调度器，DeepSeek V4 Pro，决定任务分配)               │
└──────┬──────┬──────┬──────┬──────┬──────┬──────┬───────┘
       │      │      │      │      │      │      │
       ▼      ▼      ▼      ▼      ▼      ▼      ▼
   ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
   │Coder ││Review││Test  ││Doc   ││Research│ │Deploy│ │Data │
   │Agent ││Agent ││Agent ││Agent ││Agent ││Agent ││Agent │
   └──────┘└──────┘└──────┘└──────┘└──────┘└──────┘└──────┘
```

| Agent | 模型 | 职责 | 权限 |
|-------|------|------|------|
| Orchestrator | V4 Pro | 任务分解、分配、汇总 | 全权限 |
| Coder | V4 Pro | 编码实现 | edit/write/bash |
| Reviewer | V4 Flash | 代码审查（code/structure/risk 三合一） | deny all |
| Tester | V4 Flash | 编写测试、运行测试、覆盖率分析 | bash (仅测试命令) |
| Doc Writer | V4 Flash | 文档撰写、API 文档生成 | write (仅 .md) |
| Researcher | V4 Flash | 搜索、文档查询、技术调研 | bash (只读) |
| Deployer | V4 Flash | 部署、CI/CD 触发 | bash (受控白名单) |
| Data Analyst | V4 Pro | 数据分析、可视化、报表 | bash (Python/R) |

### 3.2 通信协议

**Agent 间通信**通过共享文件系统 + 消息队列：

```
_runtime/agent_messages/
├── orchestrator/           # 主调度器消息
│   ├── tasks.jsonl         # 任务队列
│   └── results.jsonl       # 结果汇总
├── coder/
│   ├── inbox.jsonl         # 接收的任务
│   └── outbox.jsonl        # 完成的结果
└── ... (其他 agent)
```

**消息格式**：

```json
{
  "msg_id": "uuid",
  "from": "orchestrator",
  "to": "coder",
  "task_id": "task-001",
  "type": "assign",
  "content": {
    "action": "implement",
    "spec": "实现用户认证模块",
    "files": ["auth/login.py", "auth/session.py"],
    "constraints": ["使用 bcrypt", "支持 OAuth2"]
  },
  "timestamp": "2026-08-01T10:00:00Z"
}
```

### 3.3 任务分解策略

**Orchestrator 决策树**：

```
用户请求 → 任务类型识别
    │
    ├─ 简单 Q&A → 直接回答（不分配）
    │
    ├─ 单文件修改 → Coder Agent
    │
    ├─ 多文件功能 → Coder + Tester
    │   ├─ Coder 实现功能
    │   └─ Tester 编写测试
    │
    ├─ 代码审查 → Reviewer
    │
    ├─ 技术调研 → Researcher
    │   └─ 可选：Doc Writer 输出报告
    │
    ├─ Bug 修复 → Researcher (定位) + Coder (修复) + Tester (验证)
    │
    ├─ 重构 → Reviewer (评估) + Coder (执行) + Tester (回归测试)
    │
    └─ 部署 → Coder (修复) + Tester (验证) + Deployer (部署)
```

### 3.4 并行执行

利用 oh-my-opencode plugin 的异步子 agent 能力：

```typescript
// 伪代码：并行启动多个 agent
const [reviewResult, testResult, docResult] = await Promise.all([
  invokeSubagent("reviewer", { files: modifiedFiles }),
  invokeSubagent("tester", { files: modifiedFiles }),
  invokeSubagent("doc-writer", { files: modifiedFiles }),
])
```

### 3.5 Agent 状态持久化

```
_runtime/agent_state/
├── orchestrator.json       # 主调度器状态
├── coder.json              # Coder 当前任务
├── reviewer.json           # Reviewer 审查历史
└── ...
```

支持 Agent 崩溃后恢复、任务断点续传。

---

## 4. v2.5 — 本地应用自动化

**目标**：从仅控制浏览器扩展为控制本地大部分应用
**预计周期**：2-3 个月

### 4.1 自动化能力分层

```
┌─────────────────────────────────────────────────────┐
│  Layer 4: 自然语言指令                              │
│  "打开微信发送消息给张三"                            │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  Layer 3: Agent 决策层                              │
│  解析意图 → 选择应用 → 规划操作序列                 │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  Layer 2: 应用控制器（per-app adapter）             │
│  WeChat / Office / IDE / File Explorer / ...        │
└────────────────────┬────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────┐
│  Layer 1: 系统操作原语                              │
│  键盘 / 鼠标 / 窗口 / 进程 / 剪贴板 / 截图 / OCR   │
└─────────────────────────────────────────────────────┘
```

### 4.2 Layer 1 — 系统操作原语

**实现**：Python 后端模块，统一 HTTP API

```
modules/system/
├── input.py            # 键盘鼠标（pyautogui）
├── window.py           # 窗口管理（pywin32）
├── process.py          # 进程管理（psutil）
├── clipboard.py        # 剪贴板（已有，扩展）
├── screen.py           # 截图 + OCR（PIL + Tesseract）
└── file_ops.py         # 文件操作（增强版）
```

**HTTP API**（扩展 Browser Daemon 模式）：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/input/keys` | POST | 模拟键盘输入 |
| `/input/hotkey` | POST | 组合键（Ctrl+C 等） |
| `/input/click` | POST | 鼠标点击（坐标/图像匹配） |
| `/input/scroll` | POST | 滚轮 |
| `/input/drag` | POST | 拖拽 |
| `/window/list` | GET | 列出所有窗口 |
| `/window/focus` | POST | 聚焦窗口 |
| `/window/close` | POST | 关闭窗口 |
| `/window/screenshot` | POST | 窗口截图 |
| `/process/list` | GET | 列出进程 |
| `/process/start` | POST | 启动进程 |
| `/process/kill` | POST | 终止进程 |
| `/screen/screenshot` | POST | 全屏截图 |
| `/screen/ocr` | POST | OCR 识别屏幕区域 |
| `/screen/find` | POST | 图像匹配定位 |
| `/clipboard/get` | GET | 读取剪贴板 |
| `/clipboard/set` | POST | 写入剪贴板 |

### 4.3 Layer 2 — 应用控制器

**实现**：每个应用一个 adapter，封装该应用的常用操作

```
.opencode/tools/apps/
├── wechat.ts           # 微信自动化
├── vscode.ts           # VS Code 自动化
├── office.ts           # Word/Excel/PowerPoint
├── explorer.ts         # 文件资源管理器
├── browser.ts          # 浏览器（已有，迁移）
├── terminal.ts         # 终端
├── obsidian.ts         # Obsidian
└── ...
```

**示例 — 微信自动化**：

```typescript
// .opencode/tools/apps/wechat.ts
export const wechat_send_message = tool({
  description: "Send a message to a WeChat contact",
  args: {
    contact: tool.schema.string().describe("Contact name or search keyword"),
    message: tool.schema.string().describe("Message content"),
  },
  async execute(args) {
    // 1. 聚焦微信窗口
    await sysCall("window/focus", { title: "微信" })
    // 2. 搜索联系人
    await sysCall("input/hotkey", { keys: ["ctrl", "f"] })
    await sysCall("input/keys", { text: args.contact })
    await sysCall("input/keys", { key: "enter" })
    // 3. 输入消息
    await sysCall("input/keys", { text: args.message })
    await sysCall("input/keys", { key: "enter" })
    return `Message sent to ${args.contact}`
  },
})
```

### 4.4 Layer 3 — Agent 决策

**实现**：扩展 AGENTS.md + 新的 Skill

**新增 Skill**：`automation-planner`

```markdown
# Automation Planning Skill

## When to Use
- User requests operation on local apps
- User describes a multi-step GUI workflow

## Planning Procedure
1. Identify target application(s)
2. Decompose task into atomic operations
3. Map operations to system primitives
4. Add verification steps (screenshot + OCR)
5. Plan rollback for failures

## Application Registry
- WeChat: send message, read messages, send file
- VS Code: open file, run command, install extension
- Office: create document, edit, save, export PDF
- File Explorer: navigate, copy, move, rename
- Browser: navigate, click, type (existing)
```

### 4.5 安全模型

**白名单应用**：仅允许操作预设应用列表

```json
// markconfig/automation_whitelist.json
{
  "allowed_apps": ["WeChat", "VSCode", "Explorer", "Firefox"],
  "denied_apps": ["Taskmgr", "Regedit", "Cmd"],
  "allowed_operations": ["keys", "click", "screenshot", "ocr"],
  "denied_operations": ["kill_process", "modify_registry"],
  "sensitive_paths": [
    "C:/Windows/System32",
    "C:/Users/mingy/AppData/Roaming"
  ]
}
```

**确认机制**：危险操作前必须用户确认（如发送消息、删除文件、启动外部程序）。

---

## 5. v3.0 — 自进化系统

**目标**：Agent 能从交互中学习，持续优化自身
**预计周期**：6+ 个月

### 5.1 交互历史分析

**实现**：记录所有用户交互 + Agent 决策 + 结果

```
_runtime/learning/
├── interactions.jsonl       # 用户交互记录
├── decisions.jsonl          # Agent 决策记录
├── outcomes.jsonl           # 决策结果（成功/失败/用户修正）
└── patterns.json            # 提取的模式
```

**记录格式**：

```json
{
  "interaction_id": "uuid",
  "timestamp": "2026-10-01T10:00:00Z",
  "user_request": "修复 auth.py 的登录 bug",
  "agent_decision": {
    "task_type": "bug_fix",
    "agents_invoked": ["researcher", "coder", "tester"],
    "tools_used": ["read", "edit", "bash", "review-code"]
  },
  "outcome": {
    "success": true,
    "user_satisfied": true,
    "duration_seconds": 180,
    "review_score": 8.5
  },
  "user_feedback": "perfect, no notes"
}
```

### 5.2 模式抽取

**目标**：从历史交互中抽取成功模式

**抽取维度**：

| 维度 | 模式示例 |
|------|---------|
| 任务类型识别 | "修复 bug" → 优先调用 Researcher 定位 |
| 工具选择 | Python 文件 → 优先用 read + edit，不用 write |
| Agent 协作 | 多文件修改 → Coder + Tester 并行 |
| 提示词效果 | terse mode + coding task → Score 8.7 |
| 错误模式 | "fail to compile" → 自动检查依赖 |

**实现**：每周/每月运行模式抽取脚本

```python
# modules/learning/extract_patterns.py
def extract_patterns():
    interactions = load_interactions()
    patterns = {
        "task_type_to_agents": {},  # 任务类型 → 最优 agent 组合
        "task_type_to_tools": {},   # 任务类型 → 最常用工具
        "prompt_to_score": {},      # 提示词版本 → 平均 Score
    }
    # ... 分析逻辑
    save_patterns(patterns)
```

### 5.3 自适应提示词

**目标**：基于抽取的模式自动调整提示词

**示例**：
- 发现 "Python 重构任务 + socratic mode" Score 平均 9.1
- 自动在 prompts/tasks/refactor.md 中加入 `@profiles/socratic.md`
- 下次重构任务自动使用 socratic mode

### 5.4 失败学习

**目标**：从失败中学习，避免重复错误

**实现**：

```
_runtime/learning/failures/
├── 2026-10/
│   ├── 20261001_001_failure.md   # 失败案例
│   └── 20261001_002_failure.md
└── lessons_learned.md            # 总结的教训
```

**失败案例格式**：

```markdown
# Failure Case 001 — 2026-10-01

## Task
修复 auth.py 的 SQL 注入漏洞

## What Went Wrong
Agent 使用了字符串拼接修复，引入了新的注入点

## Root Cause
- AGENTS.md 未强调 SQLAlchemy 的参数化查询
- 缺少 SQL 安全审查 checklist

## Lesson Learned
- 在 prompts/tasks/security.md 中加入 SQL 注入 checklist
- review-risk subagent 应增加 SQL 注入专项检查

## Action Taken
- 已更新 prompts/tasks/security.md
- 已更新 .opencode/agents/review-risk.md
```

### 5.5 主动学习

**目标**：Agent 主动询问用户偏好，完善用户画像

**触发条件**：
- 用户连续 3 次修正同类错误
- 用户表达不满意
- 遇到不确定的偏好（如代码风格选择）

**示例**：

```
Agent: 我注意到您最近 3 次让我使用 snake_case 命名 Python 变量，
       但我之前用的是 camelCase。是否应该将 snake_case 作为
       您的默认偏好？

User: 是的

Agent: 已更新 _data/memory/user-tech-stack.md，添加：
       "Python 变量命名偏好：snake_case"
```

---

## 6. 技术栈演进路线

### 6.1 v1.5 新增

| 技术 | 用途 | 优先级 |
|------|------|--------|
| Prompt Composer 模块 | 动态提示词组装 | 高 |
| A/B 测试框架 | 提示词效果量化 | 中 |

### 6.2 v2.0 新增

| 技术 | 用途 | 优先级 |
|------|------|--------|
| oh-my-opencode 深度集成 | Agent 编排 | 高 |
| 消息队列（基于文件） | Agent 间通信 | 高 |
| Agent 状态持久化 | 崩溃恢复 | 中 |
| 任务 DAG 调度器 | 并行任务编排 | 中 |

### 6.3 v2.5 新增

| 技术 | 用途 | 优先级 |
|------|------|--------|
| pyautogui | 键盘鼠标控制 | 高 |
| pywin32 | Windows 窗口管理 | 高 |
| Tesseract OCR | 屏幕文字识别 | 高 |
| OpenCV (cv2) | 图像匹配定位 | 中 |
| psutil | 进程管理 | 中 |
| 应用 adapter 框架 | per-app 自动化 | 高 |

### 6.4 v3.0 新增

| 技术 | 用途 | 优先级 |
|------|------|--------|
| 交互历史存储 | 学习数据源 | 高 |
| 模式抽取算法 | 自适应基础 | 高 |
| 向量数据库（Chroma/FAISS） | 语义检索历史 | 中 |
| 评估框架 | Agent 性能量化 | 中 |

---

## 7. 里程碑与优先级

### 7.1 优先级矩阵

| 优先级 | 版本 | 功能 | 价值 | 实现难度 |
|--------|------|------|------|---------|
| P0 | v1.5 | 动态提示词组装 | 立即提升所有任务质量 | 低 |
| P0 | v2.0 | Agent 集群（Coder+Tester+Reviewer） | 并行化，效率翻倍 | 中 |
| P1 | v2.5 | 系统操作原语 | 解锁本地自动化基础 | 中 |
| P1 | v2.5 | 微信/VSCode adapter | 高频场景价值 | 中 |
| P1 | v2.0 | 任务分解策略 | 复杂任务自动化 | 中 |
| P2 | v1.5 | A/B 测试框架 | 量化提示词效果 | 中 |
| P2 | v3.0 | 交互历史分析 | 自进化基础 | 高 |
| P2 | v2.5 | OCR + 图像匹配 | GUI 自动化关键 | 中 |
| P3 | v3.0 | 模式抽取 | 自适应提示词 | 高 |
| P3 | v3.0 | 主动学习 | 个性化深化 | 高 |

### 7.2 推荐实施顺序

```
v1.5（1-2 月）
├─ Week 1-2: Prompt Composer 框架
├─ Week 3-4: 任务类型识别 + 动态加载
├─ Week 5-6: Few-Shot 示例库
└─ Week 7-8: A/B 测试框架

v2.0（3-4 月）
├─ Month 1: Agent 角色定义 + 通信协议
├─ Month 2: Orchestrator 实现 + 任务分解
├─ Month 3: 并行执行 + 状态持久化
└─ Month 4: 测试 + 优化

v2.5（2-3 月）
├─ Month 1: Layer 1 系统原语
├─ Month 2: Layer 2 应用 adapter（WeChat/VSCode/Office）
└─ Month 3: Layer 3 Agent 决策 + 安全模型

v3.0（6+ 月）
├─ Month 1-2: 交互历史存储 + 分析
├─ Month 3-4: 模式抽取算法
├─ Month 5-6: 自适应提示词
└─ Month 7+: 主动学习 + 失败学习
```

---

## 8. 风险评估

### 8.1 技术风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| OpenCode 版本 breaking change | 中 | 高 | 锁定版本 + 关注 release notes |
| DeepSeek API 限流 | 中 | 中 | 多 provider fallback（已支持 75+） |
| Agent 集群复杂度过高 | 高 | 高 | 渐进式引入，先 2 agent 后多 agent |
| 本地自动化误操作 | 高 | 高 | 白名单 + 确认机制 + 沙箱 |
| 提示词爆炸（过多变体） | 中 | 中 | 版本化 + A/B 测试 + 自动淘汰 |

### 8.2 安全风险

| 风险 | 缓解 |
|------|------|
| 本地应用自动化被滥用 | 白名单应用 + 用户确认 + 操作日志 |
| Agent 集群权限失控 | 最小权限原则 + 每个 Agent 独立 permission |
| 自进化系统学到错误模式 | 人工审核 patterns + 回滚机制 |
| API Key 泄露 | secrets.json 不提交 + 环境变量传递 |
| 交互历史泄露隐私 | 历史数据本地存储 + 加密 + 不上传 |

### 8.3 维护风险

| 风险 | 缓解 |
|------|------|
| 单人维护负担 | 模块化设计 + 文档完善 + 自动化测试 |
| 外部依赖（MCP/Plugin）失效 | 优先使用稳定官方包 + 本地 fallback |
| Skills 仓库 drift | 定期 git pull + 自动化测试 |
| 个人画像过时 | 季度审查 + 主动学习机制（v3.0） |

---

## 附录：成功指标

### v1.5 KPI
- 提示词版本数 ≥ 5
- 平均 review Score 提升 ≥ 0.5
- 任务类型识别准确率 ≥ 90%

### v2.0 KPI
- Agent 数量 ≥ 5
- 并行任务比例 ≥ 30%
- 任务完成时间减少 ≥ 40%

### v2.5 KPI
- 可控应用数 ≥ 5
- 自动化流程数 ≥ 10
- 用户确认率 ≤ 30%（70% 自动执行）

### v3.0 KPI
- 交互历史 ≥ 1000 条
- 抽取模式 ≥ 50 个
- 自适应提示词覆盖率 ≥ 80%
- 用户修正率下降 ≥ 50%

---

**End of Roadmap**
