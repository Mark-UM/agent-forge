---
description: Python project conventions and best practices
language: python
priority: 80
version: 1.5.0
---

# Python Context

## Style
- Follow PEP 8 strictly
- Use type hints on all function signatures
- snake_case for variables/functions, PascalCase for classes, UPPER_CASE for constants
- 4-space indentation, no tabs

## Project Conventions
- Python 3.11 for the project runtime ABI; use `sys.executable` for child Python processes
- Prefer standard library; introduce external deps only when justified
- HTTP: use `urllib.request` (zero-dep), NOT `requests` (unless already a dep)
- Testing: `pytest` with `unittest.mock`
- File I/O: use `pathlib.Path`, not `os.path` strings

## Common Patterns
- Error handling: try/except with specific exceptions, never bare `except:`
- Logging: print to stderr for warnings, stdout for results
- Config: load from `markconfig/` directory, never hardcode paths
- Secrets: read from environment variables, never commit to git

## Anti-patterns
- `import *` → explicit imports only
- `eval()` / `exec()` → never (use `ast.literal_eval` for safe parsing)
- Bare `except:` → catch specific exceptions
- `os.system()` → use `subprocess.run()` with `shell=False`

## Testing
- Test files: `tests/test_*.py`
- Test functions: `test_*`
- One assert per test when possible
- Use `pytest.fixture` for shared setup
- Mock external calls (HTTP, file system) with `unittest.mock.patch`
