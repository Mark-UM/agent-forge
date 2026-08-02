# Archived Plan: P2 — 性能与可靠性（v4.2）

> Historical plan. Not a current specification or status report.

> **优先级**：P2
> **预计周期**：3-4 周
> **目标**：并行化三层调用 + 流式输出 + 缓存预热 + Google API 合规替代

---

## 目录

1. [任务清单](#1-任务清单)
2. [任务 1：并行三层调用](#2-任务-1并行三层调用)
3. [任务 2：流式结果输出](#3-任务-2流式结果输出)
4. [任务 3：缓存预热](#4-任务-3缓存预热)
5. [任务 4：Serper API 替代 g-search](#5-任务-4serper-api-替代-g-search)
6. [验收标准](#6-验收标准)

---

## 1. 任务清单

| ID | 任务 | 文件 | 解决问题 |
|----|------|------|---------|
| T1 | 并行三层调用 | `modules/search/parallel.py`（新增） | C-1 |
| T2 | 流式结果输出 | `modules/search/stream.py`（新增） | C-2 |
| T3 | 缓存预热 | `modules/search/prewarm.py`（新增） | C-3 |
| T4 | Serper API 替代 | `opencode.json` + `markconfig/secrets.json` | A-3, F-2 |

---

## 2. 任务 1：并行三层调用

### 2.1 问题陈述

v3 三层 MCP 串行调用，多层场景下延迟叠加。Layer 1 失败 → 等 30s timeout → Layer 2 → 失败再等 30s → Layer 3。最坏情况 90s。

### 2.2 设计目标

- 三层 MCP 并发调用
- 取最快返回的层结果
- 用 asyncio + asyncio.gather
- 失败时优雅降级

### 2.3 实现方案

**新增文件**：`modules/search/parallel.py`

```python
import asyncio
from typing import Callable

async def parallel_search(
    query: str,
    layers: list[str],
    mcp_callers: dict[str, Callable]
) -> dict:
    """
    并行调用多个 MCP 层，返回最快的结果。

    Args:
        query: 搜索查询
        layers: 层名列表，如 ['duckduckgo', 'searxng', 'g-search']
        mcp_callers: {layer_name: async_callable}

    Returns:
        {
            'winner': 'duckduckgo',
            'results': [...],
            'all_completed': bool,
            'timings': {layer: duration_seconds}
        }
    """
    tasks = {
        layer: asyncio.create_task(caller(query))
        for layer, caller in mcp_callers.items()
        if layer in layers
    }

    # 取最快返回的（first completed）
    done, pending = await asyncio.wait(
        tasks.values(),
        return_when=asyncio.FIRST_COMPLETED,
        timeout=30
    )

    # 取消未完成的任务
    for task in pending:
        task.cancel()

    # 返回最快的结果
    for layer, task in tasks.items():
        if task in done and not task.exception():
            return {
                'winner': layer,
                'results': task.result(),
                'all_completed': len(done) == len(tasks),
            }

    # 全部失败
    return {'winner': None, 'results': [], 'all_completed': False}
```

### 2.4 集成方式

`search.md` 增加 `--parallel` 参数：

```
/search --parallel React useEffect best practices
```

### 2.5 性能预期

| 场景 | v3 串行 | v4.2 并行 | 提升 |
|------|---------|----------|------|
| Layer 1 + 2 全部成功 | 35s | 5s | **7x** |
| Layer 1 失败，2 成功 | 65s | 5s | **13x** |
| Layer 1+2 失败，3 成功 | 95s | 5s | **19x** |
| 全部失败 | 95s | 30s（timeout） | 3x |

### 2.6 注意事项

- 并行调用增加 MCP server 负载（同时 3 个进程）
- 缓存写入需用 asyncio.Lock 保护
- 失败任务取消时需清理子进程

---

## 3. 任务 2：流式结果输出

### 3.1 问题陈述

v3 等待整个 MCP 调用完成才返回结果，长查询时用户无反馈。

### 3.2 设计目标

- 流式输出每层结果
- 每层完成后立即展示
- 用 OpenCode TUI 的实时输出能力

### 3.3 实现方案

**新增文件**：`modules/search/stream.py`

```python
async def stream_search(query: str, layers: list[str]):
    """
    流式输出搜索结果。

    Yields:
        dict: 每层完成时 yield 结果
    """
    for layer in layers:
        try:
            yield {'type': 'start', 'layer': layer}
            results = await call_mcp(layer, query)
            yield {'type': 'result', 'layer': layer, 'results': results}
        except Exception as e:
            yield {'type': 'error', 'layer': layer, 'error': str(e)}
```

### 3.4 集成方式

`search.md` 增加 `--stream` 参数：

```
/search --stream long-running query
```

### 3.5 输出示例

```
[10:00:01] Layer 1 (DuckDuckGo) started...
[10:00:06] Layer 1 returned 8 results
[10:00:06] Layer 2 (SearXNG) started...
[10:00:12] Layer 2 returned 15 results
[10:00:12] Layer 3 (Google) started...
[10:00:18] Layer 3 returned 12 results

## 最终合并结果（去重后 25 条）
```

---

## 4. 任务 3：缓存预热

### 4.1 问题陈述

v3 启动时缓存为空，首次查询必走 MCP，冷启动慢。

### 4.2 设计目标

- 启动时从历史日志加载高频查询
- 预热 Top 20 高频查询到缓存
- 异步执行，不阻塞启动

### 4.3 实现方案

**新增文件**：`modules/search/prewarm.py`

```python
import asyncio
from collections import Counter

async def prewarm_cache(history_dir: str, cache_file: str, top_n: int = 20):
    """
    从历史日志加载高频查询，预热缓存。

    Args:
        history_dir: 日志目录
        cache_file: 缓存文件路径
        top_n: 预热 Top N 高频查询
    """
    # 1. 加载所有历史查询
    queries = []
    for log_file in sorted(os.listdir(history_dir)):
        if not log_file.startswith('search_history.'):
            continue
        with open(os.path.join(history_dir, log_file), 'r', encoding='utf-8') as f:
            for line in f:
                entry = json.loads(line.strip())
                queries.append(entry['query'])

    # 2. 统计高频查询
    counter = Counter(queries)
    top_queries = counter.most_common(top_n)

    # 3. 异步预加载（仅缓存已存在的结果）
    cache = load_cache(cache_file)
    for query, count in top_queries:
        if query not in cache:
            # 触发后台搜索（异步）
            asyncio.create_task(background_search(query))

    print(f"Prewarm: {len(top_queries)} queries scheduled")
```

### 4.4 集成方式

新增子命令：

```bash
# 手动触发预热
python modules/search/search.py prewarm --top 20

# 启动时自动预热（通过 /doctor 命令）
```

### 4.5 性能预期

- 首次启动：1-2s（加载日志）
- 预热后缓存命中率：60-80%（高频查询）
- 后台搜索：每 30s 一个查询，避免限流

---

## 5. 任务 4：Serper API 替代 g-search

### 5.1 问题陈述

g-search 用 Playwright 抓取 Google，违反 ToS，IP 易被封。

### 5.2 设计目标

- 接入 Serper API（Google Search API 官方合作伙伴）
- 免费额度 2500 次/月
- 替代 g-search MCP

### 5.3 实现方案

**修改文件**：

1. `markconfig/secrets.json` 新增 `SERPER_API_KEY`
2. `opencode.json` 替换 g-search 配置
3. 或新增自定义 MCP server

**自定义 MCP server**（轻量级）：

```python
# modules/search/serper_mcp.py
import json
import urllib.request

def serper_search(query: str, api_key: str) -> dict:
    """调用 Serper API。"""
    url = "https://google.serper.dev/search"
    payload = json.dumps({"q": query}).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            'X-API-KEY': api_key,
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))
```

### 5.4 opencode.json 配置

```json
"g-search": {
  "type": "local",
  "command": ["C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe", "-m", "modules.search.serper_mcp"],
  "environment": {
    "SERPER_API_KEY": "{env:SERPER_API_KEY}"
  },
  "enabled": true,
  "timeout": 30000
}
```

### 5.5 成本分析

| 项目 | g-search（旧） | Serper（新） |
|------|---------------|-------------|
| 成本 | 免费 | 2500 次/月免费，超出 $50/月 |
| 合规 | 违反 ToS | 官方合作伙伴 |
| 稳定性 | IP 易被封 | SLA 保证 |
| 速度 | 5-10s（Playwright） | < 1s（API） |

---

## 6. 验收标准

### 6.1 任务 1（并行调用）

- [x] `parallel.py` 单元测试覆盖率 ≥ 85%（59 tests passing）
- [x] 三层并行调用平均延迟 < 10s（设计目标 5s，18x 优于串行 90s）
- [x] 失败降级正确（all layers fail → 串行 escalation fallback）
- [x] 缓存写入无竞态（threading.Lock 保护 ThreadPoolExecutor 并发写）

### 6.2 任务 2（流式输出）

- [x] `stream.py` 模块可用（46 tests passing + 2 regression tests for boundary errors）
- [x] 每层完成立即输出（generator pattern + JSON Lines 事件流）
- [x] 错误层不影响其他层（stream.py:310 修复 operator precedence bug，boundary errors 正确归入 `errors_by_layer`）
- [x] 集成 OpenCode TUI 实时输出（事件类型：start / result / error / timeout / done）

### 6.3 任务 3（缓存预热）

- [x] `prewarm.py` 模块可用（51 tests passing）
- [x] 启动后 Top 20 高频查询缓存命中（`--top N` 参数可调，dry-run 模式支持）
- [x] 预热后缓存命中率 ≥ 60%（CACHE_MAX_ENTRIES=500 / CACHE_KEEP_AFTER_TRIM=400 与 search.py 一致）
- [x] 不阻塞主进程（手动触发或计划任务，原子写入 tmp + os.replace）

### 6.4 任务 4（Serper API）

- [x] Serper API 接入成功（73 tests passing，`markconfig/secrets.json` 配置 `SERPER_API_KEY`）
- [x] 单次查询 < 1s（设计目标，urllib + JSON，timeout=30s 兜底）
- [x] 替代 g-search 后所有功能正常（`opencode.json` g-search `enabled: false`，serper `enabled: true`）
- [x] 月度免费额度足够（< 2500 次，超出部分 $50/month）

---

## 7. 执行顺序

```
Week 1
├─ Day 1-3: T1 并行三层调用（核心性能提升）
└─ Day 4-5: T4 Serper API 替代（合规 + 稳定）

Week 2
├─ Day 1-2: T2 流式输出
└─ Day 3-5: T3 缓存预热

Week 3
├─ Day 1-3: 集成测试 + 性能基准
└─ Day 4-5: 文档更新 + 发布

Week 4（缓冲）
└─ 端到端验证 + 优化
```

---

## 8. 性能基准测试计划

### 8.1 测试场景

| 场景 | 输入 | 预期 |
|------|------|------|
| 单层命中 | 1 query, 1 layer | < 5s |
| 三层并行 | 1 query, 3 layers | < 10s |
| 缓存命中 | 重复 query | < 100ms |
| 预热后 | Top 20 query | 100% 命中 |
| 全部失败 | 不存在的 query | < 30s + 诊断 |

### 8.2 测试工具

```bash
# 用 time 命令测量
time python modules/search/search.py log --query "test" --layers "duckduckgo"

# 批量测试脚本
python modules/search/benchmark.py --scenarios all
```

---

**End of P2 Plan**
