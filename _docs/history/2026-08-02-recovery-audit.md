# 2026-08-02 Deletion Recovery Audit

This record explains what was deleted, what evidence was used, and which parts
are reconstruction rather than byte-for-byte restoration. It is historical
evidence, not a replacement for `_docs/ARCHITECTURE.md`.

## Incident

A PowerShell cleanup intended to enumerate `*.pyc`/`*.pyo` files used
`Get-ChildItem -Recurse -File -Include` with a `-LiteralPath` form for which the
filter did not constrain the recursive results. The resulting list contained
every file under `modules/` and `vendor/`, and the subsequent removal deleted
14,657 files at approximately 2026-08-02 16:01 local time.

The pre-deletion inventory recorded 247 entries under `modules/`, including 122
non-cache first-party files, and 14,410 files under `vendor/`. Private files
under `_data/memory/`, ignored runtime state outside those targets, and the
later cleanup quarantine were not targets of that command.

## Recovery authorities

Recovery used the following order of authority:

1. The OpenCode snapshot at
   `C:/Users/mingy/.local/share/opencode/snapshot/cfe0e4fda264da51f744da1356e37e5983ad5732/df6f0e037b1ab5be322bd64e19568661f7a55fe5`.
2. Exact content visible in the earlier read-only analysis transcript, where a
   snapshot did not cover a required file.
3. Archived design/acceptance documents, used only to recover intended
   contracts—not treated as proof that an implementation existed.
4. Current upstream packages for ignored dependencies, constrained by recovered
   version evidence and verified against the active Python runtime.

The snapshot is a local working-tree shadow, not a GitHub clone. Its config
points at this worktree and it has no usable remote/ref/commit history. The
118-file recovery staging tree under `_runtime/module-recovery` matched the
snapshot index with zero hash mismatches at the time of recovery.

## Why GitHub was not the restore source

The older GitHub commit examined during recovery contained only four relevant
module files. The local snapshot contained 118 module files: 114 had no
counterpart in that GitHub version, and one overlapping Browser daemon differed.
Therefore a matching hash for an old GitHub file proves only that one old file
matched; it does not establish that the lost local tree was the GitHub version.
No `git pull`, checkout, or clone was used to overwrite the recovered modules.

## Confidence by area

| Area | Recovery basis | Confidence/qualification |
|---|---|---|
| 118 first-party module files | local snapshot index and recovery tree | byte-level verified before subsequent reviewed improvements |
| `modules/memory/hook.py` | exact earlier read-only transcript content | original recovered exactly, then intentionally hardened while preserving public APIs and private data |
| private `_data/memory/*.md` | never deleted; timestamps predate incident | untouched by recovery and excluded from Git |
| ignored `vendor/python-libs` | package/directory version evidence, then reproducible reinstall | not byte-for-byte restorable; rebuilt from exact direct requirements and a resolved lock |
| old plans/reports | retained under `_docs/roadmap/archive`/`_docs/history` | evidence of intent only; not current behavior claims |
| cleanup quarantine | `_runtime/cleanup-quarantine-20260802` | preserved; not folded back into active code without evidence |

## Functional gaps found after file restoration

Restoring file bytes did not make every advertised workflow functional. The
audit found and repaired these consequential gaps:

- `vendor/` had package-shaped empty directories, which could import as false
  namespace packages. Dependency health now performs real imports and detects
  ABI/linker failures, and the launcher refuses an incompatible environment.
- The recovered Browser daemon treated a persistent `BrowserContext` as a
  `Browser`, depended on one personal profile path, and started a browser even
  for a shutdown request. It now defaults to a Playwright-managed Chromium,
  persists cookie/local-storage state under ignored runtime storage, supports
  explicit engine/executable overrides, and confines URL/body/generated paths.
- The collection `browser-use` branch returned a placeholder failure. It now
  executes the real Agent API, writes the actual final result, validates input,
  and falls back through the local daemon and static fetch paths with explicit
  error provenance.
- Scheduler `memory_review` reported success without performing work, and the
  reserved `pattern_extract` name had no execution branch. Memory review now
  performs a read-only task scan; the nonexistent job type is no longer
  advertised. Scheduler persistence, custom-call whitelisting, shutdown, and
  degraded-mode semantics were hardened.
- Memory output contained encoding-corrupted labels and ADR allocation was not
  safe across concurrent local processes. The module now writes clean UTF-8,
  uses atomic persistence and an allocation lock, reconciles a missing ignored
  counter against durable decisions, and exposes non-destructive health/review
  operations without automatically capturing conversations.

## Remaining recovery artifacts

`_runtime/module-recovery` and `_runtime/cleanup-quarantine-20260802` are kept as
local, ignored evidence. They should not be deleted until the owner has made an
independent backup and explicitly closes the incident. Their presence is not a
runtime dependency.
