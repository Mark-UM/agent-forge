---
description: Test quality gate — no soft assertions, boundary coverage required (Phase 1 Direction 5)
priority: 88
version: 2.0.0
trigger: when test files (*.test.ts, *.spec.ts, test_*.py, *_test.py) are touched
---

# Test Quality Gate (enforced)

> **Source**: `opencode升级优化方向最终总结文档.md` Section 3.2 Direction 5
> **Root cause addressed**: deepseek's "coverage-farming tests" — writing tests
> that pass by construction (`toContain(knownValue)`) rather than tests that
> verify behavior at boundaries.

## Banned Soft Assertion Patterns

These assertions pass by construction and provide no real verification:

- ❌ `expect(array).toContain(knownValue)` — when `knownValue` is a literal you
  just put into the array, this is tautological. Use `toBe(expectedArray)`.
- ❌ `expect(result).toBeTruthy()` — too lenient; passes for any non-empty string,
  any non-zero number, any object. Use semantic assertion.
- ❌ `expect(result).toBeDefined()` — too lenient; passes for `0`, `""`, `null`,
  `false`. Use exact value.
- ❌ `expect(result).toBeGreaterThan(0)` — when `0` is the only impossible value,
  this is meaningless. Use `toBe(expected)` or assert a specific semantic property.
- ❌ `expect(typeof result).toBe('object')` — too lenient; passes for `null`,
  arrays, any object. Use structural assertion.
- ❌ `expect(result).not.toBeNull()` — only asserts non-null, says nothing about
  correctness. Use `toBe(expected)` or assert a property.

## Required Precise Assertions

- ✅ `expect(result).toBe(expectedExactValue)` — exact equality
- ✅ `expect(result.score).toBe(42)` — exact property
- ✅ `expect(result.state).toBe('playing')` — exact enum value
- ✅ `expect(result).toEqual({ a: 1, b: 2 })` — deep equality on expected shape
- ✅ `expect(() => fn()).toThrow(SpecificError)` — exception type + (optionally) message

## Boundary Coverage Rule

For any threshold / saturation / critical value in the spec, MUST test three tiers:

| Tier | Value | Purpose |
|------|-------|---------|
| Below | threshold − 0.1% | Behavior just before boundary |
| At | threshold exact | Behavior at the boundary |
| Above | threshold + 0.1% | Behavior just after boundary |

### Example (Tower Stack 3D — perfect placement threshold = 5%):

```typescript
// ✅ Three-tier boundary test
it.each([
  [0.049, 'perfect'],  // below threshold (4.9%) → perfect
  [0.050, 'perfect'],  // at threshold (5.0%)    → perfect
  [0.051, 'good'],     // above threshold (5.1%) → good (not perfect)
])('overlap %f results in %s cut', (overlap, expected) => {
  const result = engine.calculateCut(overlap);
  expect(result.type).toBe(expected);  // ✅ precise
});
```

```typescript
// ❌ Banned: soft assertion that passes regardless
it('calculates cut type', () => {
  const result = engine.calculateCut(0.05);
  expect(['perfect', 'good', 'miss']).toContain(result.cut.type);  // always passes
});
```

## Project-Specific Boundaries (extend per project)

Generic list — each project adds its own boundaries based on spec:

- [ ] Zero-boundary: `count = 0` / `count = -1` (negative)
- [ ] Saturation: `value = max` / `value = max + 1` (overflow)
- [ ] Empty input: `''` / `[]` / `null` / `undefined`
- [ ] State machine: all illegal transitions return error (not silent no-op)
- [ ] `localStorage` / `sessionStorage` unavailable → graceful fallback
- [ ] Async rejection paths: network failure, timeout, malformed response
- [ ] Numeric overflow: `Number.MAX_SAFE_INTEGER`, `Infinity`, `NaN`

## Coverage Configuration Rule

- `vitest.config.ts` (or equivalent) `coverage.thresholds` MUST be set:
  - `statements: 85`, `branches: 85`, `functions: 85`, `lines: 85` (minimum)
- `coverage.include` MUST be `['src/**/*.ts']` (full source tree),
  NEVER `['src/core/**/*.ts']` (partial — inflates coverage by excluding UI/Render)
- Test file extension: pick ONE of `.test.ts` or `.spec.ts` per project,
  do NOT mix both in the same project
- Test directory: pick ONE of `co-located` (`*.test.ts` next to source)
  or `centralized` (`tests/**/*.test.ts`), do NOT mix

## TDD Compliance (extends `tasks/coding.md`)

- Red before green: write the failing test FIRST, then implementation
- Vertical slices: one test → one implementation → repeat
- Tests live at seams (public interface), never against internals
- No mocks of the system under test — mock only external collaborators
- No `spyOn` of the method being tested — that's testing the mock, not the code

## Test Self-Check Before Committing

- [ ] No soft assertions (`toContain`, `toBeTruthy`, `toBeDefined`) on known values
- [ ] Every threshold / boundary has 3-tier tests (below / at / above)
- [ ] Coverage thresholds configured in vitest.config.ts (≥85% all dimensions)
- [ ] Coverage scope is full `src/`, not partial
- [ ] Test extension is consistent across the project (`.test.ts` XOR `.spec.ts`)
- [ ] Each test has a single logical assertion (one `it` → one behavior)
- [ ] Tests do not depend on execution order (no shared mutable state)
