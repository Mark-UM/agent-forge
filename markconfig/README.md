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

Create the two local files from their examples. The launcher currently exports
only `ANTHROPIC_AUTH_TOKEN` as `DEEPSEEK_API_KEY`, `SILICONFLOW_API_KEY`,
`GITHUB_PERSONAL_ACCESS_TOKEN`, and `SERPER_API_KEY`. Other example fields are
reserved and are not loaded by the launcher.

Secrets remain plain text on disk. Git exclusion is not encryption; restrict
filesystem access and never paste the real file into logs or issues.

Machine paths are not configured here. Most process commands now use `python`
and repository-relative paths. The Firefox executable/profile are still
constants in `modules/browser/daemon.py`.
