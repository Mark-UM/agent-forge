---
description: React component example (function components, hooks, typed props)
language: typescript
task: coding
version: 1.5.0
---

# Example: React Component (TypeScript)

## Scenario
Build a `Counter` component with increment/decrement and display.

## Implementation

```tsx
// Counter.tsx
import { useState, useCallback } from 'react';

export interface CounterProps {
  initialValue?: number;
  step?: number;
  onChange?: (value: number) => void;
}

export function Counter({ initialValue = 0, step = 1, onChange }: CounterProps) {
  const [count, setCount] = useState(initialValue);

  const increment = useCallback(() => {
    setCount((prev) => {
      const next = prev + step;
      onChange?.(next);
      return next;
    });
  }, [step, onChange]);

  const decrement = useCallback(() => {
    setCount((prev) => {
      const next = prev - step;
      onChange?.(next);
      return next;
    });
  }, [step, onChange]);

  return (
    <div className="counter">
      <button onClick={decrement} aria-label="decrement">-</button>
      <span data-testid="count">{count}</span>
      <button onClick={increment} aria-label="increment">+</button>
    </div>
  );
}
```

## Test

```tsx
// Counter.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { Counter } from './Counter';

test('renders initial value', () => {
  render(<Counter initialValue={5} />);
  expect(screen.getByTestId('count')).toHaveTextContent('5');
});

test('increments by step', () => {
  render(<Counter step={2} />);
  fireEvent.click(screen.getByLabelText('increment'));
  expect(screen.getByTestId('count')).toHaveTextContent('2');
});

test('calls onChange on increment', () => {
  const onChange = vi.fn();
  render(<Counter onChange={onChange} />);
  fireEvent.click(screen.getByLabelText('increment'));
  expect(onChange).toHaveBeenCalledWith(1);
});
```

## Anti-patterns

```tsx
// DON'T: class component (use function components)
class Counter extends React.Component<CounterProps> { ... }

// DON'T: any-typed props
function Counter(props: any) { ... }

// DON'T: useEffect for derived state
function Total({ items }) {
  const [total, setTotal] = useState(0);
  useEffect(() => { setTotal(items.reduce(...)); }, [items]);  // unnecessary
  return <span>{total}</span>;
}
// DO: compute during render
function Total({ items }) {
  const total = useMemo(() => items.reduce(...), [items]);
  return <span>{total}</span>;
}

// DON'T: inline callbacks (causes re-renders)
<button onClick={() => setCount(count + 1)}>+</button>
// DO: useCallback (when passed as prop)
```

## Key Takeaways
- Function components + hooks only (no class components)
- Type all props with `interface`
- `useCallback` for handlers passed as props (perf)
- `useMemo` for expensive computations
- Derive state during render, not in effects
