---
description: Type narrowing patterns (discriminated unions, type guards)
language: typescript
task: coding
version: 1.5.0
---

# Example: Type Narrowing in TypeScript

## Pattern 1: Discriminated Unions

```ts
type Result<T, E = Error> =
  | { status: 'ok'; value: T }
  | { status: 'error'; error: E };

function handleResult(result: Result<number, string>): number {
  if (result.status === 'ok') {
    // TypeScript narrows: result.value is number
    return result.value;
  }
  // TypeScript narrows: result.error is string
  throw new Error(result.error);
}
```

## Pattern 2: Type Guards (user-defined)

```ts
interface Cat { kind: 'cat'; meow(): void; }
interface Dog { kind: 'dog'; bark(): void; }
type Animal = Cat | Dog;

function isCat(animal: Animal): animal is Cat {
  return animal.kind === 'cat';
}

function makeSound(animal: Animal) {
  if (isCat(animal)) {
    animal.meow();  // narrowed to Cat
  } else {
    animal.bark();  // narrowed to Dog
  }
}
```

## Pattern 3: `in` operator narrowing

```ts
interface User { id: string; name: string; }
interface Admin extends User { permissions: string[]; }

function getPerms(entity: User | Admin): string[] {
  if ('permissions' in entity) {
    return entity.permissions;  // narrowed to Admin
  }
  return [];  // narrowed to User
}
```

## Pattern 4: `typeof` and `instanceof`

```ts
function process(value: string | number | Date) {
  if (typeof value === 'string') {
    return value.toUpperCase();  // narrowed to string
  }
  if (typeof value === 'number') {
    return value.toFixed(2);  // narrowed to number
  }
  // narrowed to Date
  return value.toISOString();
}
```

## Pattern 5: Exhaustiveness check with `never`

```ts
type Shape =
  | { kind: 'circle'; radius: number }
  | { kind: 'square'; size: number }
  | { kind: 'triangle'; base: number; height: number };

function area(shape: Shape): number {
  switch (shape.kind) {
    case 'circle':   return Math.PI * shape.radius ** 2;
    case 'square':   return shape.size ** 2;
    case 'triangle': return 0.5 * shape.base * shape.height;
    default:
      // Exhaustiveness check: if we add a new shape variant
      // without handling it here, TypeScript errors
      const _: never = shape;
      throw new Error(`Unknown shape: ${_}`);
  }
}
```

## Anti-patterns

```ts
// DON'T: type assertions instead of narrowing
const value: unknown = JSON.parse(input);
const name = (value as { name: string }).name;  // unsafe!

// DO: validate + narrow
const parsed = z.object({ name: z.string() }).safeParse(JSON.parse(input));
if (parsed.success) {
  const name = parsed.data.name;  // safely narrowed
}

// DON'T: optional fields for state machines
interface State {
  loading?: boolean;
  error?: string;
  data?: User;
}
// This allows loading + error + data all set — invalid states

// DO: discriminated unions
type State =
  | { status: 'loading' }
  | { status: 'error'; error: string }
  | { status: 'success'; data: User };
```

## Key Takeaways
- Discriminated unions model state machines safely
- Type guards (`x is T`) enable reusable narrowing
- `never` in default branches enforces exhaustiveness
- Validate unknown inputs at boundaries (zod)
- Prefer narrowing over type assertions (`as`)
