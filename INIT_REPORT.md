# INIT REPORT — OpenCode 迁移完成报告

> 项目：`E:/system_folder/.claude/.claude`
> 所有者：韩铭洋（Mark）
> 完成日期：2026-07-17
> 迁移方向：Claude Code CLI → OpenCode
> 主模型：DeepSeek V4 Pro + V4 Flash

---

## 1. 迁移目标

将原有的 Claude Code CLI 基础设施替换为 OpenCode 架构，保留核心能力（DeepSeek 主脑、Flash 审查、视觉识别、浏览器自动化、用户记忆系统），同时利用 OpenCode 原生的扩展点系统（tools / agents / commands / skills）替代自定义的 hook + bridge 层。

## 2. 完成的工作

### 2.1 配置迁移

| 项 | 状态 |
|----|------|
| 全局配置 `~/.config/opencode/opencode.json` | ✅ 创建（provider + permission + compaction） |
| 项目配置 `opencode.json` | ✅ 创建（model + instructions + watcher） |
| `AGENTS.md` | ✅ 创建（替代 CLAUDE.md，移除 hooks/statusLine 概念） |
| 启动脚本 `start-opencode.bat` | ✅ 创建（从 secrets.json 加载 Key + 设置 PATH） |
| npm 全局目录迁移到 `E:\npm-global` | ✅（解决 C 盘空间不足） |

### 2.2 扩展点建设

| 类型 | 文件 | 用途 |
|------|------|------|
| Tool | `.opencode/tools/vision.ts` | 视觉识别工具（替代 Read 对图片/PDF 的调用） |
| Tool | `.opencode/tools/browser.ts` | 浏览器工具组（9 个工具） |
| Subagent | `.opencode/agents/review-code.md` | Flash 1 等价（代码质量审查） |
| Subagent | `.opencode/agents/review-structure.md` | Flash 2 等价（文件结构审查） |
| Subagent | `.opencode/agents/review-risk.md` | Flash 4 等价（安全风险审查） |
| Command | `.opencode/commands/doctor.md` | 健康诊断 |
| Command | `.opencode/commands/review.md` | 触发 3 阶段审查管道 |
| Command | `.opencode/commands/handoff.md` | 上下文交接管理 |
| Command | `.opencode/commands/browser.md` | 启动浏览器 Daemon |
| Skill | `.opencode/skills/memory-context/SKILL.md` | 用户记忆索引 |

### 2.3 Python 后端精简

| 模块 | 状态 | 说明 |
|------|------|------|
| `modules/vision/recognize.py` | ✅ 修复 | 移除 `api.registry` 依赖，直接调用 SiliconFlow API |
| `modules/vision/clipboard.py` | ✅ 保留 | 剪贴板识别（调用 recognize.py） |
| `modules/browser/daemon.py` | ✅ 保留 | Playwright Firefox HTTP Daemon |

### 2.4 清理废弃组件

| 删除目标 | 状态 |
|---------|------|
| `_config/` (settings.json, CLAUDE.md, .mcp.json 等) | ✅ |
| `bridge/` (hook_dispatcher, registries) | ✅ |
| `api/` (LLM/Vision 适配层) | ✅ |
| `detection/` (模型/余额检测) | ✅ |
| `commands/` (旧 slash 命令) | ✅ |
| `modules/flash_review/` | ✅ |
| `modules/dynamic-flash/` | ✅ |
| `modules/handoff/` | ✅ |
| `modules/integration/` | ✅ |
| `modules/memory/` | ✅ |
| `modules/monitoring/` | ✅ |
| `modules/translate/` | ✅ |
| `modules/tests/` | ✅ |
| `modules/utils/` | ✅ |
| `modules/extract_bcd.py` | ✅ |
| `modules/deep_scan.py` | ✅ |
| `modules/browser/browser.py` 及附属 PNG | ✅ |
| `startup/` (bridge_server, tool_registry) | ✅ |
| `start-claude.bat` | ✅ |
| `cache/`, `paste-cache/`, `.langgraph_api/` | ✅ |
| `_state/`, `_runtime/pids/`, `_runtime/checkpoints.db` | ✅ |
| `_data/flash_review/` | ✅ |
| `quicksort_bench.py`, `temp_screenshot.jpg`, `test.txt` | ✅ |
| `skills/` (51/55 subdirs deleted, claude-mem 后台进行中) | 🔄 |
| `plugins/cache/` (含 claude-mem 13.6.0 完整源码副本) | 🔄 |
| `workspace/repos/` (claude-mem clone) | 🔄 |

