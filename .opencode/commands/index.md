---
description: File indexer — index and semantically search memory/report files
agent: build
---

Manage the ChromaDB file index for semantic search over memory and report files.

## Usage

- `/index build [root_dir]` — Index files in a directory (default: `_data/memory/`)
- `/index search <query>` — Semantic search indexed files
- `/index stats` — Show index statistics
- `/index clear` — Clear the index

## Examples

```
/index build
/index build _runtime/reports
/index search "Python 技术栈"
/index stats
```

## Implementation

```python
from modules.orchestrator.file_indexer import index_directory, search_files, get_index_stats

# Index files
result = index_directory(root="_data/memory")

# Search
results = search_files("Python 技术栈", top_k=5)

# Stats
stats = get_index_stats()
```

## Notes

- Uses ChromaDB collection `memory_files` (separate from `search_history`)
- Incremental indexing: only changed files are re-indexed (by content hash)
- Supports `*.md` and `*.txt` files by default
- ChromaDB must be available (installed in vendor/python-libs/ or system)
