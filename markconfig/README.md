# markconfig — Mark 的个人配置仓库

此目录存放 Mark 的个人信息、API Key、系统路径等敏感配置。

## 文件说明

| 文件 | 用途 |
|------|------|
| `secrets.json` | API Key / Token（DeepSeek、SiliconFlow、GitHub PAT） |
| `profile.md` | 个人身份画像（被 opencode.json 通过 instructions 字段加载） |
| `paths.json` | 系统路径（项目根、Python 解释器路径） |
| `README.md` | 本说明文件 |

## 安全提示

- **请勿将本目录提交到公共仓库**（已在 `.gitignore` 中排除）。
- 修改 `secrets.json` 后需重启 OpenCode（通过 `start-opencode.bat`）以重新加载环境变量。
- `secrets.json` 字段：
  - `ANTHROPIC_AUTH_TOKEN` — DeepSeek API Key（OpenCode provider 使用）
  - `SILICONFLOW_API_KEY` — SiliconFlow 视觉模型 API Key（vision 工具使用）
  - `GITHUB_PERSONAL_ACCESS_TOKEN` — GitHub Personal Access Token（GitHub MCP 使用）
  - `FLASH_1_API_KEY` / `FLASH_2_API_KEY` — 备用字段（当前未使用）
  - `TAVILY_API_KEY` — 备用字段（当前未使用）