> 注：以上 3 个目录共约 11,500 文件，Windows 文件系统删除速度受限于 antivirus 扫描与 filesystem overhead。后台进程持续运行，不影响 OpenCode 使用。可手动用 PowerShell `[System.IO.Directory]::Delete($path, $true)` 或 File Explorer 强制删除。

### 2.5 保留的核心资产

| 路径 | 用途 |
|------|------|
| `markconfig/` | API Keys + 个人画像 + 系统路径 |
| `_data/memory/` | 用户记忆文件（MEMORY.md + 7 个分类文件） |
| `_runtime/handoff/` | 上下文交接包目录 |
| `_docs/PROJECT_DOC.md` | 架构文档（已更新为 OpenCode 架构） |

## 3. 验证结果

| 测试 | 命令 | 结果 |
|------|------|------|
| DeepSeek API 连通性 | `opencode run "echo OPENCODE_DEEPSEEK_OK"` | ✅ 返回 `OPENCODE_DEEPSEEK_OK` |
| AGENTS.md 加载 | `opencode run "echo AGENTS_MD_LOADED"` | ✅ 返回 `AGENTS_MD_LOADED` |

## 4. 架构对比

| 维度 | Claude Code (旧) | OpenCode (新) |
|------|------------------|---------------|
| 主脑 | DeepSeek V4 Pro via bridge_server | DeepSeek V4 Pro via OpenCode provider |
| 审查 | Flash 1/2 (PostToolUse) + Dynamic Flash (Stop) | 3 subagents (review-code/structure/risk) |
| 触发 | 自动 hook | `/review` 命令 + AGENTS.md 约束 |
| 视觉 | `api.registry.get_vision()` | `recognize.py` 直接调用 SiliconFlow |
| 浏览器 | Marionette (2828) + Daemon (9223) | Daemon (9223) + 9 个 TS 工具 |
| 后台服务 | bridge_server + tool_registry | 无（OpenCode 原生） |
| 状态行 | `combined_status.py` (3 秒) | OpenCode TUI 内置 |
| 配置 | `_config/settings.json` | `opencode.json` 分层 |
| 行为约束 | `CLAUDE.md` | `AGENTS.md` (instructions) |

## 5. 使用方法

### 启动

```bash
# Windows
start-opencode.bat
```

### Slash 命令

```
/doctor        # 健康诊断
/review        # 3 阶段代码审查
/handoff       # 上下文交接
/browser       # 启动浏览器 Daemon
```

### Subagent 调用

```
@review-code 检查 auth.py
@review-structure 评估目录组织
@review-risk 扫描敏感信息
```

### 直接调用 Python 后端

```bash
python modules/vision/recognize.py image.png
python modules/vision/recognize.py document.pdf
python modules/browser/daemon.py
```

## 6. 后续建议

1. **首次使用**：执行 `start-opencode.bat`，然后运行 `/doctor` 验证环境
2. **审查测试**：在第一个代码修改任务后执行 `/review`，验证 3 subagent 串联
3. **视觉测试**：粘贴一张截图，让 OpenCode 调用 vision 工具识别
4. **浏览器测试**：执行 `/browser` 启动 Daemon，然后让 OpenCode 调用 `browser_navigate`
5. **交接测试**：长任务结束后执行 `/handoff save`，新会话执行 `/handoff list`
6. **skills/ 完整清理**：后台删除任务完成后，确认 `skills/` 目录已清空
