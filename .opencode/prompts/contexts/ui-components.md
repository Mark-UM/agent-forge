---
description: UI component enforcement rules — loaded when UI files are touched
priority: 96
version: 2.0.0
trigger: when file path matches src/ui/**, src/components/**, or src/panels/**
---

# UI Component Architecture (enforced)

> **Source**: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 7
> **Skill**: `ui-component-enforcer` (Phase 3D)

## Mandatory Rules

### 1. Directory Structure

- `src/ui/panels/` directory MUST exist
- `src/ui/components/` directory MUST exist
- Each Panel/Component is a separate `.ts` file in the appropriate directory

### 2. Panel Class Pattern

Each Panel MUST:
- Be a `class` extending `BasePanel`
- Accept `HTMLElement` reference in constructor (no `getElementById` inside the class)
- Subscribe to EventBus events in constructor (not in render method)
- Implement `refreshText()` for i18n locale switching

✅ Good — Panel extends BasePanel, constructor injection, EventBus subscription:

```typescript
// src/ui/panels/HUDPanel.ts
import { BasePanel } from './BasePanel';
import { EventBus } from '@/services/EventBus';
import { t } from '@/i18n';

export class HUDPanel extends BasePanel {
  constructor(el: HTMLElement) {
    super(el);
    EventBus.on('score:update', (p: { score: number }) => this.onScore(p.score));
  }

  private onScore(score: number): void {
    this.el.textContent = t('hud.score', { value: score });
  }

  refreshText(): void {
    this.el.textContent = t('hud.score');
  }
}
```

❌ Bad — Hardcoded HTML in UIManager, no Panel class, no i18n (form compliance trap):

```typescript
// src/ui/UIManager.ts — FORBIDDEN
export class UIManager {
  render() {
    const el = document.getElementById('app');
    if (el) {
      el.innerHTML = `
        <div class="hud">
          <span>Score: 0</span>
          <button onclick="startGame()">Start</button>
        </div>
      `;
    }
  }

  exposeGlobals() {
    (window as unknown as Record<string, unknown>).startGame = () => {
      console.log('starting');
    };
  }
}
```

### 3. Forbidden Patterns

- ❌ `onclick="..."` in `index.html` — use `addEventListener` in a Panel class
- ❌ `document.getElementById('...')` in Panel/Component classes — pass el via constructor
- ❌ `(window as unknown as ...)` global exposure — use EventBus
- ❌ `innerHTML = '...hardcoded text...'` — use `t('key')` for all user-visible text
- ❌ UIManager directly rendering panel HTML — delegate to Panel classes

### 4. i18n Wiring

- UIManager MUST import `{ t, setLocale }` from `@/i18n`
- All user-visible text MUST be wrapped in `t('key')`
- `setLocale()` MUST trigger `refreshText()` on all registered Panels

### 5. EventBus Integration

- Panels communicate with business layer ONLY via EventBus
- UIManager MUST NOT call `engine.start()` or `engine.pause()` directly — emit events instead
- Business layer MUST NOT call `panel.render()` directly — emit events instead

## Self-Check Before Claiming UI Done

- [ ] `src/ui/panels/` directory exists
- [ ] `src/ui/components/` directory exists
- [ ] Each Panel is a class extending `BasePanel`
- [ ] No `onclick=` in `index.html`
- [ ] No `(window as unknown as ...)` in any UI file
- [ ] `UIManager.ts` imports `{ t, setLocale }` from `@/i18n`
- [ ] All user-visible text uses `t('key')`
- [ ] `setLocale()` triggers `refreshText()` on all panels
