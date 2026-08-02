# Historical Postmortem: Tower Stack 3D Non-Responsive Button Bug

> This incident report is retained as historical engineering context. It does
> not describe the current AgentForge architecture or current test status.

> **Date**: 2026-07-20
> **Project**: `E:\system_folder\EX game(ds)` — Tower Stack 3D (TypeScript + Three.js + Vite)
> **Bug Severity**: Critical (all UI interaction blocked)
> **Root Cause Complexity**: Trivial (7 inconsistent string keys)
> **Detection Cost**: ~30 files audited, ~2500 lines read, 6 rounds of analysis

---

## 1. Bug Summary

After the game loaded, clicking "Start"/"Endless"/"Time Attack" buttons on the main menu produced **zero response** — no state transition, no countdown, no gameplay. The game was visually complete but functionally dead.

**Impact**: All 7 buttons (Start, Endless, Time Attack, Resume, Restart, Menu, Result Menu) were non-functional across all game screens.

---

## 2. Root Cause

**File**: [`src/ui/UIManager.ts`](file:///E:/system_folder/EX%20game(ds)/src/ui/UIManager.ts)

Two methods in the same class used **different naming conventions** for the same button entities:

### `cacheElements()` — stores with **camelCase** keys

```typescript
this.elements = {
  btnStart:        document.getElementById('btn-start'),
  btnEndless:      document.getElementById('btn-endless'),
  btnTimeAttack:   document.getElementById('btn-time-attack'),
  btnResume:       document.getElementById('btn-resume'),
  btnRestart:      document.getElementById('btn-restart'),
  btnMenu:         document.getElementById('btn-menu'),
  btnResultMenu:   document.getElementById('btn-result-menu'),
};
```

### `bindButtons()` — passes **kebab-case** strings

```typescript
this.onClick('btn-start',       () => this.eventBus.emit('input:action', { action: 'start' }));
this.onClick('btn-endless',     () => this.eventBus.emit('input:action', { action: 'endless' }));
this.onClick('btn-time-attack', () => this.eventBus.emit('input:action', { action: 'time-attack' }));
// ... (7 buttons total)
```

### `onClick()` — looks up by key, fails silently

```typescript
private onClick(id: string, handler: () => void): void {
  const el = this.elements[id];
  if (el) {                              // ← 'btn-start' !== 'btnStart' → undefined → skipped
    el.addEventListener('click', handler);
  }
  // No else branch. No warning. No error.
}
```

**Result**: `this.elements['btn-start']` returns `undefined` because the key is `btnStart`. The `if (el)` guard silently skips all 7 buttons. Zero event listeners are ever attached.

---

## 3. Detection Timeline

| Round | Actions | Time Cost | Effectiveness |
|-------|---------|-----------|---------------|
| 1 | Read main.ts, InputHandler.ts, UIManager.ts — traced init flow | Medium | Found correct flow, eliminated init errors |
| 2 | Read style.css, index.html — checked CSS z-index/display/overlay | Medium | Eliminated CSS overlay blocking |
| 3 | Read EventBus.ts, GameStateMachine.ts — traced event pipeline | Low | Validated event mechanism is correct |
| 4 | Read ALL remaining source files (30+ files, ~2500 lines) | Very High | Found nothing; all logic correct |
| 5 | `npm run build` succeeded, `tsc --noEmit` clean — runtime edge? | Low | Confirmed no compile errors |
| 6 | Playwright attempt — Dev server instability, browser flakiness | High | Inconclusive |
| 7 | **Re-read UIManager.ts side-by-side: cacheElements() vs bindButtons()** | Low | **ROOT CAUSE FOUND** |

**Efficiency ratio**: ~30 files / 2500 lines read across 6 rounds to find a bug that is **one diff** — changing 7 hyphenated strings to camelCase.

---

## 4. Why Was It Missed? — Agent Process Flaws

### 4.1 Visual Similarity Blindness

`cacheElements()` stored `btnStart`, `bindButtons()` passed `'btn-start'`. The difference is a single hyphen + capitalization. When scanning code, the brain matches `'btn-start'` against the HTML `id="btn-start"` (correct) and **stops there** — never cross-referencing against the JavaScript key `btnStart`.

**Root flaw**: The agent performed "code ↔ HTML" validation but skipped "code ↔ code" validation. Two adjacent methods in the same file referencing the same entities were never compared against each other.

### 4.2 Complexity Bias in Well-Structured Code

The project has a clean architecture: `EventBus`, `GameStateMachine`, `ModeManager`, `InputHandler`, separate `render/`, `audio/`, `services/` directories. TypeScript strict mode. Consistent naming. This professional structure created a **presumption of baseline correctness** — the agent's analysis gravitated toward complex hypotheses:

- CSS `z-index` / `pointer-events` blocking clicks
- Three.js canvas DOM insertion order
- Module initialization race conditions
- Runtime errors from missing dependencies

Each hypothesis was investigated exhaustively and eliminated, but the agent never stepped back and asked: *"Could it just be a typo?"*

### 4.3 Silent Failure Antipattern

```typescript
const el = this.elements[id];
if (el) {
  el.addEventListener('click', handler);
}
```

This is textbook defensive programming, but it became a **bug concealment mechanism**. No `else` branch, no `console.warn`, no error thrown. The failure was completely invisible:

- ❌ No browser console error
- ❌ No TypeScript compilation error (string keys are valid at compile time)
- ❌ No runtime crash
- ❌ No visual indication

**Rule**: Every silent `if (x)` guard that can fail should log. `if (!el) { console.warn(`UIManager: element '${id}' not found`); return; }` would have surfaced this bug in **30 seconds**.

### 4.4 Incomplete Verification Chain

The button click path has 3 segments:

```
[1] DOM click → UIManager.onClick()
[2] UIManager.onClick() → EventBus.emit()
[3] EventBus → Game.handleInput()
```

The agent exhaustively verified segments [2] and [3]:

- ✅ EventBus emit/on mechanism — correct
- ✅ Game.handleInput() state checking — correct
- ✅ State transitions — all valid

But segment [1] was **never instrumentally verified**. The agent read `onClick()` and saw it "looked right" — correct logic, correct DOM API usage — but never asked: *"What value does the `id` parameter actually hold when called?"*

### 4.5 Missing Runtime Instrumentation

Static analysis consumed 6 rounds before any runtime probe was attempted. A single `console.log(Object.keys(this.elements))` or `console.trace()` in `onClick()` would have revealed the mismatch immediately. By the time Playwright was attempted, tool/environment instability further delayed verification.

**Rule**: When static analysis fails to locate a bug after 2 rounds, **inject a runtime diagnostic** before continuing static analysis. One `console.log` beats reading 30 files.

### 4.6 Tool Chain Friction Amplified the Delay

When the agent finally attempted runtime verification via Playwright, the environment was hostile:

- Vite dev server killed by bash timeout on repeated starts
- `Start-Process`, `Start-Job`, `cmd /c start /b` all failed or produced orphaned processes
- Playwright pages navigated to `about:blank` despite confirmed HTTP 200

This is a meta-problem: the agent's verification tooling was unreliable in this specific environment (Windows PowerShell + npx-based server), and the agent had no fallback strategy beyond retrying.

---

## 5. Agent Process Improvements

### 5.1 New Static Analysis Check: Cross-Reference String Key Maps

**Rule**: When a class has a key-value map (object, Map, WeakMap) and methods that index into it by string literal, **every literal string used as a key must be validated against the map's key set**.

Implementation:
- Flag any class that has both (a) a property assignment with multiple key-value pairs and (b) bracket-notation access with string literal keys
- Extract the key set from the map assignment and diff against the string literals in accessors
- Report mismatches

This would have caught the bug in **one pass** without reading any other file.

### 5.2 "Assume the Simplest Bug First" Checkpoint

After eliminating 3+ complex hypotheses with no result, insert a mandatory checkpoint:

> "Have I checked for typographical errors in string keys, variable names, or conditionals?"

This checkpoint should trigger at the **third elimination round** — not after the sixth. It costs negligible time and catches the highest-frequency bugs.

### 5.3 Silent Failure Detector

**Rule**: Flag every pattern of the form:

```typescript
if (x) {
  // use x
}
// no else, no console.warn, no throw
```

where `x` is the result of a lookup operation (`obj[key]`, `map.get()`, `getElementById()`, `querySelector()`). These should either:
- Have an `else` branch with at minimum a `console.warn`
- Use a `try/catch` with error logging
- Throw if the lookup is expected never to fail

The agent should flag these during code review and suggest instrumentation, even if no bug is currently suspected.

### 5.4 Runtime-First Verification for Input/Event Bugs

When the symptom is "X happens but nothing responds" (click, keypress, form submit, API call):

| Priority | Approach |
|----------|----------|
| **1st** | Inject `console.log` at the entry point of the event handler chain |
| **2nd** | Check browser DevTools console for errors |
| 3rd | Static analysis of event wiring |

Runtime probes are cheaper and more definitive than static analysis for event-related bugs. One `console.log` in `onClick()` trumps reading 30 files.

### 5.5 Post-Static-Analysis Regression Checklist

After any bugfix where the root cause was a **naming inconsistency** (camelCase vs kebab-case, snake_case vs camelCase, renamed variables, etc.), the agent should run a project-wide check:

1. Search for all `getElementById()` / `querySelector()` calls
2. Cross-reference IDs against all map/dictionary key sets in the same module
3. Flag any ID string that exists in HTML but not in JS key maps (or vice versa)

### 5.6 Verification Tooling Resilience

The Playwright failure cascade revealed a fragility: when the primary verification tool fails, the agent had no graduated fallback. Proposed improvement:

- **Tier 1**: Playwright (full browser automation)
- **Tier 2**: Direct HTTP check (`Invoke-WebRequest` or `curl`) + static assertions
- **Tier 3**: Inject a diagnostic script into `index.html` that self-tests button bindings and writes results to the console; rebuild and verify via Node.js import resolution

Each tier degrades gracefully — even Tier 3 provides more certainty than pure static analysis.

---

## 6. Code-Side Recommendations

These are suggestions for the **game project**, not modifications to be applied now:

### 6.1 Replace Silent `if (el)` with Logged Guard

```typescript
private onClick(id: string, handler: () => void): void {
  const el = this.elements[id];
  if (!el) {
    console.warn(`UIManager: element '${id}' not found in cache`);
    return;
  }
  el.addEventListener('click', handler);
}
```

### 6.2 Use Enum or Constant Map for Button IDs

Eliminate string duplication entirely:

```typescript
const BTN = {
  START: 'btnStart',
  ENDLESS: 'btnEndless',
  TIME_ATTACK: 'btnTimeAttack',
  // ...
} as const;
```

Then `cacheElements()` and `bindButtons()` both reference `BTN.START`, making inconsistency impossible.

### 6.3 Add Integration Test for Button Wiring

A simple test that imports UIManager and verifies every expected button element has a click listener attached would catch regressions in CI.

---

## 7. Conclusion

**The bug was 7 inconsistent strings. The analysis consumed 30 files, 2500 lines, and 6 rounds.**

This post-mortem documents a specific failure mode in AI-assisted debugging: when all evidence points to "correct code," the agent does not naturally revert to checking the simplest possible failure — a typo. The improvements proposed in Section 5 are designed as **process-level guardrails** to catch this class of bug earlier, regardless of the project or language.

This document serves as a reference for future sessions: when a "non-responsive" bug is reported, the agent should check Section 5.4 (runtime-first verification) before engaging in full-codebase static analysis.

---

*Generated by OpenCode (Sisyphus orchestration) as part of the Tower Stack 3D debugging session.*
