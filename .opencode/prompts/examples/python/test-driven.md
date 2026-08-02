---
description: TDD example — red-green-refactor with pytest
language: python
task: coding
version: 1.5.0
---

# Example: TDD with pytest

## Scenario
Add a `fibonacci(n)` function that returns the nth Fibonacci number.

## Step 1: Red (failing test)

```python
# test_fibonacci.py
import pytest
from myapp.fibonacci import fibonacci

def test_fibonacci_base_cases():
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1

def test_fibonacci_recursive():
    assert fibonacci(10) == 55
    assert fibonacci(20) == 6765

def test_fibonacci_negative_raises():
    with pytest.raises(ValueError):
        fibonacci(-1)
```

Run: `pytest test_fibonacci.py` → fails (module doesn't exist)

## Step 2: Green (minimal implementation)

```python
# myapp/fibonacci.py
def fibonacci(n: int) -> int:
    if n < 0:
        raise ValueError("n must be non-negative")
    if n < 2:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
```

Run: `pytest test_fibonacci.py` → passes

## Step 3: Refactor (only if needed)

No refactor needed — implementation is already clean.

## Anti-pattern (what NOT to do)

```python
# DON'T: speculative generality
def fibonacci(n, *, memoize=True, cache_size=1000, algorithm="iterative"):
    # over-engineered for a 3-line function
    ...

# DON'T: implementation-coupled test
def test_fibonacci_uses_loop():
    # tests internal implementation, breaks on refactor
    fib = Fibonacci()
    with mock.patch.object(fib, '_loop') as m:
        fib.compute(10)
        assert m.called
```

## Key Takeaways
- Test at the public seam (the function), not internals
- One test → one implementation slice (vertical)
- No speculative features (memoize/cache_size/algorithm)
- Refactor only after green, never during
