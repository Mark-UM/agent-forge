# User Guide

> **项目路径**：`E:/system_folder/.claude/.claude`
> **文档定位**：日常使用指南、命令手册、常见问题
> **受众**：Mark（项目所有者）
> **最后更新**：2026-07-19

---

## 目录

1. [快速开始](#1-快速开始)
2. [日常使用](#2-日常使用)
3. [Slash 命令手册](#3-slash-命令手册)
4. [工具使用指南](#4-工具使用指南)
5. [MCP 使用指南](#5-mcp-使用指南)
6. [Skills 使用指南](#6-skills-使用指南)
7. [工作流场景](#7-工作流场景)
8. [配置管理](#8-配置管理)
9. [常见问题](#9-常见问题)
10. [维护手册](#10-维护手册)

---

## 1. 快速开始

### 1.1 启动

双击或在终端运行：

```
E:\system_folder\.claude\.claude\start-opencode.bat
```

首次启动需要 1-2 分钟（下载 MCP 依赖），后续启动 < 5 秒。

### 1.2 首次验证

启动后输入：

```
/doctor
```

应返回 6 项检查全部 PASS。

### 1.3 测试 AI 连通

```
Reply with exactly: PING
```

应返回 `PING`。

### 1.4 退出

- `Ctrl+C` 或输入 `/exit`

---

## 2. 日常使用

### 2.1 基本交互

直接输入自然语言：

```
修复 auth.py 中的登录 bug
```

```
帮我写一个 Python 脚本，监控指定目录的文件变化
```

```
解释这段代码的作用: <paste code>
```

### 2.2 代码审查（自动）

当你让 AI 修改代码后，会**自动触发** 3 阶段审查：

1. `review-code` — 代码质量
2. `review-structure` — 文件结构
3. `review-risk` — 安全风险

任何 FAIL 会自动修复并重审（最多 2 轮）。

**手动触发**：

```
/review
```

### 2.3 视觉识别

当你提供图片或 PDF 路径时，AI 会**自动调用** vision 工具：

```
识别这张图片: E:/screenshots/error.png
```

```
分析这个 PDF: E:/docs/report.pdf
```

```
描述这些图片: img1.png img2.png 这两张图有什么区别
```

### 2.4 浏览器自动化

先启动 Browser Daemon：

```
/browser
```

然后可以：

```
帮我打开 https://github.com 并截图
```

```
导航到 https://example.com，点击 "Login" 按钮，输入用户名密码
```

---

## 3. Slash 命令手册

### 3.1 `/doctor` — 健康诊断

```
/doctor
```

检查 6 项：
- DeepSeek API 连通性
- Python 可用性
- Vision 模块存在性
- Browser 模块存在性
- markconfig/ 完整性
- AGENTS.md 存在性

**使用场景**：启动后验证环境、故障排查时。

### 3.2 `/review` — 3 阶段代码审查

```
/review
```

触发 3 个 subagent 串联执行：
1. `review-code` — 逻辑/Bug/异常/边界/命名
2. `review-structure` — 文件组织/依赖/模块耦合
3. `review-risk` — 安全/危险操作/数据丢失

**输出**：PASS/FAIL + Score + Findings 列表。

**使用场景**：手动审查代码、重大变更后验证。

### 3.3 `/handoff` — 上下文交接

```
/handoff              # 保存当前上下文（等同于 /handoff save）
/handoff save         # 显式保存
/handoff list         # 列出所有历史交接包
/handoff clean        # 清理 7 天前的交接包
```

**使用场景**：
- 上下文接近上限时
- 需要切换到新会话继续任务时
- 长任务中断保存进度

**交接包位置**：`_runtime/handoff/LATEST.md`（最新）+ `context_handoff_YYYYMMDD_HHMMSS.md`（历史）。

### 3.4 `/browser` — 启动浏览器

```
/browser
```

后台启动 Playwright Firefox Daemon（端口 9223），保留所有登录态。

**使用场景**：需要浏览器自动化前先启动。

**关闭浏览器**：

```
关闭浏览器 daemon
```

AI 会调用 `/close` 端点。

---

## 4. 工具使用指南

### 4.1 vision 工具

**触发方式**：AI 自动调用（当你提到图片/PDF 时）。

**支持格式**：PNG, JPG, JPEG, WEBP, BMP, GIF, PDF。

**示例**：

```
识别 E:/photos/screenshot.png
```

```
对比这两张图: before.png after.png
```

```
分析 PDF 内容: report.pdf 总结要点
```

**注意事项**：
- PDF 会自动转为 PNG（150 DPI）
- 多图会一次性发送给视觉模型
- 使用日志：`_runtime/vision_usage.jsonl`

### 4.2 浏览器工具组（9 个）

启动 `/browser` 后可用：

| 工具 | 示例自然语言 |
|------|------------|
| `navigate` | "打开 https://github.com" |
| `click` | "点击 #login-button" |
| `type` | "在 #username 输入 'mark'" |
| `screenshot` | "截图保存到 E:/screenshot.png" |
| `page_text` | "提取页面所有文字" |
| `page_content` | "获取页面 HTML" |
| `execute_js` | "执行 JS: document.title" |
| `browser_status` | "当前页面是什么" |
| `scroll` | "滚动到页面底部" |

**安全限制**：
- 仅允许 `http`/`https` URL
- 截图只能保存到白名单目录（项目目录、Pictures、Desktop、temp）
- JS 代码长度 ≤ 100,000 字符

---

## 5. MCP 使用指南

AI 会自动选择合适的 MCP。以下是各 MCP 的典型用法：

### 5.1 文件系统 (filesystem)

```
列出 E:/ 目录下的所有文件
读取 E:/config/app.json 的内容
在 E:/notes/ 创建一个新文件 today.md
```

### 5.2 GitHub (github)

```
搜索 'opencode' 相关的 GitHub 仓库
查看 anthropics/skills 仓库的最新 release
搜索 issue: 'memory leak in react'
```

**注意**：需要 `GITHUB_PERSONAL_ACCESS_TOKEN`（已配置）。

### 5.3 实时文档查询 (context7)

```
React useEffect 的最新用法是什么
Next.js 15 的 App Router 有什么新特性
查阅 Tailwind CSS v4 的官方文档
```

**用途**：解决 DeepSeek 知识截止问题，获取最新官方文档。

### 5.4 结构化推理 (sequential-thinking)

```
设计一个分布式缓存系统，考虑一致性、可用性、分区容错
分析这个 bug 的根本原因: <描述>
```

**用途**：复杂问题分步推理。

### 5.5 持久化记忆 (memory)

```
记住: 我偏好使用 pytest 而非 unittest
查询: 我之前提过什么关于数据库的偏好吗
```

**用途**：跨会话持久化知识图谱。

**存储位置**：`_runtime/mcp-memory.json`。

### 5.6 浏览器自动化 (playwright)

与 4.2 节的 browser 工具组类似，但是 MCP 官方实现。可替代使用。

### 5.7 网页抓取 (fetch)

```
抓取 https://example.com 的内容，转为 markdown
获取 https://api.github.com/rate_limit 的 JSON 响应
```

### 5.8 SQLite 数据库 (sqlite)

```
创建一个数据库表 tasks (id, title, done)
插入一条任务: '完成项目报告'
查询所有未完成的任务
```

**存储位置**：`_runtime/mcp-sqlite.db`。

### 5.9 时间查询 (time)

```
现在 UTC 时间是多少
马来西亚时区当前时间
计算两个时间戳的差
```

### 5.10 Git 操作 (git)

```
查看当前仓库的 git status
显示最近 5 次提交
查看 main 分支和 dev 分支的差异
```

### 5.11 三层搜索

**Layer 1 — DuckDuckGo**：

```
搜索: OpenCode 最新特性
```

**Layer 2 — SearXNG 元搜索**（70+ 搜索引擎聚合）：

```
用元搜索查询: DeepSeek V4 Pro benchmark
```

**Layer 3 — Google 直查**：

```
Google 搜索: React Server Components vs Client Components
```

**AI 会自动选择**，也可手动指定。

---

## 6. Skills 使用指南

AI 会根据任务自动加载相关 skill。你也可以显式调用：

### 6.1 PDF 操作 (anthropics-pdf)

```
读取 E:/report.pdf 的内容
把这份 PDF 转为 markdown
提取 PDF 中的表格数据
```

### 6.2 Excel 操作 (anthropics-excel)

```
读取 E:/data.xlsx
创建一个新 Excel 文件，包含销售数据
把 CSV 转为 Excel
```

### 6.3 Word 操作 (anthropics-docx)

```
读取 E:/document.docx
创建一个 Word 文档，标题是"项目报告"
```

### 6.4 TDD 工作流 (obra-test-driven-development)

```
用 TDD 方式实现一个栈数据结构
先写测试，再实现功能
```

### 6.5 系统化调试 (obra-systematic-debugging)

```
调试这个 bug: <描述>
系统化分析根本原因
```

### 6.6 代码审查 (matt-code-review)

```
审查这段代码的质量: <paste code>
```

### 6.7 React 最佳实践 (vercel-react-best-practices)

```
审查我的 React 组件是否符合最佳实践
优化这个 Next.js 页面的性能
```

**完整 Skills 列表**（67 个）：

| 仓库 | Skills |
|------|--------|
| anthropics-skills | pdf, excel, docx, pptx, mcp-builder, 等 17 个 |
| obra-superpowers | test-driven-development, systematic-debugging, brainstorming, 等 14 个 |
| mattpocock-skills | code-review, tdd, domain-modeling, 等 25 个 |
| vercel-agent-skills | react-best-practices, vercel-optimize, 等 9 个 |

---

## 7. 工作流场景

### 7.1 日常编程

```
1. 启动: start-opencode.bat
2. 描述需求: "修复 auth.py 的登录 bug"
3. AI 自动修改代码
4. AI 自动触发 3 阶段审查
5. 全部 PASS → 完成
6. 退出: Ctrl+C
```

### 7.2 图像识别

```
1. 启动
2. 提供图片: "识别 E:/screenshot.png"
3. AI 调用 vision 工具
4. 返回识别结果
```

### 7.3 浏览器自动化

```
1. 启动
2. 输入: /browser (启动 Daemon)
3. 描述任务: "打开 github.com，登录，截图"
4. AI 调用浏览器工具组
5. 完成后: "关闭浏览器"
```

### 7.4 网络搜索

```
1. 启动
2. 描述: "搜索 OpenCode 最新特性，并总结"
3. AI 选择搜索 MCP (duckduckgo/searxng/g-search)
4. 获取搜索结果
5. AI 用 fetch MCP 抓取详细页面
6. AI 返回总结
```

### 7.5 GitHub 操作

```
1. 启动
2. 描述: "搜索 opencode 相关仓库，按 stars 排序"
3. AI 调用 github MCP
4. 返回仓库列表
5. 继续: "clone 第一个到 E:/projects/"
6. AI 调用 filesystem 或 bash 执行 git clone
```

### 7.6 长任务上下文交接

```
1. 任务进行中，上下文接近上限
2. 输入: /handoff save
3. AI 生成交接包到 _runtime/handoff/LATEST.md
4. 退出当前会话
5. 启动新会话
6. 输入: "读取最新的交接包，继续任务"
7. AI 读取 LATEST.md，继续任务
```

### 7.7 实时文档查询

```
1. 启动
2. 描述: "React useEffect 的最新用法"
3. AI 调用 context7 MCP
4. 获取 React 官方文档实时内容
5. AI 基于最新文档回答
```

### 7.8 数据库操作

```
1. 启动
2. 描述: "创建一个 SQLite 数据库，表 tasks (id, title, done)"
3. AI 调用 sqlite MCP
4. 数据库文件: _runtime/mcp-sqlite.db
5. 继续: "插入任务: '完成报告'，查询所有未完成任务"
```

---

## 8. 配置管理

### 8.1 修改 API Keys

编辑 `markconfig/secrets.json`：

```json
{
  "ANTHROPIC_AUTH_TOKEN": "新的 DeepSeek API Key",
  "SILICONFLOW_API_KEY": "新的 SiliconFlow API Key",
  "GITHUB_PERSONAL_ACCESS_TOKEN": "新的 GitHub Token"
}
```

重启 OpenCode 生效。

### 8.2 修改行为约束

编辑 `AGENTS.md`，例如：
- 修改 Response Style
- 添加新的审查规则
- 修改 Web Search Permission

重启 OpenCode 生效。

### 8.3 修改用户画像

编辑 `markconfig/profile.md` 或 `_data/memory/user-*.md`。

重启 OpenCode 生效（profile.md）或按需加载（memory 文件）。

### 8.4 启用/禁用 MCP

编辑 `opencode.json` 的 `mcp` 字段，设置 `"enabled": false`：

```json
"github": {
  "type": "local",
  "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
  "enabled": false,  // 禁用
  "timeout": 30000
}
```

### 8.5 修改权限

编辑 `opencode.json` 的 `permission` 字段：

```json
"permission": {
  "edit": "allow",    // allow / ask / deny
  "bash": "ask",      // allow / ask / deny
  "write": "allow"    // allow / ask / deny
}
```

- `allow` — 自动执行
- `ask` — 每次询问用户
- `deny` — 禁止

---

## 9. 常见问题

### 9.1 启动失败

**问题**：`opencode` 命令未找到

**解决**：

```
# 检查 PATH
echo %PATH%

# 确保 E:\npm-global 在 PATH 中
set PATH=E:\npm-global;%PATH%

# 或重新安装 OpenCode
npm install -g opencode-ai
```

### 9.2 DeepSeek API 连接失败

**问题**：AI 无响应或报错

**解决**：

```
# 1. 检查 API Key
type markconfig\secrets.json

# 2. 测试连通性
opencode run "Reply with exactly: PING"

# 3. 检查环境变量
echo %DEEPSEEK_API_KEY%
```

### 9.3 MCP 加载失败

**问题**：某个 MCP 不工作

**解决**：

```
# 首次启动 npx 可能超时，手动预安装:
npm install -g @modelcontextprotocol/server-github
npm install -g @modelcontextprotocol/server-filesystem
npm install -g mcp-searxng
npm install -g g-search-mcp
npm install -g @playwright/mcp

pip install mcp-server-git duckduckgo-mcp-server
```

### 9.4 GitHub MCP 认证失败

**问题**：GitHub 操作返回 401

**解决**：

1. 检查 token 是否过期: https://github.com/settings/tokens
2. 重新生成 token
3. 更新 `markconfig/secrets.json` 中的 `GITHUB_PERSONAL_ACCESS_TOKEN`
4. 重启 OpenCode

### 9.5 搜索 MCP 在国内不可用

**问题**：搜索返回空或超时

**解决**：

| MCP | 国内可用 | 替代方案 |
|-----|---------|---------|
| duckduckgo | 需代理 | 用 searxng |
| searxng | 通常可用 | — |
| g-search | 需代理 | 用 searxng |

到马来西亚后三个搜索 MCP 全部无障碍。

### 9.6 视觉工具失败

**问题**：vision 工具报错

**解决**：

```
# 1. 检查 SILICONFLOW_API_KEY
echo %SILICONFLOW_API_KEY%

# 2. 直接测试 Python
python modules/vision/recognize.py test.png

# 3. 检查 PyMuPDF (PDF 识别)
python -c "import fitz; print(fitz.__version__)"
```

### 9.7 浏览器 Daemon 无法启动

**问题**：`/browser` 命令无响应

**解决**：

```
# 1. 检查端口占用
netstat -ano | findstr :9223

# 2. 检查 Firefox 路径
dir "C:\Program Files\Mozilla Firefox\firefox.exe"

# 3. 检查 Playwright
python -c "from playwright.sync_api import sync_playwright; print('OK')"

# 4. 手动启动
python modules/browser/daemon.py
```

### 9.8 Skills 未识别

**问题**：AI 不知道某个 skill

**解决**：

```
# 重新生成 junctions
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1

# 重启 OpenCode
```

### 9.9 上下文即将用完

**问题**：AI 提示上下文不足

**解决**：

```
/handoff save
```

然后退出，重新启动，输入：

```
读取最新的交接包，继续任务
```

### 9.10 C 盘空间不足

**问题**：C 盘满

**解决**：

```
# 清理 npm 缓存
npm cache clean --force

# 检查 npm 全局位置
npm config get prefix
# 应为 E:\npm-global

# 清理 npx 缓存
Remove-Item -Recurse -Force ~/.npm/_npx
```

---

## 10. 维护手册

### 10.1 日常维护

| 任务 | 频率 | 命令 |
|------|------|------|
| 健康检查 | 每周 | `/doctor` |
| 清理交接包 | 每月 | `/handoff clean` |
| 清理视觉日志 | 日志 > 10MB | `Remove-Item _runtime\vision_usage.jsonl` |
| 更新 skills | 每月 | `cd .opencode/skills/<repo> && git pull` |
| 重新生成 junctions | skills 更新后 | `powershell -File _runtime\create-skill-junctions.ps1` |

### 10.2 月度维护

```powershell
# 1. 更新所有 skills 仓库
cd .opencode\skills\anthropics-skills; git pull
cd ..\obra-superpowers; git pull
cd ..\mattpocock-skills; git pull
cd ..\vercel-agent-skills; git pull

# 2. 重新生成 junctions
cd E:\system_folder\.claude\.claude
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1

# 3. 更新 OpenCode
npm update -g opencode-ai

# 4. 更新 Python 依赖
pip install --upgrade PyMuPDF Pillow playwright mcp-server-git duckduckgo-mcp-server

# 5. 健康检查
start-opencode.bat
# 然后输入: /doctor
```

### 10.3 季度维护

- 检查 API Key 是否需要轮换
- 检查 GitHub Token 是否过期
- 清理 `_runtime/mcp-memory.json`（如过大）
- 备份 `markconfig/` 和 `_data/memory/`
- 检查 OpenCode 是否有 major version 升级

### 10.4 年度维护

- 完整备份项目到外部存储
- 审查 AGENTS.md 是否符合当前需求
- 审查所有 MCP 是否仍需要
- 审查所有 skills 是否仍使用
- 更新文档（README, TECHNICAL, SYSTEM, DEVELOPER, USER_GUIDE）

### 10.5 紧急恢复

如果项目损坏无法启动：

```powershell
# 1. 从备份恢复
Copy-Item -Path "E:\backup\claude_<date>" -Destination "E:\system_folder\.claude\.claude" -Recurse -Force

# 2. 重装 OpenCode
npm install -g opencode-ai

# 3. 重装 Python 依赖
pip install PyMuPDF Pillow playwright mcp-server-git duckduckgo-mcp-server
playwright install firefox

# 4. 重新克隆 skills
cd .opencode\skills
git clone https://ghfast.top/https://github.com/anthropics/skills.git anthropics-skills
# ... (其他 3 个仓库)

# 5. 重新生成 junctions
powershell -ExecutionPolicy Bypass -File _runtime\create-skill-junctions.ps1

# 6. 启动验证
start-opencode.bat
/doctor
```

---

## 附录：快速参考卡

### 启动
```
E:\system_folder\.claude\.claude\start-opencode.bat
```

### Slash 命令
```
/doctor          # 健康检查
/review          # 代码审查
/handoff save    # 保存上下文
/handoff list    # 列出交接包
/handoff clean   # 清理交接包
/browser         # 启动浏览器
```

### Subagent 调用
```
@review-code 检查 xxx
@review-structure 评估 xxx
@review-risk 扫描 xxx
```

### 常用 MCP
```
filesystem    # 文件操作
github        # GitHub 操作
context7      # 实时文档
duckduckgo    # 搜索 (Layer 1)
searxng       # 元搜索 (Layer 2)
g-search      # Google 搜索 (Layer 3)
memory        # 持久化记忆
sqlite        # 数据库
```

### 故障排查
```
/doctor                      # 第一步
opencode run "PING"          # 测试 AI
python modules/vision/recognize.py test.png  # 测试视觉
python modules/browser/daemon.py             # 测试浏览器
```

### 紧急联系
- OpenCode 文档: https://opencode.ai/docs
- DeepSeek API: https://platform.deepseek.com
- SiliconFlow: https://siliconflow.cn
- GitHub Token: https://github.com/settings/tokens

---

**End of User Guide**
