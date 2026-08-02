---
description: Async pattern — asyncio tasks, gather, cancellation
language: python
task: coding
version: 1.5.0
---

# Example: Async Patterns with asyncio

## Scenario
Fetch multiple URLs concurrently with timeout and cancellation handling.

## Pattern 1: Concurrent fetch with gather

```python
import asyncio
import urllib.request

async def fetch(url: str, timeout: float = 10.0) -> bytes:
    """Fetch a URL with timeout."""
    try:
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, _fetch_sync, url),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return b""  # graceful degradation


def _fetch_sync(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()


async def fetch_all(urls: list[str]) -> list[bytes]:
    """Fetch all URLs concurrently."""
    return await asyncio.gather(*[fetch(url) for url in urls])


# Usage
results = asyncio.run(fetch_all(["https://a.com", "https://b.com"]))
```

## Pattern 2: TaskGroup (Python 3.11+) with error isolation

```python
import asyncio

async def fetch_with_isolation(urls: list[str]) -> dict[str, bytes | Exception]:
    """Fetch all URLs; isolate failures (one failure doesn't cancel others)."""
    results: dict[str, bytes | Exception] = {}

    async def task(url: str):
        try:
            results[url] = await fetch(url)
        except Exception as e:
            results[url] = e  # store exception instead of raising

    async with asyncio.TaskGroup() as tg:
        for url in urls:
            tg.create_task(task(url))

    return results
```

## Pattern 3: Cancellation handling

```python
async def long_running_task():
    try:
        while True:
            await do_work()
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        # Cleanup before re-raising
        await cleanup()
        raise  # ALWAYS re-raise CancelledError
```

## Anti-patterns

```python
# DON'T: blocking call inside async function
async def fetch(url):
    return urllib.request.urlopen(url).read()  # blocks the event loop!

# DON'T: gather without return_exceptions (one failure cancels all)
results = await asyncio.gather(*[fetch(u) for u in urls])  # one raises → all cancelled

# DON'T: swallow CancelledError
async def task():
    try:
        await work()
    except asyncio.CancelledError:
        pass  # BUG: cancellation is suppressed, task keeps running

# DON'T: create_task without keeping reference (may be GC'd)
asyncio.create_task(work())  # task may disappear before completing
```

## Key Takeaways
- Use `run_in_executor` for blocking I/O in async code
- `TaskGroup` (3.11+) is preferred over `gather` for structured concurrency
- ALWAYS re-raise `CancelledError` after cleanup
- Keep references to tasks created with `create_task`
- Use `return_exceptions=True` with `gather` if you want fault tolerance
