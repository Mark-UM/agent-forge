---
description: Cross-language anti-pattern blacklist (Phase 1 Direction 6)
priority: 95
version: 2.1.0
trigger: when any code language is detected (python/typescript/haskell/java/cpp)
---

# Anti-Pattern Blacklist (cross-language, enforced)

> **Source**: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 6
> **Scope**: Only rules that apply to **all** languages. Language-specific rules
> live in their own context files and are loaded only when that context is active.

## Implementation Anti-patterns (defeats "form compliance trap")

These apply to every language. They catch the "looks done but isn't" pattern
that flawed models use to escape scrutiny.

- ❌ Empty function body (`{}` / `pass` / only `console.log` / only `return`)
  without `// TODO:` / `# TODO:` describing pending work
- ❌ Hook / callback / lifecycle method that returns its input unchanged
  (pretends to implement a transform but does nothing)
- ❌ Class name promising capability but no real implementation
  (e.g., `PostProcessing` class without any post-processing call,
  `Validator` class that always returns `True`)
- ❌ File exists but content is Stub / Placeholder
  (unless spec doc explicitly allows it)
- ❌ Public API function that silently swallows all exceptions and returns
  a default value without logging or surfacing the failure

## Integration Anti-patterns (defeats "integration link breakage")

Cross-language rules for wiring. Project-specific examples live in their
own dedicated context files (loaded only when the project matches).

- ❌ Method named `init()` / `load*()` / `setup*()` / `register*()` defined
  but never invoked in any bootstrap path
- ❌ Event / message `emit` / `publish` / `dispatch` with no matching
  `on` / `subscribe` / `listen` anywhere in the codebase
- ❌ `manifest.json` / `package.json` / `pyproject.toml` referencing
  non-existent resource files (icons, entry points, modules)
- ❌ Import of a module whose only export is re-exported unused
  (dead integration link)
- ❌ Configuration key read at runtime but never written by any path
  (orphaned config)

## Type Safety Anti-patterns (general)

Applies to any language with a type system (TS, Haskell, Java, C++, typed Python).

- ❌ `any` / `as any` / `<any>` / `Object` / `dynamic` /
  `typing.Any` used to silence a type error rather than fix it
- ❌ Non-null assertion `!` (TS) or `!!` cast to bypass null checks —
  use optional chaining / `Maybe` / `Optional` / proper narrowing
- ❌ Suppression directives (`@ts-ignore`, `# type: ignore`,
  `@SuppressWarnings`, `{-# OPTIONS_GHC -Wno-... #-}`)
  without an accompanying issue link or justification comment
- ❌ Default exports in TS/JS (use named exports for refactoring & tree-shaking)
- ❌ `enum` in TS (use union of string literals for tree-shaking)

## Python-Specific (extends `contexts/python.md`)

- ❌ `import *` (use explicit imports)
- ❌ `eval()` / `exec()` (use `ast.literal_eval` for safe literal parsing)
- ❌ Bare `except:` (catch specific exceptions)
- ❌ `os.system()` (use `subprocess.run()` with list args, `shell=False`)
- ❌ Non-atomic file writes for persistent state — use `tmp + os.replace`
  (Phase 0 B2 fix)
- ❌ Hardcoded Windows paths — use `sys.executable` or `pathlib.Path`
- ❌ `requests` library in core modules — use `urllib.request`
  (zero-dep principle)
- ❌ `str.startswith` for path containment check — use
  `realpath + os.sep` (Phase 0 B3 fix)
- ❌ `dict.get()` on tuple return value — unpack tuples explicitly
  (Phase 0 B1 fix)

## Self-Check Before Claiming Task Done

- [ ] No empty function bodies without `TODO`
- [ ] Every `init()` / `load*()` / `setup*()` defined method is invoked somewhere
- [ ] Every event `emit` has at least one `on` subscriber
- [ ] No `any` / `as any` / `type: ignore` in new code without justification
- [ ] No config key read but never written
