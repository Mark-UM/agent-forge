# Local Configuration

This directory contains tracked examples plus ignored machine/user-specific
configuration.

| Path | Git policy | Runtime use |
|---|---|---|
| `secrets.example.json` | tracked | safe field template |
| `secrets.json` | ignored | read by `start-opencode.bat` |
| `profile.example.md` | tracked | safe profile template |
| `profile.md` | ignored | loaded by `opencode.json` |
| `authority_whitelist.json` | tracked | read by `modules/search/quality.py` |

Create the two local files from their examples. The launcher exports
`DEEPSEEK_API_KEY` (falling back to the legacy `ANTHROPIC_AUTH_TOKEN` field),
`SILICONFLOW_API_KEY`, `GITHUB_PERSONAL_ACCESS_TOKEN`, and `SERPER_API_KEY`.
Other example fields are reserved and are not loaded by the launcher.

Secrets remain plain text on disk. Git exclusion is not encryption; restrict
filesystem access and never paste the real file into logs or issues.

Machine paths are not configured here. Use `AGENT_FORGE_PYTHON` and the
`AGENT_FORGE_BROWSER_*` environment variables for interpreter/Browser
overrides; default state remains repository-relative and ignored.
