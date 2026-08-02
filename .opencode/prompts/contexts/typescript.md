---
description: TypeScript project conventions (strict mode, React, Node)
language: typescript
priority: 80
version: 1.5.0
---

# TypeScript Context

## Style
- `strict: true` in tsconfig.json (no implicit any, no implicit returns, strict null checks)
- camelCase for variables/functions, PascalCase for types/classes/components, UPPER_SNAKE for constants
- Prefer `interface` over `type` for object shapes; `type` for unions/intersections
- Use `const` by default, `let` only when reassignment needed, never `var`

## Project Conventions
- TypeScript 5.x+
- Target: ES2022+, module: ESNext, moduleResolution: Bundler
- Linting: ESLint + Prettier
- Testing: Vitest (preferred) or Jest
- HTTP: `fetch` (native) or `undici` for Node.js
- File naming: `kebab-case.ts` for modules, `PascalCase.tsx` for components

## Type System
- Avoid `any` — use `unknown` + type narrowing, or define proper types
- Avoid non-null assertion `!` — use optional chaining `?.` + nullish coalescing `??`
- Use `readonly` for immutable fields
- Use `as const` for literal type inference
- Discriminated unions over optional fields for state machines

## React (when applicable)
- Function components only, no class components
- Hooks: `useState`, `useEffect`, `useMemo`, `useCallback`
- Props: destructure with explicit interface
- State: lift only when siblings need it; prefer local state
- Effects: dependencies array must be exhaustive

## Anti-patterns
- `any` → `unknown` + narrowing
- `as` type assertions → type guards (`typeof`, `in`, `instanceof`)
- `enum` → union of string literals (better tree-shaking)
- `@ts-ignore` → fix the type error
- Default exports → named exports (better refactoring)

## Testing
- Test files: `*.test.ts` or `*.spec.ts` colocated with source
- Use `describe` / `it` / `expect`
- Mock with `vi.fn()` (Vitest) or `jest.fn()`
- Test behavior, not implementation
