---
description: Tower Stack 3D project-specific lessons and contracts
priority: 92
version: 2.0.0
trigger: when project root contains ".tower-stack" marker file, OR package.json "name" field equals "tower-stack-3d", OR any file path matches "src/game/Tower*"
---

# Tower Stack 3D — Project-Specific Rules

> **Scope**: Only the Tower Stack 3D project. These rules are derived from
> real production bugs in this codebase and do not apply elsewhere.
> General Three.js rules live in `threejs-game-loop.md`.

## Fatal Bugs Recorded (must not regress)

### Bug 1: `SoundManager.init()` defined but never called

**Root cause**: `SoundManager.init()` was defined in `src/audio/SoundManager.ts`
but never invoked from `main.ts` or any bootstrap path. Result: audio never
initialized, all `playSound()` calls silently no-op'd.

**Rule**:
- `SoundManager.init()` MUST be called from `main.ts` after
  `Renderer.init()` but before `core.start()`
- Bootstrap order in `main.ts` MUST be:
  1. `Renderer.init()`
  2. `SoundManager.init()`
  3. `EventBus.wire()`
  4. `core.start()`
- A regression test MUST exist that greps `main.ts` for `SoundManager.init()`
  and fails if absent

### Bug 2: `UIManager` rendered hardcoded HTML, bypassing Panel classes

**Root cause**: `UIManager.render()` built HUD HTML inline, ignoring the
Panel class hierarchy. Result: i18n switching didn't update HUD, score
updates required full re-render.

**Rule**: UIManager MUST NOT contain `innerHTML = '...'` with user-visible
text. All HUD elements MUST be Panel classes per `ui-components.md`.

### Bug 3: `PostProcessing` class existed but had no `EffectComposer`

**Root cause**: `src/render/PostProcessing.ts` declared a class with the
right name but only logged a constructor message. No bloom, no FXAA,
no composer. Result: scene rendered without advertised effects.

**Rule**: Any class named `PostProcessing` / `Effects` MUST instantiate
`EffectComposer` in its constructor or `init()` method.

### Bug 4: `(window as unknown as ...).startGame` global exposure

**Root cause**: `index.html` had `onclick="startGame()"` and UIManager
exposed `startGame` on `window`. Result: tight coupling to global scope,
untestable, bypassed EventBus contract.

**Rule**: No `onclick=` in `index.html`. All UI events go through
`addEventListener` in a Panel class, dispatching via EventBus.

## Project File Map

```
src/
├── core/              # Core layer (no three import)
│   ├── Game.ts
│   ├── Tower.ts
│   └── Input.ts
├── render/            # Render layer (three import OK)
│   ├── Renderer.ts
│   ├── PostProcessing.ts
│   └── meshes/
├── ui/                # UI layer (DOM + EventBus only)
│   ├── UIManager.ts
│   ├── panels/
│   │   ├── HUDPanel.ts
│   │   ├── ScorePanel.ts
│   │   └── MenuPanel.ts
│   └── components/
├── audio/
│   └── SoundManager.ts
├── i18n/
│   ├── index.ts
│   └── locales/
└── services/
    └── EventBus.ts
```

## Bootstrap Contract (main.ts)

```typescript
// main.ts — fixed bootstrap order
import { Renderer } from './render/Renderer';
import { SoundManager } from './audio/SoundManager';
import { EventBus } from './services/EventBus';
import { Game } from './core/Game';

async function main(): Promise<void> {
  await Renderer.init();          // 1. GPU context
  await SoundManager.init();      // 2. Audio context (was MISSING → Bug 1)
  EventBus.wire();                // 3. Event subscriptions
  const game = new Game();
  game.start();                   // 4. Game loop begins
}

main().catch((err) => {
  console.error('Bootstrap failed:', err);
});
```

## Regression Checklist

- [ ] `main.ts` calls `SoundManager.init()` before `game.start()`
- [ ] `UIManager` has no `innerHTML` with user-visible text
- [ ] `PostProcessing` (if present) instantiates `EffectComposer`
- [ ] `index.html` has no `onclick=` attributes
- [ ] No `(window as unknown as ...)` global exposure anywhere
