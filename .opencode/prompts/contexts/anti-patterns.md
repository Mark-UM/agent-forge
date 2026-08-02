---
description: Anti-pattern blacklist enforced across all code generation (Phase 1 Direction 6)
priority: 95
version: 2.0.0
trigger: when any code language is detected (python/typescript/haskell/java/cpp)
---

# Anti-Pattern Blacklist (enforced, no exceptions)

> **Source**: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 6
> **Root cause addressed**: deepseek's "explicit constraint bypass" pattern — treating
> constraints as obstacles to disable rather than baselines to satisfy.

## Engineering Anti-patterns
- ❌ ESLint rule explicitly set to `'off'` without accompanying `// TODO(<issue>):` comment + issue link
- ❌ `// eslint-disable-next-line` / `@ts-expect-error` without TODO + issue link in same comment block
- ❌ Missing `ESLint` / `Prettier` / `husky` / `CI` config files when project has `package.json`
- ❌ Vite config with `minify: 'terser'` but missing `drop_console` / `drop_debugger` / `manualChunks`
- ❌ `tsconfig.json` without `strict: true` (or with strict flags individually disabled)

## Type Safety Anti-patterns (TS/JS)
- ❌ `(window as unknown as Record<string, unknown>)` global function exposure — use EventBus instead
- ❌ `any` / `as any` / `<any>` — use `unknown` + type narrowing, or define proper types
- ❌ `@ts-ignore` (use `@ts-expect-error` with issue link if absolutely necessary)
- ❌ Default exports (use named exports for better refactoring & tree-shaking)
- ❌ `enum` (use union of string literals)
- ❌ Non-null assertion `!` (use optional chaining `?.` + nullish coalescing `??`)

## Architecture Anti-patterns
- ❌ UI layer exposing methods via `window.*` globals (must use EventBus)
- ❌ Renderer using independent `requestAnimationFrame` (must go through main loop `update(dt)`)
- ❌ `main.ts` directly calling Render layer methods (must go through EventBus)
- ❌ Core layer importing `three` / `document` / `window` (must be environment-agnostic)
- ❌ State mutation outside the state machine (must dispatch via state machine transitions)

## Implementation Anti-patterns (defeats "form compliance trap")
- ❌ Empty function body (`{}` or only `console.log`) without `// TODO:` describing pending work
- ❌ Hook empty implementation (e.g., `onPlacement() { return scores; }` — does nothing)
- ❌ Class name promising capability but no real implementation
  (e.g., `PostProcessing` class without `EffectComposer`)
- ❌ File exists but content is Stub / Placeholder (unless spec doc explicitly allows it)

## Integration Anti-patterns (defeats "integration link breakage")
- ❌ Method named `init()` / `load*()` / `setup*()` defined but never invoked in any bootstrap path
  (Tower Stack 3D fatal bug: `SoundManager.init()` defined but never called from `main.ts`)
- ❌ Event `emit` with no matching `on` / `subscribe` anywhere in the codebase
  (EventBus treated as message queue instead of state sync contract)
- ❌ `manifest.json` / `package.json` referencing non-existent resource files
  (form compliance: file exists but icons are missing on disk)

## Python-Specific (extends `contexts/python.md`)
- ❌ `import *` (use explicit imports)
- ❌ `eval()` / `exec()` (use `ast.literal_eval` for safe literal parsing)
- ❌ Bare `except:` (catch specific exceptions)
- ❌ `os.system()` (use `subprocess.run()` with list args, `shell=False`)
- ❌ Non-atomic file writes for persistent state — use `tmp + os.replace` (Phase 0 B2 fix)
- ❌ Hardcoded Windows paths — use `sys.executable` or `pathlib.Path`
- ❌ `requests` library in core modules — use `urllib.request` (zero-dep principle)
- ❌ `str.startswith` for path containment check — use `realpath + os.sep` (Phase 0 B3 fix)
- ❌ `dict.get()` on tuple return value — unpack tuples explicitly (Phase 0 B1 fix)

## Self-Check Before Claiming Task Done
- [ ] No `: 'off'` ESLint overrides without TODO+issue
- [ ] No `any` / `as any` in new code
- [ ] No empty function bodies
- [ ] Every `init()` / `load*()` defined method is invoked somewhere
- [ ] Every event `emit` has at least one `on` subscriber
- [ ] No `window.*` global exposure from UI layer
