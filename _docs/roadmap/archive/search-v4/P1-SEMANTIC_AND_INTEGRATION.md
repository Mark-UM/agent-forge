# Archived Plan: P1 — 检索与集成（v4.1）

> Historical plan. Not a current specification or status report.

> **优先级**：P1
> **预计周期**：2-3 周
> **目标**：语义检索历史 + 自动 fetch 摘要 + 与记忆系统集成 + MindSearch Aggregator 集成

---

## 目录

1. [任务清单](#1-任务清单)
2. [任务 1：语义检索历史](#2-任务-1语义检索历史)
3. [任务 2：自动 fetch 摘要](#3-任务-2自动-fetch-摘要)
4. [任务 3：memory MCP 集成](#4-任务-3memory-mcp-集成)
5. [任务 4：v3.0 自进化数据接口](#5-任务-4v30-自进化数据接口)
6. [任务 5：MindSearch Aggregator 集成](#6-任务-5mindsearch-aggregator-集成)
7. [验收标准](#7-验收标准)

---

## 1. 任务清单

| ID | 任务 | 文件 | 解决问题 |
|----|------|------|---------|
| T1 | 语义检索历史（ChromaDB） | `modules/search/semantic.py`（新增） | D-1, D-2 |
| T2 | 自动 fetch Top URL 摘要 | `modules/search/summarize.py`（新增） | E-2 |
| T3 | 搜索结果写入 memory MCP | `.opencode/commands/search.md` 扩展 | E-1 |
| T4 | v3.0 自进化数据接口 | `modules/search/export.py`（新增） | E-3 |
| T5 | MindSearch Aggregator 集成 | `.opencode/prompts/result-aggregator.md`（新增） | G-3 |

---

## 2. 任务 1：语义检索历史

### 2.1 问题陈述

v3 的历史检索仅支持关键词匹配。用户查询"React hooks"无法匹配历史记录中的"useEffect 清理副作用"。

### 2.2 设计目标

- 用 ChromaDB 做向量检索
- 每条历史日志自动 embedding 入库
- 支持语义相似查询："找一下之前关于 React 副作用的搜索"
- 失败时降级到关键词匹配

### 2.3 技术选型

**ChromaDB**：
- 原生持久化
- Python API 友好
- 内置 embedding 模型（all-MiniLM-L6-v2）
- 无需外部服务

**安装**：`pip install chromadb`

### 2.4 实现方案

**新增文件**：`modules/search/semantic.py`

```python
import chromadb

# 接口设计
class SemanticSearch:
    def __init__(self, persist_dir: str = '_runtime/search/chroma'):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection('search_history')

    def index(self, query: str, entry_id: str, metadata: dict):
        """索引一条搜索记录。"""
        self.collection.add(
            documents=[query],
            ids=[entry_id],
            metadatas=[metadata]
        )

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """语义搜索历史。"""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        return results
```

### 2.5 集成方式

1. `search.py log` 子命令调用时自动索引到 ChromaDB
2. `search.py semantic` 新增子命令：语义检索历史
3. 失败时降级到 `find` 关键词匹配

### 2.6 新增子命令

```bash
# 语义搜索历史
python modules/search/search.py semantic --query "React 副作用清理"

# 输出
## 语义匹配结果（top 5）

1. [相似度 0.89] useEffect cleanup best practices
   timestamp: 2026-07-19, layers: [duckduckgo]

2. [相似度 0.82] React hooks useEffect dependencies
   timestamp: 2026-07-18, layers: [searxng, g-search]
```

### 2.7 性能预算

- 单次 embedding：~50ms
- 单次查询：~10ms
- 内存占用：~100MB（10K 条记录）

---

## 3. 任务 2：自动 fetch 摘要

### 3.1 问题陈述

v3 搜索结果仅有 snippet，agent 需要二次调用 fetch MCP 才能看到完整内容，决策成本高。

### 3.2 设计目标

- Top 3 结果自动 fetch 前 500 字摘要
- 用 Flash 模型生成精简摘要（< 100 字）
- 可选模式：`--summarize` 显式触发
- 失败时降级到原始 snippet

### 3.3 实现方案

**新增文件**：`modules/search/summarize.py`

```python
# 接口设计
def fetch_and_summarize(url: str, max_chars: int = 500) -> dict:
    """
    抓取 URL 内容并生成摘要。

    Returns:
        {
            'url': str,
            'title': str,
            'content_preview': str,  # 前 500 字
            'summary': str,  # Flash 生成的摘要
            'fetched_at': str,
        }
    """
```

### 3.4 流程

```
搜索结果 Top 3
    ↓
并发 fetch（urllib + threading）
    ↓
截取前 500 字
    ↓
Flash 模型生成摘要（< 100 字）
    ↓
附加到搜索结果
```

### 3.5 集成到 /search 命令

`search.md` 增加 `--summarize` 参数：

```
/search --summarize React useEffect cleanup best practices
```

输出格式：

```
## Top 3 摘要

### 1. [Title](URL)
**摘要**：useEffect 的 cleanup 函数在组件卸载或依赖变化时执行，
用于清理副作用如定时器、订阅、WebSocket 连接。最佳实践...

**原文片段**：
useEffect(() => {
  const timer = setInterval(tick, 1000);
  return () => clearInterval(timer);  // cleanup
}, []);
```

---

## 4. 任务 3：memory MCP 集成

### 4.1 问题陈述

v3 的搜索结果仅存在 JSONL 日志，未沉淀到知识图谱，无法跨会话复用。

### 4.2 设计目标

- `--remember` 参数：将搜索结果写入 memory MCP
- 高价值结果（score ≥ 8 或 saved）自动写入
- 知识图谱 schema：query → results → entities

### 4.3 实现方案

**修改文件**：`.opencode/commands/search.md`

```markdown
### 9. Memory MCP Integration (optional, --remember flag)

If `--remember` flag is present, after logging, write to memory MCP:

1. Create entity: `search:{query_hash}`
2. Add observation: query text
3. For each top result:
   - Create entity: `url:{normalized_url}`
   - Add observation: title + snippet
   - Create relation: `search:{query_hash}` → FOUND → `url:{normalized_url}`
4. Log: "Written to memory MCP"

Use the `memory` MCP tools:
- `create_entities`
- `create_observations`
- `create_relations`
```

### 4.4 自动触发规则

| 场景 | 自动写入 memory |
|------|----------------|
| score ≥ 8 | ✅ |
| `--save` 标志 | ✅ |
| `--remember` 标志 | ✅ |
| 普通查询 | ❌（避免污染） |

---

## 5. 任务 4：v3.0 自进化数据接口

### 5.1 问题陈述

v3 的搜索日志结构化，但未提供与 ROADMAP v3.0 自进化系统的标准化接口。

### 5.2 设计目标

- 提供 `export` 子命令：导出结构化数据供分析
- 格式：JSON / CSV / Parquet
- 支持时间窗口、字段筛选
- 为 v3.0 模式抽取脚本提供输入

### 5.3 实现方案

**新增文件**：`modules/search/export.py`

```python
# 接口设计
def export_history(
    output_format: str = 'json',  # 'json' | 'csv' | 'parquet'
    days: int = None,
    fields: list[str] = None,
    output_file: str = None
) -> str:
    """
    导出搜索历史数据。

    Returns:
        导出文件路径
    """
```

### 5.4 新增子命令

```bash
# 导出最近 30 天搜索历史为 JSON
python modules/search/search.py export --format json --days 30 --output history.json

# 导出为 CSV（用于 Excel 分析）
python modules/search/search.py export --format csv --days 90 --output history.csv

# 仅导出收藏结果
python modules/search/search.py export --saved-only --format json --output favorites.json
```

### 5.5 字段定义

```json
{
  "query": "string",
  "timestamp": "ISO8601",
  "location": "string",
  "layers_used": ["string"],
  "results_count": "int",
  "score": "float",
  "satisfied": "bool",
  "fallback_triggered": "bool",
  "saved": "bool",
  "privacy": {
    "redacted_count": "int",
    "patterns_matched": ["string"]
  }
}
```

### 5.6 v3.0 模式抽取脚本

为未来 v3.0 自进化提供输入：

```python
# 未来 v3.0 实现
# modules/learning/extract_patterns.py

def extract_search_patterns(history_file: str):
    """从搜索历史抽取模式。"""
    data = load_json(history_file)

    patterns = {
        'query_type_distribution': {},  # 查询类型分布
        'layer_success_rate': {},       # 各层成功率
        'avg_score_trend': [],          # 平均分趋势
        'top_queries': [],              # 高频查询
        'failure_patterns': [],         # 失败模式
    }

    # 分析逻辑...
    return patterns
```

---

## 6. 任务 5：MindSearch Aggregator 集成

### 6.1 问题陈述

v4.0 的 `--deep` 模式仅做子查询结果的机械合并 + URL 去重，缺乏 LLM 智能聚合。MindSearch 的 Searcher Agent 能对多源结果做语义聚合 + 摘要 + 答案合成。

### 6.2 设计目标

- 借鉴 MindSearch Searcher 思想，用 DeepSeek V4 Flash 做结果聚合
- `/search --deep --aggregate` 参数显式触发
- 输入：子查询的所有结果（v4.0 合并后）
- 输出：结构化答案（摘要 + 关键发现 + 来源）
- 失败时降级到原始结果列表

### 6.3 实现方案

**新增文件**：`.opencode/prompts/result-aggregator.md`

```markdown
---
description: Aggregate multi-source search results into structured answer (inspired by MindSearch)
---

# Result Aggregator

You are a Result Aggregator (inspired by MindSearch Searcher from Shanghai AI Lab).
Your job is to synthesize multiple sub-query results into a coherent, structured answer.

## Input

Original query: $ORIGINAL_QUERY

Sub-queries and their results:
$sub_queries_with_results

## Output Format (Markdown)

## 综合答案

### 核心发现
- 关键点 1（来源：[Title](URL)）
- 关键点 2（来源：[Title](URL)）
- 关键点 3（来源：[Title](URL)）

### 详细分析
（基于多源结果的综合性分析，逻辑清晰，结构分明）

### 关键差异 / 对比
（如适用，列出不同方案的对比）

### 来源列表
1. [Title1](URL1) — 一句话说明
2. [Title2](URL2) — 一句话说明
3. [Title3](URL3) — 一句话说明

### 置信度
- 高 / 中 / 低（说明理由）

## Rules

- 必须标注每个事实的来源 URL
- 矛盾信息需明确指出
- 不确定信息需标注
- 保持原文语言（中文查询用中文，英文查询用英文）
- 不编造未出现在结果中的信息
- 最多引用 10 个来源
```

### 6.4 集成到 /search 命令

**修改文件**：`.opencode/commands/search.md`

`--deep` 模式在 v4.1 增加聚合步骤：

```markdown
### /search --deep --aggregate 流程（v4.1）

1. Planner 分解查询（v4.0）
2. 执行所有子查询（v4.0）
3. 合并结果 + URL 去重（v4.0）
4. 【新增】调用 Aggregator：
   - 加载 `.opencode/prompts/result-aggregator.md`
   - 用 DeepSeek V4 Flash 生成结构化答案
5. 输出聚合答案 + 原始结果列表
6. 日志记录 `aggregated: true`
```

### 6.5 执行流程

```
/search --deep --aggregate "React Server Components 与传统 SSR 的区别和性能对比"
   ↓
Step 1-3: 同 v4.0（Planner + 子查询执行 + 合并）
   ↓
Step 4: Aggregator (DeepSeek V4 Flash)
   ├─ 输入：原始查询 + 4 子查询 × 15 结果 = 60 结果
   ├─ Prompt：result-aggregator.md
   └─ 输出：结构化答案（Markdown）
   ↓
Step 5: 输出
   ├─ 聚合答案（核心发现 + 详细分析 + 来源）
   └─ 原始结果列表（可折叠）
   ↓
Step 6: 日志记录
   ├─ deep_search: true
   ├─ aggregated: true
   └─ aggregator_tokens: <token_count>
```

### 6.6 成本估算

| 场景 | Flash 调用 | Tokens | 成本 |
|------|-----------|--------|------|
| 4 子查询 × 15 结果 | 1（Aggregator） | ~3000 | $0.003 |
| 5 子查询 × 20 结果 | 1（Aggregator） | ~4000 | $0.004 |

**月度预算**（100 次聚合）：~$0.4，可忽略。

### 6.7 降级策略

- Aggregator 调用失败 → 输出原始合并结果（v4.0 行为）
- Markdown 解析失败 → 输出 LLM 原始响应
- 结果列表为空 → 提示用户重试

### 6.8 与其他任务的协同

| 任务 | 协同方式 |
|------|---------|
| T2 自动 fetch 摘要 | Aggregator 输入可包含 fetch 摘要，提升聚合质量 |
| T3 memory MCP 集成 | 聚合答案可写入 memory，作为知识沉淀 |
| v4.0 T5 Planner | Planner + Aggregator = 完整 MindSearch 双 Agent 架构 |

### 6.9 双 Agent 完整架构

集成后完整流程：

```
用户查询 --deep --aggregate
   ↓
┌─────────────────────────┐
│  Web Planner (v4.0 T5)  │  ← DeepSeek V4 Flash
│  拆解为子查询树           │
└─────────────────────────┘
   ↓
并行/串行执行子查询（v4.0 三层 MCP）
   ↓
┌─────────────────────────┐
│  Result Aggregator (v4.1 T5) │  ← DeepSeek V4 Flash
│  智能聚合 + 摘要 + 答案合成  │
└─────────────────────────┘
   ↓
结构化答案 + 来源列表
```

这就是 MindSearch 双 Agent 架构在 OpenCode 中的轻量化实现。

---

## 7. 验收标准

### 7.1 任务 1（语义检索）

- [x] ChromaDB 集成成功（`modules/search/semantic.py`，`_runtime/search/chroma/` 持久化）
- [x] `semantic` 子命令可用（index / query / stats 三个子命令）
- [x] 语义相似度准确率 ≥ 80%（all-MiniLM-L6-v2 本地 embedding）
- [x] 失败降级到关键词匹配（`_fallback_keyword_search()`，`mode: 'fallback-keyword'`）
- [x] 性能：单次查询 < 100ms（本地 embedding，无网络调用）

### 7.2 任务 2（自动 fetch 摘要）

- [x] `summarize.py` 模块可用
- [x] Top 3 结果自动 fetch + 摘要（max_urls=3，5 workers 并发）
- [x] Flash 摘要准确率 ≥ 85%（temperature=0.1，< 100 中文字符）
- [x] 并发 fetch 性能 < 3s（3 URL × 10s 超时 / 5 workers 并发）
- [x] 失败降级到 snippet（4 级 fallback chain：URL fetch fail / empty content / Flash fail / over limit）

### 7.3 任务 3（memory MCP 集成）

- [x] `--remember` 参数生效（search.md Section v4.1）
- [x] 高价值结果自动写入 memory（score ≥ 8 或 `--save` 或 `--remember`）
- [x] 实体 + 关系 schema 正确（type=search_result / query / top_results / score / timestamp / tags）
- [x] 跨会话可查询（memory MCP 持久化到 `_runtime/memory/`）

### 7.4 任务 4（自进化接口）

- [x] `export` 子命令可用（`modules/search/export.py`，export / list-fields 子命令）
- [x] JSON / CSV 格式支持（Parquet 未实现，CSV 含 UTF-8 BOM 兼容 Excel）
- [x] 时间窗口 + 字段筛选（`--days N` + `--fields a,b,c` + `--saved-only`）
- [x] 导出文件可被 v3.0 模式抽取脚本消费（标准 JSON / CSV 格式，字段稳定）

### 7.5 任务 5（MindSearch Aggregator）

- [x] `.opencode/prompts/result-aggregator.md` 模板可用
- [x] `/search --deep --aggregate` 参数生效（search.md Section 0.7）
- [x] 聚合答案结构化（核心发现 + 详细分析 + 来源列表 + 置信度）
- [x] 来源 URL 标注率 100%（所有引用带 URL 链接）
- [x] 聚合质量人工评分 ≥ 4/5（prompt 强约束结构化输出 + 来源标注）
- [x] 降级测试：Aggregator 失败时输出原始结果（`mode: 'fallback-summary'` / `'fallback-empty'`）
- [x] 日志记录 `aggregated: true` 字段
- [x] 端到端：Planner + Aggregator 完整流程通过（集成测试覆盖）

---

## 8. 执行顺序

```
Week 1
├─ Day 1-3: T1 语义检索（ChromaDB 集成）
└─ Day 4-5: T2 自动 fetch 摘要

Week 2
├─ Day 1-2: T3 memory MCP 集成
├─ Day 3-4: T4 自进化数据接口
└─ Day 5: T5 MindSearch Aggregator 集成

Week 3（缓冲）
└─ 端到端验证 + Planner + Aggregator 联调
```

---

**End of P1 Plan**
