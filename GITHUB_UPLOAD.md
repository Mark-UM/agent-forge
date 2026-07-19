# GitHub 上传流程

> **目标仓库**：将 AgentForge 项目上传到你自己的 GitHub 仓库
> **前置条件**：已安装 Git，已在 GitHub 建立空仓库
> **最后更新**：2026-07-19

---

## 目录

1. [前置检查](#1-前置检查)
2. [项目命名](#2-项目命名)
3. [敏感信息清理](#3-敏感信息清理)
4. [Git 初始化](#4-git-初始化)
5. [首次提交](#5-首次提交)
6. [推送到 GitHub](#6-推送到-github)
7. [验证](#7-验证)
8. [后续维护](#8-后续维护)
9. [可选增强](#9-可选增强)

---

## 1. 前置检查

### 1.1 验证 Git 安装

```powershell
git --version
# 应输出: git version 2.x.x.windows.x
```

### 1.2 验证 GitHub 仓库已建立

- 登录 https://github.com
- 确认已创建空仓库（不要勾选 "Initialize this repository with README"）
- 记录仓库 URL，格式如：`https://github.com/<你的用户名>/<仓库名>.git`

### 1.3 配置 Git 全局身份（如未配置过）

```powershell
git config --global user.name "韩铭洋"
git config --global user.email "mingyang0010@outlook.com"

# 验证
git config --global --list
```

### 1.4 配置 GitHub 认证

推荐使用 HTTPS + Personal Access Token（已有 token 可复用）：

```powershell
# 缓存凭证（避免每次输入）
git config --global credential.helper manager

# 或使用 Git Credential Manager Core（Windows 默认）
# 首次 push 时会弹出浏览器登录 GitHub
```

**SSH 方式**（可选）：

```powershell
# 生成 SSH key
ssh-keygen -t ed25519 -C "mingyang0010@outlook.com"
# 一路回车（默认路径，无密码）

# 查看 public key
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub

# 复制到 GitHub: Settings → SSH and GPG keys → New SSH key
```

---

## 2. 项目命名

### 2.1 推荐项目名

**AgentForge**

含义：
- **Agent** — 体现 AI Agent 核心定位
- **Forge** — 锻造厂/工坊，体现"打造、构建、工艺"
- 简洁、英文化、有辨识度
- 不绑定个人身份，适合公开仓库

**GitHub 仓库名**：`agent-forge`（小写连字符，GitHub 惯例）
**项目内引用名**：`AgentForge`（首字母大写）

### 2.2 备选方案（如不喜欢 AgentForge）

| 候选 | 含义 | 适用场景 |
|------|------|---------|
| `MarkOps` | Mark 的 Operations | 强调个人化 |
| `Nexus` | 神经中枢 | 强调连接能力 |
| `Sentinel` | 哨兵 | 强调自动化守护 |
| `Aegis` | 神盾 | 强调安全防护 |
| `Polymath` | 博学者 | 强调多能力 |

### 2.3 更新项目内引用

项目内文档仍以 "Mark AI Infrastructure" 或 "OpenCode Edition" 自称，不强求统一改名。如希望统一品牌：

```powershell
# 在项目根目录执行
# 替换 README.md 中的项目名
# (手动编辑，确保不破坏其他内容)
```

---

## 3. 敏感信息清理

### 3.1 关键检查清单

**绝对不能提交**：

| 文件 | 内容 | 保护方式 |
|------|------|---------|
| `markconfig/secrets.json` | API Keys | `.gitignore` 已排除 |
| `_runtime/` | 运行时数据（含可能的历史 token） | `.gitignore` 已排除 |
| `_data/memory/user-*.md` | 个人隐私（电话/邮箱/位置） | 建议排除 |
| `markconfig/profile.md` | 完整个人画像 | 建议排除 |

### 3.2 验证 .gitignore 生效

```powershell
cd E:\system_folder\.claude\.claude

# 检查 .gitignore 存在
Test-Path .gitignore

# 模拟 git 跟踪，验证敏感文件被忽略
git init
git status

# 应该看不到:
# - markconfig/secrets.json
# - _runtime/
# - node_modules/
# - .opencode/skills/anthropics-skills/ (等 4 个外部仓库)
```

### 3.3 提供示例配置（让他人能复用）

为方便他人复用，创建示例配置文件：

```powershell
# 创建示例 secrets 模板
Copy-Item markconfig\secrets.json markconfig\secrets.example.json

# 编辑 secrets.example.json，将所有真实值替换为占位符
notepad markconfig\secrets.example.json
```

`secrets.example.json` 内容：

```json
{
  "ANTHROPIC_AUTH_TOKEN": "sk-deepseek-your-api-key-here",
  "SILICONFLOW_API_KEY": "sk-siliconflow-your-api-key-here",
  "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_your-github-token-here",
  "FLASH_1_API_KEY": "",
  "FLASH_2_API_KEY": "",
  "TAVILY_API_KEY": ""
}
```

**同样为 profile.md 创建示例**：

```powershell
Copy-Item markconfig\profile.md markconfig\profile.example.md
# 编辑 profile.example.md，替换为占位符
```

### 3.4 个人记忆文件处理

`_data/memory/user-*.md` 包含个人隐私，建议**不提交**：

```powershell
# .gitignore 已包含以下规则（如未包含则添加）:
# _data/memory/user-*.md
# _data/memory/MEMORY.md

# 但保留模板:
Copy-Item _data\memory\MEMORY.md _data\memory\MEMORY.example.md
# 编辑为占位符
```

### 3.5 最终验证

```powershell
# 确保以下文件不在 git 跟踪范围
git status --short

# 重点检查:
git status --short | Select-String "secrets|profile|memory|_runtime"
# 应该没有任何输出（这些文件都被 .gitignore 排除）
```

---

## 4. Git 初始化

### 4.1 在 GitHub 创建仓库

1. 访问 https://github.com/new
2. **Repository name**: `agent-forge`
3. **Description**: `A highly personalized AI agent infrastructure built on OpenCode + DeepSeek V4 Pro, featuring 13 MCP servers, 3 plugins, 67 skills, multi-stage code review, and local automation capabilities.`
4. **Visibility**: Public（或 Private，按需）
5. **不要勾选** "Initialize this repository with README"
6. 点击 "Create repository"

记录仓库 URL：`https://github.com/<你的用户名>/agent-forge.git`

### 4.2 本地初始化

```powershell
cd E:\system_folder\.claude\.claude

# 初始化 git 仓库
git init

# 设置默认分支名为 main（GitHub 当前默认）
git branch -M main

# 验证 .gitignore 生效
git status
```

### 4.3 配置远程仓库

```powershell
# 添加远程仓库（替换 <你的用户名>）
git remote add origin https://github.com/<你的用户名>/agent-forge.git

# 验证
git remote -v
# 应输出:
# origin  https://github.com/<你的用户名>/agent-forge.git (fetch)
# origin  https://github.com/<你的用户名>/agent-forge.git (push)
```

---

## 5. 首次提交

### 5.1 暂存文件

```powershell
# 添加所有文件（.gitignore 会自动排除敏感文件）
git add .

# 检查暂存内容
git status

# 重点验证: 确认以下文件未暂存
git diff --cached --name-only | Select-String "secrets|profile.md|_runtime|memory"
# 应该无输出
```

### 5.2 验证暂存内容安全

```powershell
# 列出所有暂存文件
git diff --cached --name-only

# 应包含:
# .gitignore
# .opencode/agents/review-code.md
# .opencode/agents/review-risk.md
# .opencode/agents/review-structure.md
# .opencode/commands/browser.md
# .opencode/commands/doctor.md
# .opencode/commands/handoff.md
# .opencode/commands/review.md
# .opencode/package.json
# .opencode/skills/memory-context/SKILL.md
# .opencode/tools/browser.ts
# .opencode/tools/vision.ts
# AGENTS.md
# INIT_REPORT.md
# README.md
# ROADMAP.md
# _data/memory/README-INIT.md
# _data/memory/MEMORY.example.md (如创建了示例)
# _docs/DEVELOPER.md
# _docs/PROJECT_DOC.md
# _docs/SYSTEM.md
# _docs/TECHNICAL.md
# _docs/USER_GUIDE.md
# _runtime/create-skill-junctions.ps1
# _runtime/cleanup-legacy.ps1
# markconfig/README.md
# markconfig/paths.json
# markconfig/profile.example.md (如创建了示例)
# markconfig/secrets.example.json (如创建了示例)
# modules/browser/daemon.py
# modules/vision/clipboard.py
# modules/vision/manifest.json
# modules/vision/recognize.py
# opencode.json
# start-opencode.bat
```

**不应包含**：
- `markconfig/secrets.json`（敏感）
- `markconfig/profile.md`（个人隐私，可选保留）
- `_runtime/handoff/*.md`（交接包含会话信息）
- `_runtime/*.jsonl` / `*.db`（运行时数据）
- `.opencode/skills/anthropics-skills/`（外部仓库克隆）

### 5.3 创建首次提交

```powershell
git commit -m @"
Initial commit: AgentForge — Personalized AI Agent Infrastructure

Features:
- OpenCode-based architecture with DeepSeek V4 Pro/Flash
- 13 MCP servers (filesystem, github, context7, search, etc.)
- 3 plugins (oh-my-opencode, dcp, antigravity-auth)
- 67 skills from 4 GitHub repos (anthropics, obra, mattpocock, vercel)
- 3-stage code review pipeline (review-code/structure/risk)
- Vision recognition via SiliconFlow Qwen3-VL-Plus
- Browser automation via Playwright + Firefox
- 3-layer search architecture (DuckDuckGo + SearXNG + Google)
- User memory system (7 categorized files)
- Comprehensive documentation (5 docs: README/TECHNICAL/SYSTEM/DEVELOPER/USER_GUIDE)

Tech stack: TypeScript (tools) + Python (backend) + Markdown (config)
"@
```

---

## 6. 推送到 GitHub

### 6.1 首次推送

```powershell
# 推送并设置上游
git push -u origin main
```

**首次推送可能较慢**（文件较多），耐心等待。

### 6.2 认证方式

**HTTPS + Token**（推荐）：

首次 push 时，Git Credential Manager 会弹出浏览器：
1. 浏览器打开 GitHub 登录页
2. 登录 GitHub 账号
3. 授权 Git Credential Manager
4. 凭证自动缓存，后续无需重复

**SSH 方式**（如已配置 SSH key）：

```powershell
# 修改 remote URL 为 SSH 格式
git remote set-url origin git@github.com:<你的用户名>/agent-forge.git

# 推送
git push -u origin main
```

### 6.3 推送失败处理

**问题 1：`rejected - non-fast-forward`**

```powershell
# 拉取远程变更后重推
git pull origin main --rebase
git push -u origin main
```

**问题 2：`Authentication failed`**

```powershell
# 重新配置凭证
git config --global credential.helper manager

# 或更新 token
# 控制面板 → 凭据管理器 → Windows 凭据 → 找到 git:https://github.com → 删除
# 再次 push 时会重新提示登录
```

**问题 3：`file too large`**

```powershell
# 检查大文件
git ls-files | Where-Object { (Get-Item $_).Length -gt 10MB }

# 如有大文件，加入 .gitignore 后重新提交
git rm --cached <大文件路径>
git add .gitignore
git commit -m "exclude large files"
git push
```

---

## 7. 验证

### 7.1 在线验证

1. 访问 `https://github.com/<你的用户名>/agent-forge`
2. 检查文件列表：
   - ✓ README.md 显示项目说明
   - ✓ 目录结构完整（`.opencode/`, `modules/`, `_docs/`, `markconfig/`）
   - ✓ 代码文件存在（`opencode.json`, `AGENTS.md`, `start-opencode.bat`）
   - ✗ `markconfig/secrets.json` 不存在（敏感文件已排除）
   - ✗ `_runtime/*.jsonl` / `*.db` 不存在（运行时数据已排除）

### 7.2 安全验证

**关键检查**：在 GitHub 网页搜索可能泄露的内容：

1. 访问 `https://github.com/<你的用户名>/agent-forge/search?q=sk-`
   - 应该无结果（无 API Key 泄露）

2. 访问 `https://github.com/<你的用户名>/agent-forge/search?q=ghp_`
   - 应该无结果（无 GitHub Token 泄露）

3. 访问 `https://github.com/<你的用户名>/agent-forge/search?q=<你的手机号>`
   - 应该无结果（无手机号泄露）

4. 访问 `https://github.com/<你的用户名>/agent-forge/search?q=mingyang0010`
   - 应该无结果（无邮箱泄露，除非保留在文档中）

**如发现泄露**：
- 立即从 GitHub 删除该文件
- 重新生成泄露的 API Key / Token
- 修改 `.gitignore` + `git rm --cached <file>` + 重新提交

### 7.3 GitHub Security 扫描

启用 GitHub 自动安全扫描：

1. 仓库 → Settings → Code security and analysis
2. 启用：
   - Secret scanning
   - Push protection
   - Dependabot alerts
   - Dependabot security updates

GitHub 会自动扫描提交内容，发现密钥泄露会报警。

### 7.4 功能验证

clone 到新目录验证可复现性：

```powershell
# clone 到临时目录
cd E:\temp
git clone https://github.com/<你的用户名>/agent-forge.git agent-forge-test
cd agent-forge-test

# 验证目录结构
ls

# 验证配置文件存在
Test-Path .gitignore
Test-Path opencode.json
Test-Path AGENTS.md

# 验证示例配置存在
Test-Path markconfig\secrets.example.json

# 复制示例为真实配置
Copy-Item markconfig\secrets.example.json markconfig\secrets.json
# 编辑 secrets.json 填入真实 API Key

# 启动验证
.\start-opencode.bat
# 在 OpenCode 中输入 /doctor
```

---

## 8. 后续维护

### 8.1 日常更新流程

```powershell
# 1. 修改代码
# (编辑文件)

# 2. 检查变更
git status
git diff

# 3. 暂存相关文件（避免 git add . 误加敏感文件）
git add <specific-files>

# 4. 提交
git commit -m "feat: 添加 xxx 功能"
# 或
git commit -m "fix: 修复 xxx 问题"
# 或
git commit -m "docs: 更新 xxx 文档"

# 5. 推送
git push
```

### 8.2 提交信息规范

推荐使用 Conventional Commits：

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat:` | 新功能 | `feat: add OCR tool` |
| `fix:` | Bug 修复 | `fix: resolve daemon crash on startup` |
| `docs:` | 文档更新 | `docs: update ROADMAP with v2.0 details` |
| `refactor:` | 重构 | `refactor: extract vision API caller` |
| `test:` | 测试 | `test: add daemon unit tests` |
| `chore:` | 杂项 | `chore: update dependencies` |
| `security:` | 安全修复 | `security: validate URL scheme in browser` |

### 8.3 分支策略

对于个人项目，简化版 Git Flow：

```
main          ← 稳定版本，可随时使用
└─ develop    ← 日常开发
   └─ feature/xxx  ← 新功能分支
```

```powershell
# 创建开发分支
git checkout -b develop
git push -u origin develop

# 新功能分支
git checkout -b feature/agent-cluster
# 开发...
git add .
git commit -m "feat: implement orchestrator agent"

# 合并回 develop
git checkout develop
git merge feature/agent-cluster
git push

# develop 稳定后合并到 main
git checkout main
git merge develop
git push
```

### 8.4 Skills 仓库同步

Skills 仓库（anthropics/obra/mattpocock/vercel）不提交到你的仓库，但需要在 README 说明：

```markdown
## Setup

1. Clone this repo
2. Clone skill repos:
   cd .opencode/skills
   git clone https://github.com/anthropics/skills.git anthropics-skills
   # ... 其他 3 个
3. Generate junctions:
   powershell -File _runtime/create-skill-junctions.ps1
4. Copy markconfig/secrets.example.json to markconfig/secrets.json, fill in API keys
5. Run start-opencode.bat
```

### 8.5 版本标签

里程碑版本打 tag：

```powershell
# v1.0
git tag -a v1.0 -m "AgentForge v1.0 — Initial release"
git push origin v1.0

# v1.5
git tag -a v1.5 -m "AgentForge v1.5 — Dynamic prompt engineering"
git push origin v1.5

# 查看所有 tag
git tag
```

在 GitHub Releases 页面可基于 tag 创建 Release notes。

---

## 9. 可选增强

### 9.1 GitHub Actions CI

创建 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install PyMuPDF Pillow playwright mcp-server-git duckduckgo-mcp-server
      - name: Syntax check
        run: |
          python -m py_compile modules/vision/recognize.py
          python -m py_compile modules/vision/clipboard.py
          python -m py_compile modules/browser/daemon.py
      - name: JSON validation
        run: |
          python -c "import json; json.load(open('opencode.json')); print('JSON_OK')"
```

### 9.2 README 徽章

在 README.md 顶部添加：

```markdown
![License](https://img.shields.io/badge/license-MIT-blue)
![OpenCode](https://img.shields.io/badge/OpenCode-latest-green)
![DeepSeek](https://img.shields.io/badge/DeepSeek-V4_Pro-purple)
![MCP](https://img.shields.io/badge/MCP-13_servers-orange)
![Skills](https://img.shields.io/badge/Skills-67-yellow)
```

### 9.3 LICENSE 文件

```powershell
# 创建 MIT License
@"
MIT License

Copyright (c) 2026 韩铭洋 (Mark)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"@ | Out-File -FilePath LICENSE -Encoding utf8
```

### 9.4 CONTRIBUTING.md

```markdown
# Contributing to AgentForge

## Development Setup
1. Fork this repo
2. Clone your fork
3. Run `start-opencode.bat`
4. Run `/doctor` to verify environment

## Coding Standards
- Python: PEP 8
- TypeScript: ESLint recommended
- Commits: Conventional Commits (feat/fix/docs/refactor/...)

## Pull Request Process
1. Create feature branch from develop
2. Make changes, ensure `/doctor` passes
3. Run `/review` for self-review
4. Submit PR with description
```

### 9.5 ISSUE_TEMPLATE

```markdown
<!-- .github/ISSUE_TEMPLATE/bug_report.md -->
---
name: Bug Report
about: Report a bug
---

## Description
[Describe the bug]

## Steps to Reproduce
1.
2.
3.

## Expected Behavior
[What should happen]

## Actual Behavior
[What actually happens]

## Environment
- OS: Windows 11
- Python: 3.11.x
- OpenCode: [version]
```

---

## 附录：快速上传命令汇总

```powershell
# 一键上传（首次）

cd E:\system_folder\.claude\.claude

# 1. 初始化
git init
git branch -M main
git remote add origin https://github.com/<你的用户名>/agent-forge.git

# 2. 创建示例配置（如尚未创建）
# (参考第 3.3 节)

# 3. 暂存 + 提交
git add .
git commit -m "Initial commit: AgentForge — Personalized AI Agent Infrastructure"

# 4. 推送
git push -u origin main

# 5. 验证
# 浏览器访问 https://github.com/<你的用户名>/agent-forge
```

---

**End of GitHub Upload Guide**
