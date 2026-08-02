---
description: Refactoring pattern — extract method
language: python
task: coding
version: 1.5.0
---

# Example: Extract Method Refactoring

## Scenario
A function that prints an invoice has too many responsibilities. Extract methods to clarify intent.

## Before

```python
def print_invoice(order):
    # Print header
    print("=== Invoice ===")
    print(f"Customer: {order.customer_name}")
    print(f"Date: {order.date}")
    print()

    # Print items
    total = 0
    for item in order.items:
        print(f"  {item.name}: ${item.price:.2f} x {item.quantity}")
        total += item.price * item.quantity
    print()

    # Print summary
    print(f"Subtotal: ${total:.2f}")
    tax = total * 0.1
    print(f"Tax (10%): ${tax:.2f}")
    print(f"Total: ${total + tax:.2f}")
```

## After (extracted)

```python
def print_invoice(order):
    _print_header(order)
    _print_items(order.items)
    _print_summary(order.items)


def _print_header(order):
    print("=== Invoice ===")
    print(f"Customer: {order.customer_name}")
    print(f"Date: {order.date}")
    print()


def _print_items(items):
    for item in items:
        print(f"  {item.name}: ${item.price:.2f} x {item.quantity}")
    print()


def _print_summary(items):
    total = sum(item.price * item.quantity for item in items)
    print(f"Subtotal: ${total:.2f}")
    tax = total * 0.1
    print(f"Tax (10%): ${tax:.2f}")
    print(f"Total: ${total + tax:.2f}")
```

## Refactor Steps
1. Identify a cohesive block (e.g., "print header")
2. Extract to a new function with a name that describes what it does
3. Pass only needed parameters (not the whole order if it only uses name/date)
4. Repeat for each cohesive block
5. Run tests after each extraction (don't batch refactors)

## Anti-patterns
- Extracting too early (before tests exist) → can't verify behavior preserved
- Passing `self` or whole objects when only 1-2 fields needed → tight coupling
- Naming the extracted method `helper` or `util` → name should describe intent
- Extracting multiple blocks in one commit → hard to review

## Key Takeaways
- Extract method is the most common refactoring; master it first
- Each extracted function should do ONE thing
- Tests must pass before AND after each extraction
- One extraction per commit (for reviewability)
