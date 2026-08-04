---
description: Three.js game loop architecture — Core / Render / UI layer isolation
priority: 90
version: 2.0.0
trigger: when package.json contains "three" dependency, OR any file imports from "three", OR project root contains "three.config.json"
---

# Three.js Game Loop Architecture (enforced)

> **Scope**: Three.js-based games / visualizations. Loads only when the project
> actually depends on `three`. General anti-patterns live in `anti-patterns.md`.

## Layer Isolation

A Three.js project MUST be split into three layers:

```
┌─────────────────────────────────────────────┐
│ UI Layer      (DOM, panels, i18n, EventBus) │  ← may import three? NO
├─────────────────────────────────────────────┤
│ Render Layer  (Scene, Camera, Renderer,     │  ← may import three? YES
│                post-processing, meshes)     │
├─────────────────────────────────────────────┤
│ Core Layer    (game state, rules, physics,  │  ← may import three? NO
│                input, main loop update(dt)) │
└─────────────────────────────────────────────┘
```

### Forbidden cross-layer imports

- ❌ Core layer importing `three` / `document` / `window`
  (Core must be environment-agnostic and unit-testable without a DOM)
- ❌ Core layer importing from Render layer
  (Core dispatches state via EventBus; Render subscribes)
- ❌ UI layer importing from Render layer
  (UI talks to Core via EventBus; never touches meshes directly)
- ❌ `main.ts` directly calling Render layer methods
  (must go through EventBus or the main loop's `update(dt)`)

## Main Loop Contract

There MUST be exactly **one** `requestAnimationFrame` driver, owned by the
main loop. The main loop:

1. Computes `dt` from the previous frame timestamp
2. Calls `core.update(dt)` — pure state mutation, no rendering
3. Calls `renderer.render(scene, camera)` — pure rendering, no state mutation
4. Emits `frame:end` on EventBus (for UI panels to refresh)

### Forbidden loop patterns

- ❌ Renderer using its own `requestAnimationFrame` loop
  (causes double-rAF, desync from Core state)
- ❌ Multiple independent rAF loops (one per panel / one per effect)
- ❌ `setInterval` driving game logic (not synced with display refresh)
- ❌ `update(dt)` performing DOM writes (those belong to UI layer via EventBus)

## Post-Processing

- ❌ Class named `PostProcessing` / `Effects` / `Bloom` that does not
  instantiate `EffectComposer` (form compliance trap)
- ❌ Post-processing pass added to composer but never `.render()`-ed
- ❌ `EffectComposer` constructed but `renderToScreen` not set when used
  as the final pass

## Dispose / Cleanup

- ❌ `Geometry` / `Material` / `Texture` created but never `.dispose()`-ed
  on teardown (GPU memory leak)
- ❌ `renderer.dispose()` not called on scene teardown
- ❌ Event listeners on `EventBus` not removed on entity destruction
  (zombie handlers fire after the entity is gone)

## Self-Check Before Claiming Three.js Work Done

- [ ] Exactly one `requestAnimationFrame` driver in the project
- [ ] Core layer has zero imports of `three` / `document` / `window`
- [ ] `main.ts` does not call Render layer methods directly
- [ ] Every `Geometry` / `Material` / `Texture` has a matching `.dispose()`
- [ ] `EffectComposer` (if present) is actually rendered each frame
- [ ] No panel or effect runs its own rAF loop
