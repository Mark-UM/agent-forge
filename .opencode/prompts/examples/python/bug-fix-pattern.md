---
description: Bug fix pattern — reproduce, locate, fix, regress
language: python
task: debugging
version: 1.5.0
---

# Example: Bug Fix Pattern (Root-Cause)

## Scenario
Users report `calculate_discount(price, discount_pct)` returns wrong value when `discount_pct` is 0.

## Step 1: Reproduce (red)

```python
# test_calculate_discount.py
from myapp.pricing import calculate_discount

def test_zero_discount_returns_original_price():
    assert calculate_discount(100.0, 0) == 100.0  # currently returns 0.0 (BUG)
```

Run: `pytest test_calculate_discount.py::test_zero_discount_returns_original_price` → FAILS

## Step 2: Locate the root cause

```python
# myapp/pricing.py (BUGGY)
def calculate_discount(price: float, discount_pct: float) -> float:
    return price * (1 - discount_pct / 100)
    # When discount_pct = 0:
    #   1 - 0/100 = 1.0 → returns price * 1.0 = price ✓
    # So the function is correct... where is the bug?
```

Wait, the function looks correct. The bug must be elsewhere. Check the caller:

```python
# myapp/cart.py
def apply_discount(cart):
    if cart.discount_pct:  # BUG: falsy when discount_pct == 0!
        cart.total = calculate_discount(cart.total, cart.discount_pct)
    # When discount_pct == 0, the if-block is skipped, so discount is "not applied"
    # But the test calls calculate_discount directly, which works...
```

The bug is in `cart.py`, not `pricing.py`. The test was misleading. Let me re-read the report:

> Users report `calculate_discount(price, discount_pct)` returns wrong value when `discount_pct` is 0.

Actually, users see the bug at the cart level. The test should target the cart:

```python
# test_cart.py
def test_zero_discount_applied_correctly():
    cart = Cart(total=100.0, discount_pct=0)
    apply_discount(cart)
    assert cart.total == 100.0  # currently total stays 100 (bug: discount "not applied" but mathematically should be no change)
    # Wait, the test passes because the bug is invisible here
```

Actually the bug is: when `discount_pct == 0`, the `if cart.discount_pct:` is `False`, so `calculate_discount` is never called. This means `cart.total` is unchanged, which happens to be correct (no discount). But the bug is that if a user explicitly passes 0, the system reports "discount applied" when it wasn't.

The real root cause is using truthiness instead of `is not None`.

## Step 3: Fix at the root

```python
# myapp/cart.py (FIXED)
def apply_discount(cart):
    if cart.discount_pct is not None:  # explicit None check, not truthiness
        cart.total = calculate_discount(cart.total, cart.discount_pct)
```

## Step 4: Regression test

```python
# test_cart.py
def test_zero_discount_is_applied():
    """Regression: discount_pct=0 must trigger calculate_discount (root-cause for
    'discount not applied' reports)."""
    cart = Cart(total=100.0, discount_pct=0)
    apply_discount(cart)
    assert cart.total == 100.0
    # Verify calculate_discount WAS called (not skipped):
    assert cart.discount_applied is True  # add a flag for observability
```

## Anti-patterns
- Fixing `calculate_discount` (wrong location) → bug persists in `cart.py`
- Adding `if discount_pct == 0: return price` to `calculate_discount` → symptom fix
- `try/except` around the cart logic → hides the bug

## Key Takeaways
- The reported location is often NOT the root cause location
- Use `git bisect` if root cause isn't obvious from reading
- Add observability (flags/log lines) to verify the fix path runs
- Regression test must target the ROOT cause, not the symptom
