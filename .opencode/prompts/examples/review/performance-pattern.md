---
description: Performance review patterns (algorithmic complexity, caching, I/O)
language: neutral
task: review
version: 1.5.0
---

# Example: Performance Review Patterns

## Categories to check

### 1. Algorithmic Complexity
- [ ] No O(n²) loops where O(n) suffices (nested loops over same data)
- [ ] Hash-based lookups (`set` / `dict`) instead of linear search in lists
- [ ] Sorting done once, not inside a loop
- [ ] Database queries avoid N+1 pattern (use JOIN or batch fetch)

### 2. Memory
- [ ] Large collections not loaded into memory all at once (use generators / streams)
- [ ] No accidental retention (caches without eviction, growing dicts)
- [ ] Big files streamed, not `read()` in full
- [ ] Immutable copies of large structures avoided where mutation is safe

### 3. I/O
- [ ] Concurrent I/O for independent operations (asyncio, threads, parallel)
- [ ] Database connections pooled (not opened per-request)
- [ ] HTTP requests have timeouts
- [ ] File I/O batched (write once, not per-line)

### 4. Caching
- [ ] Cache only what's expensive AND frequently read
- [ ] Cache invalidation defined (TTL, explicit, event-based)
- [ ] Cache size bounded (LRU, not infinite growth)
- [ ] Cache hits/misses logged for observability

### 5. Database
- [ ] Indexes on columns used in WHERE / JOIN / ORDER BY
- [ ] SELECT specific columns, not `SELECT *`
- [ ] Pagination for large result sets (LIMIT/OFFSET or cursor)
- [ ] EXPLAIN analyzed for slow queries

## Common Findings

### Critical (O(n²) or worse in hot path)
```python
# DON'T: O(n*m) lookup
for user in users:
    if user.id in banned_ids_list:  # list scan = O(m)
        ...

# DO: O(n+m) with set
banned_ids = set(banned_ids_list)
for user in users:
    if user.id in banned_ids:  # O(1)
        ...
```

### High (N+1 queries)
```python
# DON'T: query per iteration
for user in users:
    orders = db.query(f"SELECT * FROM orders WHERE user_id = {user.id}")

# DO: batch fetch with IN clause or JOIN
user_ids = [u.id for u in users]
orders = db.query("SELECT * FROM orders WHERE user_id IN %s", (user_ids,))
```

### Medium (blocking I/O in async)
```python
# DON'T: blocking call in async function
async def fetch_all():
    return [requests.get(u) for u in urls]  # blocks event loop

# DO: run in executor or use async HTTP client
async def fetch_all():
    return await asyncio.gather(*[async_fetch(u) for u in urls])
```

### Low (micro-optimizations)
- String concatenation in a loop (use `''.join(parts)`)
- Repeated regex compilation (compile once, reuse)
- List comprehension vs map/filter (comprehension usually faster)
- `len(list(gen))` instead of `sum(1 for _ in gen)` (memory)

## Anti-patterns

```python
# Premature optimization without measurement
if optimize:  # guessing, not measuring
    cache_everything()

# Caching without invalidation
_CACHE = {}  # grows forever, memory leak

# Micro-optimizing cold paths
def init_app():
    # optimizing startup that runs once is wasteful
    x = sum(i for i in range(100))  # fine, don't optimize

# Ignoring algorithmic complexity
def find_dup(lst):
    for i, a in enumerate(lst):
        for b in lst[i+1:]:  # O(n²)
            if a == b: return a
    # DO: return next((x for x in lst if lst.count(x) > 1), None) — still O(n²)
    # DO: seen = set(); for x in lst: if x in seen: return x; seen.add(x) — O(n)
```

## Review Process
1. Identify hot paths (frequently executed code)
2. Check algorithmic complexity of loops and queries
3. Look for N+1 query patterns
4. Verify I/O is concurrent where independent
5. Check cache invalidation strategy
6. Profile if uncertain (don't guess)
