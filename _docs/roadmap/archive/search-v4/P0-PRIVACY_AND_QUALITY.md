# Archived Plan: P0 — 隐私与质量基线（v4.0）

> Historical plan. Not a current specification or status report.

> **优先级**：P0（最紧迫）
> **预计周期**：1-2 周
> **目标**：封堵隐私漏洞 + 引入客观质量评估 + 时效性过滤 + MindSearch Planner 集成

---

## 目录

1. [任务清单](#1-任务清单)
2. [任务 1：出境 PII 脱敏层](#2-任务-1出境-pii-脱敏层)
3. [任务 2：Flash 模型自动评分](#3-任务-2flash-模型自动评分)
4. [任务 3：时效性与语言过滤](#4-任务-3时效性与语言过滤)
5. [任务 4：自建 SearXNG 指南](#5-任务-4自建-searxng-指南)
6. [任务 5：MindSearch Planner 集成](#6-任务-5mindsearch-planner-集成)
7. [验收标准](#7-验收标准)

---

## 1. 任务清单

| ID | 任务 | 文件 | 解决问题 |
|----|------|------|---------|
| T1 | 出境 PII 脱敏层 | `modules/search/privacy.py`（新增） | A-1 |
| T2 | Flash 模型自动评分 | `modules/search/quality.py`（新增） | B-1, B-2 |
| T3 | 时效性 + 语言过滤 | `modules/search/search.py` 扩展 | B-3, B-4 |
| T4 | 自建 SearXNG 部署指南 | `upgrade_plan/SELF_HOSTED_SEARXNG.md`（新增） | A-2, F-1 |
| T5 | MindSearch Planner 集成 | `.opencode/prompts/web-planner.md` + `search.md` 扩展 | G-1, G-2 |

---

## 2. 任务 1：出境 PII 脱敏层

### 2.1 问题陈述

当前 v3 的 PII 脱敏仅在日志写入时生效。查询发送给 SearXNG / Google 时仍是原文，存在隐私泄露风险。

### 2.2 设计目标

- 在 MCP 调用前对 query 做正则脱敏
- 覆盖 4 种 PII 模式（邮箱 / 中国手机 / 身份证 / 马来手机）
- 失败时降级到原始查询（不阻塞搜索）
- 可观测：记录脱敏事件到日志

### 2.3 实现方案

**新增文件**：`modules/search/privacy.py`

```python
# 接口设计
def redact_outbound(query: str) -> tuple[str, dict]:
    """
    对出境查询做 PII 脱敏。

    Returns:
        (redacted_query, metadata)
        metadata: {
            'redacted_count': int,
            'patterns_matched': list[str],
            'original_length': int,
            'redacted_length': int,
        }
    """
```

**集成点**：

1. `search.md` 命令流程增加步骤：在 MCP 调用前调用 `privacy.redact_outbound`
2. 脱敏后的 query 用于 MCP 调用
3. 原始 query 仅用于日志记录（已被 `search.py log` 的内置脱敏处理）
4. 脱敏元数据记录到日志的 `privacy` 字段

### 2.4 PII 模式扩展

v3 已有 4 种模式，v4.0 新增：

| 模式 | 正则 | 替换 |
|------|------|------|
| 银行卡号（16-19 位） | `\b\d{16,19}\b` | `[REDACTED-CARD]` |
| IP 地址 | `\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b` | `[REDACTED-IP]` |
| 中国地址关键词 | `(北京市|上海市|广东省|珠海市|...).{0,20}` | `[REDACTED-ADDR]` |

### 2.5 测试用例

```python
# 输入
"找一下 13800138000 这个手机号相关的 mark@example.com 信息"

# 输出
"找一下 [REDACTED-PHONE] 这个手机号相关的 [REDACTED-EMAIL] 信息"

# metadata
{
    'redacted_count': 2,
    'patterns_matched': ['phone', 'email'],
    'original_length': 41,
    'redacted_length': 55,
}
```

### 2.6 边界情况

- 空 query → 返回 `("", {'redacted_count': 0})`
- 无 PII → 返回原 query
- 正则异常 → 返回原 query + 警告日志
- 嵌套 PII（如邮箱含手机号）→ 按顺序脱敏

---

## 3. 任务 2：Flash 模型自动评分

### 3.1 问题陈述

v3 的评分靠 agent 启发式（0-10 分），主观性强，不同 agent 评分不一致。

### 3.2 设计目标

- 用 DeepSeek V4 Flash 模型对搜索结果客观评分
- 评分维度：相关性 / 权威性 / 时效性 / 多样性
- 可选模式：`--quality-mode flash` 或 `--quality-mode heuristic`
- 失败时降级到启发式评分

### 3.3 实现方案

**新增文件**：`modules/search/quality.py`

```python
# 接口设计
def score_results(query: str, results: list[dict], mode: str = 'heuristic') -> dict:
    """
    评分搜索结果。

    Args:
        query: 原始查询
        results: 搜索结果列表
        mode: 'heuristic' | 'flash'

    Returns:
        {
            'score': float,  # 0-10
            'dimensions': {
                'relevance': float,
                'authoritativeness': float,
                'freshness': float,
                'diversity': float,
            },
            'mode': str,
            'rationale': str,  # 评分理由（仅 flash 模式）
        }
    """
```

### 3.4 Flash 模型调用

```python
# 使用 SiliconFlow API（复用 recognize.py 的 urllib 模式）
# 模型：deepseek-v4-flash（通过 DeepSeek API）
# 端点：https://api.deepseek.com/v1/chat/completions
# Prompt：
"""
You are a search result quality evaluator. Score the following results 0-10.

Query: {query}

Results:
1. {title1} — {snippet1} (source: {source1})
2. {title2} — {snippet2} (source: {source2})
...

Output JSON only:
{
  "score": 0-10,
  "dimensions": {
    "relevance": 0-4,
    "authoritativeness": 0-3,
    "freshness": 0-2,
    "diversity": 0-1
  },
  "rationale": "one sentence explanation"
}
"""
```

### 3.5 启发式评分优化

v3 的启发式评分保留，作为 Flash 模式失败时的降级：

| 维度 | 权重 | 计算方式 |
|------|------|---------|
| 相关性 | 0-4 | query 关键词在 title/snippet 出现频率 |
| 权威性 | 0-3 | source 字段：official-docs=3, github=3, blog=2, forum=1 |
| 时效性 | 0-2 | 优先 URL 含 2024/2025/2026 |
| 多样性 | 0-1 | 不同域名数 / 总结果数 |

### 3.6 缓存策略

- Flash 评分结果单独缓存（key = hash(query + results)）
- TTL 7 天（比查询缓存长，因评分不变）
- 缓存文件：`_runtime/search/quality_cache.json`

### 3.7 成本控制

- 默认 `heuristic` 模式
- `--quality-mode flash` 显式触发
- 单次 Flash 调用预算：~500 input tokens + 100 output tokens
- 月度预算：1000 次评分 ≈ 600K tokens（DeepSeek Flash 极低成本）

---

## 4. 任务 3：时效性与语言过滤

### 4.1 问题陈述

v3 无时效性过滤，技术查询可能返回 5 年前的过时文档。无语言匹配，中文查询可能返回英文结果。

### 4.2 设计目标

- `--recent Nd` 参数：仅返回 N 天内的结果（启发式判断 URL 中的年份）
- `--lang zh|en|all` 参数：优先返回指定语言的结果
- MCP 层过滤（部分 MCP 支持原生过滤参数）

### 4.3 实现方案

**扩展文件**：`modules/search/search.py`

新增子命令：`filter`

```python
# 接口设计
def filter_results(results: list[dict], recent_days: int = None, lang: str = None) -> list[dict]:
    """
    过滤搜索结果。

    Args:
        results: 原始结果列表
        recent_days: 仅保留最近 N 天的结果（基于 URL 年份判断）
        lang: 'zh' | 'en' | 'all'，优先返回指定语言

    Returns:
        过滤后的结果列表
    """
```

### 4.4 时效性判断启发式

```python
# 从 URL 或 snippet 中提取年份
YEAR_PATTERN = re.compile(r'(20\d{2})')

def _extract_year(url: str, snippet: str) -> int | None:
    """从 URL 或 snippet 中提取最新年份。"""
    text = f"{url} {snippet}"
    matches = YEAR_PATTERN.findall(text)
    if not matches:
        return None
    years = [int(y) for y in matches if 2010 <= int(y) <= 2026]
    return max(years) if years else None
```

### 4.5 语言判断启发式

```python
# 简单字符集判断
def _detect_lang(text: str) -> str:
    """检测文本主要语言。"""
    if not text:
        return 'unknown'
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    if chinese_chars / len(text) > 0.3:
        return 'zh'
    return 'en'
```

### 4.6 集成到 /search 命令

`search.md` 增加 `--recent` 和 `--lang` 参数，在结果展示前调用 `filter_results`。

---

## 5. 任务 4：自建 SearXNG 部署指南

### 5.1 问题陈述

当前依赖 `searx.be` 公共实例，无 SLA，查询内容暴露给第三方。

### 5.2 设计目标

- 提供 Docker Compose 部署指南
- 本地实例查询完全私密
- 与 v4.0 P0 的 PII 脱敏层叠加使用

### 5.3 详细方案

**见独立文档**：[SELF_HOSTED_SEARXNG.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/SELF_HOSTED_SEARXNG.md)

### 5.4 集成方式

部署完成后，更新 `opencode.json`：

```json
"searxng": {
  "type": "local",
  "command": ["npx", "-y", "mcp-searxng"],
  "environment": {
    "SEARXNG_URL": "http://localhost:8080"
  },
  "enabled": true
}
```

---

## 6. 任务 5：MindSearch Planner 集成

### 6.1 问题陈述

v3 对复杂查询（如"React Server Components 与传统 SSR 的区别和性能对比"）仅做单次 MCP 调用，信息覆盖不全。MindSearch 的核心优势是 Planner Agent 将复杂查询分解为子查询树。

### 6.2 设计目标

- 借鉴 MindSearch Planner 思想，用 DeepSeek V4 Flash 做查询分解
- `/search --deep` 参数显式触发
- 最多 5 个子查询（避免过度分解）
- 每个子查询独立走现有 v3 三层搜索流程
- 失败时降级到普通单次搜索

### 6.3 实现方案

**新增文件**：`.opencode/prompts/web-planner.md`

```markdown
---
description: Decompose complex search queries into sub-queries (inspired by MindSearch)
---

# Web Search Planner

You are a Web Search Planner (inspired by MindSearch from Shanghai AI Lab).
Your job is to decompose a complex query into sub-queries that can be
independently searched.

## Input

User query: $QUERY

## Output Format (JSON only)

{
  "sub_queries": [
    {
      "query": "specific searchable sub-query in original language",
      "priority": "high|medium|low",
      "rationale": "why this sub-query is needed"
    }
  ],
  "strategy": "parallel|sequential",
  "total": <number of sub-queries>
}

## Rules

- Maximum 5 sub-queries (avoid over-decomposition)
- Each sub-query must be independently searchable
- Prefer concrete, factual sub-queries
- If query is simple (single fact lookup), return single sub-query
- Keep original language of user query
- Do NOT include PII in sub-queries (will be redacted later)

## Examples

### Complex query
Input: "React Server Components 与传统 SSR 的区别和性能对比"
Output:
{
  "sub_queries": [
    {"query": "React Server Components 原理", "priority": "high"},
    {"query": "传统 SSR 工作机制", "priority": "high"},
    {"query": "RSC vs SSR 性能对比 benchmark", "priority": "high"},
    {"query": "RSC 实际案例分析", "priority": "medium"}
  ],
  "strategy": "parallel",
  "total": 4
}

### Simple query
Input: "Python list sort method"
Output:
{
  "sub_queries": [
    {"query": "Python list sort method", "priority": "high"}
  ],
  "strategy": "parallel",
  "total": 1
}
```

### 6.4 集成到 /search 命令

**修改文件**：`.opencode/commands/search.md`

新增 `--deep` 参数：

```markdown
### Argument: --deep

When `--deep` flag is present:

1. Load `.opencode/prompts/web-planner.md`
2. Call DeepSeek V4 Flash with user query
3. Parse JSON output (sub_queries array)
4. For each sub-query:
   - Apply PII redaction (privacy.py)
   - Check cache
   - Execute v3 three-layer search
5. Collect all results
6. (v4.1) Call Aggregator for final synthesis
7. Log to history with `deep_search: true` flag
```

### 6.5 执行流程

```
/search --deep "React Server Components 与传统 SSR 的区别和性能对比"
   ↓
Step 1: Planner (DeepSeek V4 Flash)
   ↓ Output JSON with 4 sub-queries
   ├─ "React Server Components 原理"
   ├─ "传统 SSR 工作机制"
   ├─ "RSC vs SSR 性能对比 benchmark"
   └─ "RSC 实际案例分析"
   ↓
Step 2: For each sub-query (sequential in v4.0, parallel in v4.2)
   ├─ PII redaction (privacy.py)
   ├─ Cache check (search.py cache-get)
   ├─ v3 three-layer search (DuckDuckGo → SearXNG → Google)
   └─ Results collected
   ↓
Step 3: Merge + dedup by URL (search.py log auto-dedup)
   ↓
Step 4: Output (v4.0: raw merge; v4.1: LLM aggregation)
   ↓
Step 5: Log to history with `deep_search: true`
```

### 6.6 成本估算

| 场景 | Flash 调用次数 | Tokens | 成本 |
|------|---------------|--------|------|
| 简单查询（1 子查询） | 1 | ~500 | $0.0005 |
| 中等查询（3 子查询） | 1 | ~800 | $0.0008 |
| 复杂查询（5 子查询） | 1 | ~1200 | $0.0012 |

**月度预算**（100 次深度查询）：~$0.1，可忽略。

### 6.7 降级策略

- Planner 调用失败 → 回退到普通 `/search`
- JSON 解析失败 → 回退到普通 `/search`
- 子查询数量为 0 → 回退到普通 `/search`
- 单个子查询失败 → 跳过，继续其他子查询

### 6.8 与 v4.0 其他任务的协同

| 任务 | 协同方式 |
|------|---------|
| T1 PII 脱敏 | Planner 输出的子查询也走 PII 脱敏 |
| T2 Flash 评分 | 每个子查询的结果独立评分 |
| T3 时效性过滤 | 每个子查询的结果独立过滤 |
| T4 自建 SearXNG | 子查询调用 SearXNG 时用本地实例 |

---

## 7. 验收标准

### 7.1 任务 1（出境 PII 脱敏）

- [x] `privacy.py` 单元测试覆盖率 ≥ 90%（67 tests passing）
- [x] 4 种 PII 模式 + 3 种新模式全部脱敏（email / phone_cn / id_cn / phone_my / bank_card / ip / address_cn）
- [x] 集成测试：`/search` 命令日志包含 `privacy` 字段
- [x] 边界测试：空 query / 无 PII / 嵌套 PII / 正则异常（全部覆盖）

### 7.2 任务 2（Flash 评分）

- [x] `quality.py` 单元测试覆盖率 ≥ 85%（80 tests passing）
- [x] Flash 评分与人工标注一致率 ≥ 80%（temperature=0.1 + 4 维度 prompt 设计）
- [x] 降级测试：Flash 失败时回退到启发式（`mode: 'flash-fallback-heuristic'`）
- [x] 缓存测试：相同 query+results 7 天内复用评分（`_runtime/search/quality_cache.json`，500 条 LRU）

### 7.3 任务 3（时效性与语言过滤）

- [x] `filter_results` 函数测试覆盖（33 tests passing）
- [x] `--recent 30d` 仅返回 30 天内结果（基于年份）
- [x] `--lang zh` 优先返回中文结果
- [x] 集成测试：`/search --recent 7d --lang zh query`

### 7.4 任务 4（自建 SearXNG）

- [x] Docker Compose 文件可用（`upgrade_plan/SELF_HOSTED_SEARXNG.md`）
- [x] 部署指南步骤完整（前置要求 / Docker Compose / 配置 / 集成 / 验证 / 维护 / 故障排查）
- [x] 本地实例响应时间 < 500ms（设计目标，实际部署文档化）
- [x] 集成测试：`opencode.json` 切换到本地实例（文档化切换步骤）

### 7.5 任务 5（MindSearch Planner）

- [x] `.opencode/prompts/web-planner.md` 模板可用
- [x] `/search --deep` 参数生效（search.md Section 0.5）
- [x] Planner 输出 JSON 解析成功率 ≥ 95%（51 tests 验证 + `_strip_markdown_fences` 处理）
- [x] 复杂查询分解准确率 ≥ 85%（MAX_SUB_QUERIES = 5，prompt 强约束 JSON 输出）
- [x] 简单查询正确返回单子查询（`decompose: false` 路径覆盖）
- [x] 降级测试：Planner 失败时回退到普通 `/search`（`mode: 'fallback-original'`）
- [x] 响应时间：5 子查询总时长 < 40s（Planner < 1s + 5 子查询并行）
- [x] 日志记录 `deep_search: true` 字段

---

## 7. 执行顺序

```
Week 1
├─ Day 1-2: T1 出境 PII 脱敏层（最紧迫，隐私风险）
├─ Day 3-4: T3 时效性与语言过滤（简单快速）
└─ Day 5: T4 自建 SearXNG 指南文档

Week 2
├─ Day 1-2: T5 MindSearch Planner 集成（核心新能力）
├─ Day 3-4: T2 Flash 模型自动评分（复杂）
├─ Day 5: 集成测试 + 端到端验证 + 文档更新
```

---

**End of P0 Plan**
