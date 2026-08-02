# Non-Code Audit

Audit date: 2026-08-02. Scope: tracked documentation, Markdown commands/skills,
JSON manifests/configuration, dependency declarations, ignore policy, and
repository organization. This is a remediation record, not a passing test
report.

## Resolved in this cleanup

| Problem found | Resolution |
|---|---|
| Multiple current architecture documents contradicted code/config | replaced by one maintained `ARCHITECTURE.md`; old `PROJECT_DOC.md`, `SYSTEM.md`, and `TECHNICAL.md` removed |
| Root roadmap plus many mixed active/historical upgrade files | one active roadmap under `_docs/roadmap/`; all old plans grouped under `archive/` with disclaimers |
| Search docs referenced nonexistent `modules/search/logger.py`, an undated log, fixed location rules, and disabled providers | command/skill now derive provider state from `opencode.json` and identify dated history plus callback boundaries |
| Browser tool claimed daemon auto-start | tool and `/browser` now state explicit startup |
| Collection docs claimed working browser-use primary execution | docs/manifests now record the placeholder and daemon fallback |
| Skills named nonexistent `/bootstrap`, `/check-integration`, or `modules/delivery/checks/*` | owned skills now point to actual CLIs/files and state static-analysis limits |
| Active `modules/memory/` was hidden by a legacy ignore rule | ignore rule removed; module is versionable and manifest states explicit-call behavior |
| Private memory docs claimed automatic loading/complete tracked contents | policy now separates tracked templates from ignored optional local files |
| `markconfig` docs implied the whole directory was private and all fields were exported | safe tracked examples/whitelist and four launcher-exported fields are distinguished |
| Tracked dead `markconfig/paths.json` exposed local absolute paths but was read by no code | file removed; launcher/MCP config changed to repository-relative commands |
| Filesystem MCP exposed the project, parent directory, and whole drive | configuration narrowed to the current repository |
| Requirements omitted Playwright, Vision, ChromaDB, Git MCP, and pytest declarations while listing unused browser-use/apprise plans | runtime/development requirements split and aligned with functional imports/processes |
| External skill junctions/caches/vendor trees polluted status and portability | ignore policy now covers their actual names; generated/dependency trees are excluded |
| Historical reports/plans presented acceptance statements as current facts | archive/history headers and index explicitly revoke current-spec status |
| Tracked instructions duplicated personal identity/location and machine paths | personal facts remain only in ignored local profile/memory files |

## Remaining known non-code/configuration gaps

- No CI or automated documentation consistency/secret scan exists.
- Several npm/plugin references and `mcp-server-git` are unpinned; some install
  at execution time.
- SearXNG is a fixed public endpoint.
- No tracked installer recreates external skill repositories/junctions.
- Firefox paths and two Search health-check Python executable probes are still
  machine-specific constants in Python code; two test helper files also retain
  the original repository path.
- Provider enabled state does not prove package/key/network readiness.
- Historical documents intentionally retain old details for decision context;
  consumers must follow their archive warning.

## Deliberately not claimed

This cleanup does not claim that all tests pass, all MCPs connect, archived
benchmarks are reproducible, or every optional provider is installed. Those
states require a current execution in the target environment.

## Current verification evidence

On 2026-08-02, `python -m pytest -q` completed with 1,717 passed, 9 failed,
and 2 skipped tests. The nine failures are known and not hidden by a general
"tests pass" claim:

- four Bootstrap/Delivery subprocess assertions fail under the host's default
  GBK decoding; the same four pass with `PYTHONUTF8=1`;
- two Scheduler assertions require APScheduler, which is declared in
  `requirements.txt` but is not installed in the current interpreter;
- three Search export assertions use a fixed July 2026 fixture as "within seven
  days" and have become date-dependent.

Both skipped tests are conditional Scheduler tests skipped because APScheduler
is unavailable in that interpreter.

JSON parsing, Git ignore/privacy checks, documentation reference checks, and
`git diff --check` are separate static verification steps; they do not prove
external MCP/API readiness.
