# User Guide

## Start

Create ignored local configuration, install dependencies, and launch:

```powershell
Copy-Item markconfig/secrets.example.json markconfig/secrets.json
Copy-Item markconfig/profile.example.md markconfig/profile.md
python -m pip install -r requirements.txt
npm ci --prefix .opencode
python -m playwright install firefox
.\start-opencode.bat
```

`python`, `npx`, and `opencode` must be on `PATH`. The launcher composes the
generated Prompt but does not start Browser/Scheduler daemons.

## Slash Commands

| Command | Purpose | Boundary |
|---|---|---|
| `/doctor` | report configuration/dependency/memory health | reports; does not repair |
| `/review` | sequentially coordinate 3 read-only reviewers | procedural |
| `/deliver` | combine TS/Vite static checks and review | not one Python transaction |
| `/browser` | start the localhost Firefox daemon | required before Browser tools |
| `/search` | provider-aware Search procedure | not directly wired to `SearchOrchestrator` |
| `/collect` | screenshot/Vision-based URL report | current browser-use branch unfinished |
| `/index` | ChromaDB file indexing/search | requires ChromaDB |
| `/schedule` | SQLite schedule CRUD/action extraction | Scheduler daemon is separate |
| `/mode` | compose a task/profile Prompt | writes ignored generated state |
| `/prompt` | inspect Prompt version/experiments | reads ignored logs |
| `/handoff` | manage context packets | packets may be sensitive |

## Browser and collection

Run `/browser`, verify `http://127.0.0.1:9223/ping`, then use Browser tools.
The daemon is visible (`headless=False`), binds localhost, and remains running
until closed. Edit its Firefox executable/profile constants for your machine.

`/collect` currently uses the daemon fallback. It navigates, waits, screenshots,
calls Vision, and summarizes recognized screenshot text. It is not full DOM
extraction.

## Vision

Image/PDF requests use the custom Vision tool. PDF rendering requires PyMuPDF;
clipboard images require Pillow; API calls require `SILICONFLOW_API_KEY`.

## Search

The currently enabled general providers are SearXNG and Serper; Serper needs an
API key. Context7/GitHub are preferred for their domains, and arXiv/Semantic
Scholar handle academic requests. DuckDuckGo and g-search are disabled
definitions. Always report providers that actually succeeded.

## Local state and privacy

Secrets, profile, memory, and `_runtime/` remain plain local files. They are
ignored by Git, not encrypted. The memory hook writes only when explicitly
called; it is not an automatic event listener.

## Troubleshooting

- Browser import/start error: install requirements and Playwright Firefox, then
  check the Firefox constants.
- Vision PDF/clipboard error: verify PyMuPDF/Pillow and the SiliconFlow key.
- Index unavailable: verify ChromaDB installation.
- Scheduler reports `stub`: APScheduler is absent.
- Serper fails: verify `SERPER_API_KEY` and network access.
- External skills missing: expected on a clean clone; no tracked installer
  currently recreates the four upstream repositories/junctions.
