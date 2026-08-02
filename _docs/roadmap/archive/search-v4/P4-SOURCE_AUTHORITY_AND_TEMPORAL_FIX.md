# Archived Plan: P4 — 搜索质量与权威性修复规划

> Historical plan and incident analysis. Not a current specification or status report.

**触发来源**：`E:\system_folder\Agent-Forge_错误分析报告.md`（FIT2004 课程报告 16 项事实性错误）
**规划范围**：基于 Agent-Forge 暴露的 10 类根因，逐一映射到本 Search v4.3.1 代码库，识别同源问题与项目特有额外问题，形成分阶段修复执行计划
**规划日期**：2026-07-20
**规划性质**：仅形成执行规划，不修改代码；后续按阶段分批执行
**前置状态**：P0–P3 已完成并 merge，863 单元测试通过，3-stage review 全部通过

---

## 一、背景与触发

### 1.1 Agent-Forge 错误事件概述

Agent-Forge（基于 MindSearch 集成构建的报告生成 agent）在生成 `FIT2004_Comprehensive_Guide.pdf` 时，相对 Monash 2026 官方 handbook 出现 **16 项事实性错误**（致命 6 + 严重 10），错误率 ≥ 60%。

错误类别分布：
- Assessment 结构错误（4 项致命）
- Hurdle 机制错误（2 项致命）
- 教学团队错误（2 项严重）
- Contact Hours 错误（1 项严重）
- Prerequisites 错误（1 项严重）
- Learning Outcomes LLM 润色污染（4 项严重）
- Course Synopsis 信息源污染（2 项严重）

### 1.2 核心症状

同一份报告内混用 4 个不同年份数据（2022/2023/2024/2025），封面标注 "July 2026"，构成严重时序一致性失败。Agent-Forge 将 L1 官方 handbook 与 L3 学生 GitHub 笔记视为同等权威，因 "学生笔记内容更丰富" 而用 L3 覆盖 L1。

### 1.3 与本 Search 模块的关系

本 Search v4.3.1 是 Agent-Forge 调用 MindSearch 集成时的核心搜索基础设施。Agent-Forge 通过 `/search --deep --aggregate` 流程获取信息，再由 LLM 综合输出。报告中识别的 10 类根因，**有 8 类在本 Search 代码库中存在同源架构缺陷**，需要在 P4 阶段系统性修复，避免未来类似 Agent-Forge 复发。

---

## 二、Agent-Forge 报告根因 ↔ Search v4.3.1 代码映射分析

对报告 10 类根因逐一映射到本 Search 代码库，判断是否存在同源问题。

### 根因 1：信息源权威性分层缺失 → **存在同源问题（致命）**

**报告问题**：Agent-Forge 把学生 GitHub 笔记视为与 handbook 同级权威。

**代码定位**：[modules/search/quality.py:58-73](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

```python
_AUTHORITY_MAP = {
    'official-docs': 3, 'official_docs': 3, 'official': 3,
    'github': 3,           # ← 致命：GitHub 全部打 3 分，与 official-docs 同级
    'docs': 3, 'reference': 3,
    'blog': 2, 'medium': 2, 'dev.to': 2,
    'forum': 1, 'stackoverflow': 2, 'reddit': 1,
    'unknown': 0,
}
```

**问题分析**：
- `_AUTHORITY_MAP` 仅按 MCP 返回的 `source` 字段做 12 类扁平映射，**无 L1/L2/L3/L4 分层**
- `github` 评级 3，等于 `official-docs`。这意味着 `github.com/jenul-ferdinand/algorithms`（学生笔记）与 `handbook.monash.edu`（L1 官方）在本项目中**也被视为同级权威**
- 域名后缀加权（[quality.py:135-148](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)）只识别 `.gov` / `.edu` / `.org` / `.dev`，**`handbook.monash.edu` 因不含 `docs.` / `developer.` 子串仅得 2 分**（被 .edu 路径识别为 3，但仅当 netloc 以 `.edu` 结尾时；`monash.edu` 符合，但学生仓库 `github.com` 也因 source=github 得 3 分）

**同源问题严重度**：致命。Agent-Forge 错误的直接代码源头。

### 根因 2：MCP 多工具并发调用的时序不对齐 → **存在同源问题（严重）**

**报告问题**：Agent-Forge 多源并发搜索时各结果命中不同时点缓存，未做时间戳对齐。

**代码定位**：
- [modules/search/parallel.py](file:///e:/system_folder/.claude/.claude/modules/search/parallel.py)：`parallel_search` 并行调用三层 MCP，结果合并时仅做 URL 去重
- [modules/search/aggregator.py:99-163](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)：`_format_results_for_prompt` 仅记录 title/url/snippet/source，**无 `fetched_at` / `data_year` 字段**
- [modules/search/search.py:239-255](file:///e:/system_folder/.claude/.claude/modules/search/search.py)：`cache_store` 仅记录 `cached_at`（缓存写入时刻），**不记录结果的原始数据年份**

**问题分析**：
- 并行调用返回的 results 来自不同 MCP（DuckDuckGo / SearXNG / Serper），每个 MCP 内部缓存时点不同
- aggregator 聚合时不传递时间戳，DeepSeek LLM 收到的是 "无时间锚点" 的多源数据
- 与 Agent-Forge 报告中 "搜索 1 命中 2025 CDN 缓存 / 搜索 2 命中 2023 GitHub 快照" 完全同源

**同源问题严重度**：严重。本项目中虽未直接生成 PDF，但任何 `--aggregate` 调用都可能产生年份混乱的 Markdown 输出。

### 根因 3：LLM 置信度幻觉润色 → **存在同源问题（严重）**

**报告问题**：Agent-Forge 对官方 LOs 文本做 LLM 润色，添加原文不存在的短语。

**代码定位**：[.opencode/prompts/result-aggregator.md:46-58](file:///e:/system_folder/.claude/.claude/.opencode/prompts/result-aggregator.md)

```
## Rules
1. 必须标注来源: 每个事实后附 [Title](URL)
2. 矛盾处理: 不同来源的矛盾信息需明确指出
3. 不确定信息: 标注"（未证实）"或"（来源不明）"
4. 语言一致
5. 不编造: 严格基于提供的搜索结果，不引入外部知识
6. 来源上限: 最多引用 10 个来源
7. 核心发现: 3-5 个关键点
8. 详细分析: 200-500 字
9. 置信度
```

**问题分析**：
- prompt 仅有 "不编造" 规则，**无 "verbatim mode"**（对契约性文本如 LOs / Assessment / Prerequisites 必须逐字引用）
- aggregator 的本质是 "综合语义聚合"，LLM 倾向于 "优化" 原文，与 Agent-Forge 同源
- 温度 0.2（[aggregator.py:275](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)）虽低但仍非 0，存在润色风险

**同源问题严重度**：严重。任何对官方文档的聚合都可能被 LLM 改写。

### 根因 4：验证回路缺失 → **存在同源问题（致命）**

**报告问题**：Agent-Forge 拿到 scraper 仓库数据后未回访 handbook 官网做最终核对。

**代码定位**：[.opencode/commands/search.md](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md)

**问题分析**：
- `/search` 命令 14 个 step（0 → 0.13 → 1 → 7.5）**无 "回访最终权威源" 步骤**
- Step 8 "Follow-up Tools" 仅是 "可选"（fetch / context7 / github），非强制
- aggregator 流程是 "搜索 → 候选源 → 选最丰富的 → 输出"，与 Agent-Forge 完全同源
- 无 "二次抓取交叉验证" 强制步骤（报告 P1 修复项 #8）

**同源问题严重度**：致命。这是 Agent-Forge 错误率 60% 的关键缺口。

### 根因 5：日期上下文的语义理解失效 → **存在同源问题（严重）**

**报告问题**：Agent-Forge 知道当前日期但未与数据年份做差值比较。

**代码定位**：
- [modules/search/quality.py:158-200](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)：`_score_freshness` 仅按 URL/snippet 正则提取年份，age = current_year - latest_year，**无 staleness threshold 警报**
- [modules/search/search.py:373-403](file:///e:/system_folder/.claude/.claude/modules/search/search.py)：`_extract_year` 仅返回年份整数，**不触发 "数据可能过时" 推理**

**问题分析**：
- 2024 数据在 2026-07 评分：age=2 → freshness=1.0（满分 2.0 的 50%），**不会触发 "需重新搜索" 警报**
- 系统时间（`datetime.now().year`）仅用于评分计算，**不参与 ReAct 推理链**
- 无 "数据年份 vs 当前日期差距 > N 个月 → 强制重新搜索" 规则

**同源问题严重度**：严重。报告根因 5 直接映射。

### 根因 6：过度依赖学生经验型资料 → **存在同源问题（严重）**

**报告问题**：Agent-Forge 引用 3 个过时 GitHub 仓库视为与 handbook 同等。

**代码定位**：[modules/search/quality.py:58-73](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)（同根因 1）

**问题分析**：
- `github: 3` 评级使所有 GitHub 仓库（含学生笔记、handbook scraper、官方文档镜像）一律 3 分
- 无 "GitHub 仓库分类"（official org repo / personal notes / scraper）
- 无 "GitHub last_commit_at" 检查（[jenul-ferdinand/algorithms](https://github.com/jenul-ferdinand/algorithms) 最后提交 2024，仍得 3 分）

**同源问题严重度**：严重。直接对应报告根因 6。

### 根因 7：内容丰富度被误判为内容正确性 → **存在同源问题（严重）**

**报告问题**：Agent-Forge 用学生笔记覆盖 handbook（因 "更丰富"）。

**代码定位**：[modules/search/quality.py:82-116](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

```python
def _score_relevance(query, results):
    # 提取 query 关键词
    keywords = [w.lower() for w in re.split(r'\W+', query.strip())
               if w and len(w) >= 2 and w.lower() not in stop_words]
    # 关键词命中比例 → 0-4 分
    hits = sum(1 for kw in keywords if kw in text)
    total_hits += (hits / len(keywords))
    return min(4.0, avg_hit_ratio * 4.0)
```

**问题分析**：
- 关键词命中比例 **无语义匹配**："JS" 不会匹配 "JavaScript"，"RSC" 不会匹配 "React Server Components"
- 学生笔记因含更多技术细节（Karatsuba / Floyd-Warshall），关键词命中数更高 → relevance 分更高
- handbook 文档简洁，关键词命中较少 → relevance 分较低
- aggregator.py 在选 "最丰富源" 时也会倾向学生笔记（同源）

**同源问题严重度**：严重。评分机制本身鼓励 "丰富但可能错误" 的内容。

### 根因 8：缺少数据来源声明的粒度 → **存在同源问题（中等）**

**报告问题**：Agent-Forge 每章节未标注 "本段数据来自哪年哪源"。

**代码定位**：
- [modules/search/search.py:273-284](file:///e:/system_folder/.claude/.claude/modules/search/search.py)：log entry 仅记录 query / timestamp / layers_used / top_results（title/url/snippet/source）
- [modules/search/search.py:152-157](file:///e:/system_folder/.claude/.claude/modules/search/search.py)：top_results 字段固定 4 个，**无 `source_tier` / `data_year` / `accessed_at` / `authority_level`**
- [modules/search/aggregator.py:146-155](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)：prompt 中 source 字段仅为 `unknown` / `github` / `blog`，**无 URL → authority tier 映射**

**问题分析**：
- 读者无法从 history log 判断某条 fact 的来源权威性
- aggregator LLM 收到的 source 字段无权威层级信息
- memory MCP 写入（[search.md:474-488](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md)）仅存 redacted query + top_results，**无 source attribution**

**同源问题严重度**：中等。影响下游 agent 决策能力。

### 根因 9：补丁式累积而非重写式更新 → **部分存在（中等）**

**报告问题**：Agent-Forge 分章节独立调用 LLM，每 chunk 独立"正确"，合在一起年份混乱。

**代码定位**：
- [modules/search/search.py](file:///e:/system_folder/.claude/.claude/modules/search/search.py)：单文件 939 行，9 个子命令堆叠（log / recent / stats / find / save / cache-get / cache-clean / health / filter）
- 项目版本累积：v4.0 → v4.1 → v4.2 → v4.3，每版本加新功能但未做整体一致性 review
- MCP server 重复：[serper_mcp.py](file:///e:/system_folder/.claude/.claude/modules/search/serper_mcp.py) / [arxiv_mcp.py](file:///e:/system_folder/.claude/.claude/modules/search/arxiv_mcp.py) / [semantic_scholar_mcp.py](file:///e:/system_folder/.claude/.claude/modules/search/semantic_scholar_mcp.py) JSON-RPC 协议处理重复

**问题分析**：
- 本项目虽不直接生成 PDF，但 `--aggregate` 输出 Markdown 时**同样不做全局一致性检查**
- aggregator 单次调用，MAX_OUTPUT_TOKENS=800，长报告必然被截断 → 下游 agent 分章节调用 → 与 Agent-Forge 同源
- 项目文档（SKILL.md / search.md）随版本累积变长，无定期重审

**同源问题严重度**：中等。架构层面问题。

### 根因 10：过早触发"答案已足够"判断 → **存在同源问题（中等）**

**报告问题**：Agent-Forge 拿到 3-4 个搜索结果就停止，未做 "是否有更新数据" 二轮探索。

**代码定位**：
- [.opencode/commands/search.md:154-157](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md)（Decision Rule）：
  ```
  - Score < 5 → escalate to next layer
  - Score ≥ 7 → sufficient, present results
  ```
- [modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)：评分仅基于 keyword + authority + freshness + diversity 4 维度，**无 "数据年份最新性" 强制阈值**

**问题分析**：
- 评分 ≥ 7 即 "sufficient"，但旧数据可能也 ≥ 7（如 query "FIT2004 assessment" → DuckDuckGo 返回 2024 数据 → keyword 命中高 + .monash.edu 域名 + GitHub 同级 3 分 → 评分可能 7.5）
- 无 "二轮探索" 机制（`should_explore_more` flag）
- 无 "搜索结果中最新年份 < 当前年份 - 1 → 强制重新搜索" 规则

**同源问题严重度**：中等。导致 Agent-Forge 在 3-4 个结果后停止，与报告根因 10 同源。

---

## 三、Search v4.3.1 项目特有额外问题

除报告 10 类根因外，本项目代码库存在以下额外问题，可能在 Agent-Forge 类场景下导致类似错误。

### 问题 A：`/search` 命令是 Markdown 指令而非可执行 pipeline（致命）

**代码定位**：[.opencode/commands/search.md](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md)

**问题**：14 个 step（0 → 0.13 → 1 → 7.5）依赖 LLM 按文档执行，**不是确定性 Python pipeline**。不同模型/会话可能跳步，Agent-Forge 可能跳过 "回访权威源" 这一步。

**影响**：所有根因修复都依赖 step 执行，若 step 本身不可执行，修复无效。

### 问题 B：search.py log 的 PII 脱敏仅 4 个 pattern，privacy.py redact_outbound 有 7 个（中等）

**代码定位**：
- [modules/search/search.py:57-66](file:///e:/system_folder/.claude/.claude/modules/search/search.py)：仅 4 pattern（email / phone_cn / id_cn / phone_my）
- [modules/search/privacy.py](file:///e:/system_folder/.claude/.claude/modules/search/privacy.py)：7 pattern（含 bank_card / ip / address_cn）

**问题**：top_results 中可能保留地址 / 银行卡 / IP。SKILL.md 已文档化但未修复。

**影响**：日志文件 PII 泄露风险，memory MCP 持久化时也带 PII。

### 问题 C：缓存无 timestamp 验证（严重）

**代码定位**：[modules/search/search.py:187-219](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**问题**：`search_cache.json` 24h TTL 基于 `cached_at`（缓存写入时刻），但缓存内的 `top_results` 可能来自更旧的数据源。Agent-Forge 命中 2023 年 CDN 缓存的同源问题。

**影响**：缓存命中后无法判断数据原始年份，可能用过时数据。

### 问题 D：planner.py 的 max_subqueries=5 上限过低（严重）

**代码定位**：[modules/search/planner.py:58](file:///e:/system_folder/.claude/.claude/modules/search/planner.py)

**问题**：复杂研究类查询（如 "FIT2004 课程完整指南"）需分解为 ≥ 10 子查询（Assessment / Hurdle / Staff / Contact Hours / Prerequisites / LOs / Synopsis / Weekly / Topic Breakdown / Resources），5 个不够，覆盖度不足，agent 自己补内容（幻觉来源）。

**影响**：复杂查询覆盖不全，迫使 agent 补内容。

### 问题 E：aggregator.py 的 MAX_OUTPUT_TOKENS=800 过低（严重）

**代码定位**：[modules/search/aggregator.py:61](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)

**问题**：800 tokens 远不足以生成完整报告，必然截断 → 下游 agent 分章节独立调用 → 补丁式累积（报告根因 9）。

**影响**：长报告生成场景下，aggregator 输出不完整，迫使 agent 多次调用，年份混乱风险上升。

### 问题 F：arxiv_mcp / semantic_scholar_mcp 不参与 quality.py 评分（中等）

**代码定位**：[modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py) 评分仅接受 `[{title, url, snippet, source}]`，arxiv/S2 结果字段不同（含 abstract / authors / year / citationCount）。

**问题**：学术搜索结果不进入 `score_results`，agent 无法用统一评分判断学术结果质量。

**影响**：学术查询场景下，agent 无客观质量信号，可能凭 citationCount 自行判断（但不准确）。

### 问题 G：无 query rewriting / spell correction（中等）

**代码定位**：[modules/search/planner.py](file:///e:/system_folder/.claude/.claude/modules/search/planner.py) 仅做分解，不展开缩写。

**问题**："FIT2004" 不会被自动展开为 "FIT2004 Algorithms and Data Structures 2026 Monash handbook"，搜索引擎可能返回 2018 年的 FIT2004 数据。

**影响**：查询语义不足，搜索引擎返回旧版本数据。

### 问题 H：无 circuit breaker（中等）

**代码定位**：[modules/search/serper_mcp.py](file:///e:/system_folder/.claude/.claude/modules/search/serper_mcp.py) / [parallel.py](file:///e:/system_folder/.claude/.claude/modules/search/parallel.py)

**问题**：Serper 配额超限后无熔断机制，会持续 429。报告中 Agent-Forge 持续重试也是同源。

**影响**：高负载场景下持续失败，可能迫使 agent 降级用更旧的缓存数据。

### 问题 I：`_score_authoritativeness` 的域名判断粗糙（严重）

**代码定位**：[modules/search/quality.py:135-148](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

```python
if netloc.endswith('.gov') or netloc.endswith('.edu'):
    score = max(score, 3)
elif netloc.endswith('.org') or netloc.endswith('.dev'):
    score = max(score, 2)
...
if 'docs.' in netloc or 'developer.' in netloc:
    score = max(score, 3)
```

**问题**：
- `handbook.monash.edu` → 因 .edu 得 3，但 `monash.edu/fit2004`（学生页面）也得 3
- `github.com` 因 source=github 已得 3，不再走域名加权
- 无 `handbook.` / `moodle.` / `official.` 子串识别
- 不区分 `monash.edu`（L1 官方）vs `student.monash.edu`（L3 学生页面）

**影响**：评分无法区分同域名下不同权威性的页面。

### 问题 J：无 freshness staleness threshold（严重）

**代码定位**：[modules/search/quality.py:185-197](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

```python
age = _VALID_YEAR_MAX - latest
if age <= 0:
    scores.append(2.0)
elif age <= 1:
    scores.append(1.5)
elif age <= 2:
    scores.append(1.0)
elif age <= 3:
    scores.append(0.5)
else:
    scores.append(0.0)
```

**问题**：2024 数据在 2026-07 评分 age=2 → freshness=1.0（满分 2 的 50%），仍可能综合评分 ≥ 7，不触发 "需重新搜索" 警报。

**影响**：旧数据不被识别为 "过时"，进入 aggregate 流程。

### 问题 K：planner 的 sub_queries 无 query rewriting（中等）

**代码定位**：[modules/search/planner.py:226-285](file:///e:/system_folder/.claude/.claude/modules/search/planner.py)

**问题**：sub_queries 仅是原始字符串拆分，无 "FIT2004 → FIT2004 Algorithms and Data Structures 2026 handbook" 自动扩展。

**影响**：子查询仍用缩写，搜索引擎返回旧版本数据。

### 问题 L：aggregator 的 `_fallback_summary` 不做 source attribution（中等）

**代码定位**：[modules/search/aggregator.py:313-381](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)

**问题**：降级路径直接拼接，每条事实无来源 URL 标注，仅 markdown 链接形式。

**影响**：降级模式下读者无法判断数据来源。

### 问题 M：memory MCP 仅存 redacted query + top_results（中等）

**代码定位**：[.opencode/commands/search.md:474-488](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md)

**问题**：不存 source attribution / data_year / authority_tier，未来 agent 检索时无法判断数据年份与权威性。

**影响**：memory 中历史数据无元信息，下游 agent 可能用过时数据。

### 问题 N：stats 子命令无 "data_year 分布"（轻微）

**代码定位**：[modules/search/search.py:518-566](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**问题**：仅统计 total / avg_score / satisfaction_rate / fallback_rate / saved_rate / 各层使用次数，**无数据年份分布**。

**影响**：无法发现历史搜索的数据年份分布，无法主动发现时序不一致问题。

### 问题 O：MindSearch React 前端完全未集成（中等）

**代码定位**：[MindSearch/frontend/React/](file:///e:/system_folder/.claude/.claude/MindSearch/frontend/React/)

**问题**：完整 React mind-map 前端存在但未集成到 `/search` 流程，结果无思维图可视化。

**影响**：用户无法可视化看到 Planner 分解的子查询结果对比，难以发现年份不一致。

---

## 四、修复优先级矩阵

| # | 问题 | 严重度 | 影响范围 | 修复成本 | 优先级 |
|---|------|--------|----------|----------|--------|
| 1 | 信息源权威性分层缺失（根因 1+6） | 致命 | 全报告 | 中 | P4.1 |
| 4 | 验证回路缺失 | 致命 | 全报告 | 中 | P4.1 |
| A | `/search` 命令是 Markdown 指令 | 致命 | 全流程 | 高 | P4.1 |
| 2 | 时序不对齐 | 严重 | 多源并发 | 中 | P4.2 |
| 3 | LLM 润色污染 | 严重 | 官方文本 | 低 | P4.2 |
| 5 | 日期推理链失效 | 严重 | 全报告 | 中 | P4.2 |
| 7 | 内容丰富度误判 | 严重 | heuristic 评分 | 中 | P4.2 |
| C | 缓存无 timestamp 验证 | 严重 | 缓存命中 | 低 | P4.2 |
| D | max_subqueries=5 过低 | 严重 | 复杂查询 | 低 | P4.2 |
| E | MAX_OUTPUT_TOKENS=800 过低 | 严重 | 长报告 | 低 | P4.2 |
| I | 域名判断粗糙 | 严重 | authoritativeness | 中 | P4.2 |
| J | freshness staleness threshold | 严重 | freshness 评分 | 低 | P4.2 |
| 8 | source attribution 缺失 | 中等 | history log | 中 | P4.3 |
| 9 | 补丁式累积 | 中等 | 架构 | 高 | P4.3 |
| 10 | 过早"答案已足够" | 中等 | Decision Rule | 低 | P4.3 |
| B | PII 脱敏 4 vs 7 pattern | 中等 | log 入站 | 低 | P4.3 |
| F | arxiv/S2 不参与评分 | 中等 | 学术搜索 | 中 | P4.3 |
| G | 无 query rewriting | 中等 | 缩写查询 | 中 | P4.3 |
| H | 无 circuit breaker | 中等 | 高负载 | 中 | P4.3 |
| K | sub_queries 无 rewriting | 中等 | 子查询 | 低 | P4.3 |
| L | fallback_summary 无 attribution | 中等 | 降级路径 | 低 | P4.3 |
| M | memory MCP 无 attribution | 中等 | 持久化 | 中 | P4.3 |
| N | stats 无 data_year 分布 | 轻微 | 诊断 | 低 | P4.3 |
| O | MindSearch 前端未集成 | 中等 | 可视化 | 高 | P4.3 |

---

## 五、分阶段执行计划

### P4.1 致命修复（Week 1–2）

**目标**：消除 Agent-Forge 错误的直接代码源头（信息源权威性分层 + 验证回路 + 可执行 pipeline）

#### P4.1.1 信息源权威性分层（根因 1+6+I）

**模块**：[modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

**新增**：
1. 重构 `_AUTHORITY_MAP` 为 L1/L2/L3/L4 四层：
   ```python
   AUTHORITY_TIERS = {
       'L1': {  # 官方权威源
           'domains': ['handbook.monash.edu', 'moodle.monash.edu', 'monash.edu/policy',
                       '.gov', '.edu', 'docs.python.org', 'docs.python.org',
                       'react.dev', 'developer.mozilla.org', 'kubernetes.io', '...'],
           'source_fields': ['official-docs', 'official_docs', 'official'],
           'score': 3,
       },
       'L2': {  # 教师 / 官方邮件列表
           'domains': ['users.monash.edu', 'mail-archive.com'],
           'source_fields': [],
           'score': 2,
       },
       'L3': {  # 学生笔记 / GitHub 仓库
           'domains': ['github.com', 'gitlab.com', 'gitee.com'],
           'source_fields': ['github', 'personal'],
           'score': 1,  # ← 从 3 降到 1
       },
       'L4': {  # 第三方博客 / 论坛
           'domains': ['medium.com', 'dev.to', 'reddit.com', 'stackoverflow.com'],
           'source_fields': ['blog', 'medium', 'forum', 'stackoverflow', 'reddit'],
           'score': 1,
       },
   }
   ```
2. 新增 `_classify_authority(url, source)` 函数：返回 `{'tier': 'L1', 'score': 3, 'reason': 'handbook.monash.edu'}`
3. 修改 `_score_authoritativeness` 调用 `_classify_authority` 替代旧的 `_AUTHORITY_MAP.get` + 域名后缀判断
4. 维护 `markconfig/authority_whitelist.json`：用户可配置个人权威域名白名单

**验收**：
- 单元测试覆盖 4 个 tier 至少各 5 个域名
- `handbook.monash.edu` 评分必须 ≥ 3
- `github.com/jenul-ferdinand/algorithms` 评分必须 ≤ 1
- 863 个旧测试不破坏

#### P4.1.2 验证回路（根因 4）

**模块**：新增 `modules/search/verifier.py`

**新增**：
1. `verify_against_authority(query, results, authority_domains)` 函数
2. 逻辑：
   - 从 results 提取 L1 域名候选
   - 若 query 含 "2026" / "latest" / "current" 等时效关键词
   - 调用 `fetch` MCP 二次抓取 L1 域名首页
   - 对比 results 与 L1 抓取内容，标记 `verified: true/false`
3. 集成到 `/search` 命令 Step 6.5（在 formatting 之前）
4. aggregator 输入新增 `verified` 字段

**验收**：
- 单元测试覆盖 query 含 "2026" 时触发 L1 二次抓取
- 失败兜底：verify 失败不阻塞主流程，仅 warning

#### P4.1.3 可执行 Pipeline（问题 A）

**模块**：新增 `modules/search/orchestrator.py`

**新增**：
1. `SearchOrchestrator` 类：把 `/search` 命令的 14 个 step 封装为可执行 Python pipeline
2. 方法链：
   ```python
   orchestrator = SearchOrchestrator(query, flags)
   orchestrator.redact_pii()      # Step 0
   orchestrator.plan()             # Step 0.5
   orchestrator.aggregate_pre()    # Step 0.7
   orchestrator.parallel_exec()   # Step 0.8
   orchestrator.cache_lookup()    # Step 1
   orchestrator.detect_location()# Step 2
   orchestrator.classify_query()  # Step 3
   orchestrator.execute_layers()  # Step 4
   orchestrator.dedup()           # Step 5
   orchestrator.format()          # Step 6
   orchestrator.verify()          # Step 6.5 (P4.1.2)
   orchestrator.log()             # Step 7
   orchestrator.persist_memory()  # Step 7.5
   result = orchestrator.result
   ```
3. `/search` 命令修改为 `python -m modules.search.orchestrator "<query>" <flags>` 单行调用
4. 保留 [search.md](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md) 作为人类可读说明，但实际执行走 orchestrator.py

**验收**：
- orchestrator.py 单元测试覆盖所有 step 调用顺序
- 失败兜底：任何 step 失败不阻塞后续 step
- `--dry-run` 模式打印 pipeline 计划但不执行

---

### P4.2 严重修复（Week 3–4）

**目标**：修复时序一致性、LLM 润色、评分机制三大类问题

#### P4.2.1 时序对齐（根因 2 + 问题 C）

**模块**：[modules/search/parallel.py](file:///e:/system_folder/.claude/.claude/modules/search/parallel.py) + [aggregator.py](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py) + [search.py](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**修改**：
1. `parallel_search` 返回结果新增 `fetched_at` 字段（每个 result 标注抓取时刻 ISO 8601）
2. `cache_store` 新增 `data_year` 字段（从 result 的 URL/snippet 提取最新年份）
3. `cache_get` 命中时检查 `data_year`，若 `< current_year - 1` → 标记 `stale: true`，返回但仍可使用（让调用方决策）
4. `aggregator._format_results_for_prompt` 在每个 result 后追加 `(fetched_at: YYYY-MM-DD, data_year: YYYY)`
5. `result-aggregator.md` prompt 新增规则：
   ```
   - 若 results 的 data_year 跨度 > 1 年，必须在 "置信度" 部分明确指出
   - 若 data_year 最早 < 当前年份 - 1，置信度评级强制降一级
   ```

**验收**：
- 单元测试覆盖 fetched_at / data_year 字段传递
- aggregator 输出含 "data_year 跨度" 标注

#### P4.2.2 LLM Verbatim 模式（根因 3 + 问题 L）

**模块**：[.opencode/prompts/result-aggregator.md](file:///e:/system_folder/.claude/.claude/.opencode/prompts/result-aggregator.md) + [aggregator.py](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)

**修改**：
1. prompt 新增 "Verbatim Mode" 规则：
   ```
   ## Verbatim Mode (新增)
   对于以下类型的内容，必须逐字引用，禁止改写、补全、添加标签：
   - Learning Outcomes / Course Objectives
   - Assessment structure (权重 + 类型)
   - Prerequisites (课程代码列表)
   - Hurdle requirements
   - Official policies

   识别规则：若 source 为 L1（official-docs / handbook / .edu / .gov），且 snippet 含上述关键词，启用 verbatim mode
   ```
2. aggregator.py 新增 `_detect_verbatim_trigger(results)` 函数，返回 `bool`
3. 启用 verbatim mode 时，temperature 降至 0.0
4. `_fallback_summary` 新增 source attribution：每条结果后追加 `(source_tier: L1/L2/L3/L4, data_year: YYYY)`

**验收**：
- 单元测试：L1 + LOs 关键词 → verbatim_mode = True
- 单元测试：verbatim mode 下 temperature = 0.0
- 单元测试：fallback_summary 含 source_tier 标注

#### P4.2.3 日期推理链（根因 5 + 问题 J）

**模块**：[modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

**修改**：
1. `_score_freshness` 新增 staleness threshold：
   ```python
   STALENESS_THRESHOLD_YEARS = 1  # 数据年份落后当前 ≥ 1 年 → staleness_warning

   def _score_freshness(results):
       ...
       for r in results:
           ...
           if age > STALENESS_THRESHOLD_YEARS:
               scores.append(0.0)  # 强制 0 分
               stale_count += 1
           else:
               scores.append(2.0 if age <= 0 else 1.5)

       staleness_warning = stale_count > len(results) * 0.5
       return score, {'staleness_warning': staleness_warning, 'stale_count': stale_count}
   ```
2. `score_results` 返回新增 `staleness_warning: bool` 字段
3. orchestrator 接到 `staleness_warning=True` 时 → 强制触发 P4.1.2 验证回路

**验收**：
- 单元测试：2024 数据在 2026-07 → freshness=0 + staleness_warning=True
- 单元测试：staleness_warning 触发 orchestrator 调用 verifier

#### P4.2.4 评分机制改进（根因 7 + 问题 I + F）

**模块**：[modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

**修改**：
1. `_score_relevance` 新增语义匹配层：
   - 调用 Flash API 做关键词扩展（"JS" → ["JS", "JavaScript"]）
   - 仅在 flash 模式启用，heuristic 模式保持原逻辑
2. `_score_authoritativeness` 调用 P4.1.1 的 `_classify_authority`
3. `score_results` 新增 `arxiv_results` / `s2_results` 可选参数，统一评分
4. 新增 `_score_completeness` 维度（0-1）：检查 query 关键词是否在结果中全部覆盖

**验收**：
- 单元测试：flash 模式下 "JS" 匹配 "JavaScript"
- 单元测试：arxiv_results 参与统一评分
- 单元测试：_score_completeness 正确计算覆盖率

#### P4.2.5 Planner / Aggregator 参数调整（问题 D + E）

**模块**：[modules/search/planner.py](file:///e:/system_folder/.claude/.claude/modules/search/planner.py) + [aggregator.py](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py)

**修改**：
1. `MAX_SUB_QUERIES` 从 5 提升到 10（仅在 `--deep-research` 模式下）
2. `MAX_OUTPUT_TOKENS` 从 800 提升到 2000（仅在 `--full-report` 模式下）
3. 新增 `--deep-research` / `--full-report` flags，默认仍保持 5 / 800 以控制成本

**验收**：
- 单元测试：--deep-research 模式 max_subqueries=10
- 单元测试：--full-report 模式 MAX_OUTPUT_TOKENS=2000

#### P4.2.6 缓存 timestamp 验证（问题 C）

**模块**：[modules/search/search.py](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**修改**：
1. `cache_store` 新增 `data_year` 字段（从 top_results 提取最新年份）
2. `cache_get` 命中时返回 `data_year` 字段
3. orchestrator 接到 `data_year < current_year - 1` 时 → 标记 `stale: true`，仍返回但 warning

**验收**：
- 单元测试：cache 命中 stale data_year 时返回 stale=true
- 单元测试：cache miss 时不报错

---

### P4.3 中等修复（Week 5–6）

**目标**：补齐 source attribution、补丁式重构、终止条件、剩余问题

#### P4.3.1 Source Attribution 完整化（根因 8 + 问题 L + M）

**模块**：[modules/search/search.py](file:///e:/system_folder/.claude/.claude/modules/search/search.py) + [aggregator.py](file:///e:/system_folder/.claude/.claude/modules/search/aggregator.py) + [.opencode/commands/search.md](file:///e:/system_folder/.claude/.claude/.opencode/commands/search.md)

**修改**：
1. log entry 的 top_results 字段新增：
   ```json
   {
     "title": "...",
     "url": "...",
     "snippet": "...",
     "source": "github",
     "source_tier": "L3",        // 新增
     "data_year": 2024,           // 新增
     "accessed_at": "2026-07-20", // 新增
     "authority_score": 1         // 新增（来自 P4.1.1）
   }
   ```
2. memory MCP 写入新增 `source_tier` / `data_year` / `accessed_at` 字段
3. aggregator `_fallback_summary` 每条结果后追加 `(tier: L3, year: 2024)`

**验收**：
- 单元测试：log entry 含 4 个新字段
- 单元测试：memory 写入含 source_tier

#### P4.3.2 过早"答案已足够"修复（根因 10）

**模块**：[modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py) + orchestrator

**修改**：
1. `score_results` 新增 `exploration_needed: bool` 字段：
   - 条件 1：staleness_warning = True
   - 条件 2：results 中 L1 占比 < 30%
   - 条件 3：data_year 跨度 > 2 年
2. orchestrator 接到 `exploration_needed=True` 时 → 强制触发下一轮搜索（最多 2 轮）
3. 新增 `should_explore_more(query, results, score)` 函数

**验收**：
- 单元测试：3 个条件各覆盖
- 单元测试：orchestrator 触发二轮搜索

#### P4.3.3 PII 脱敏对齐（问题 B）

**模块**：[modules/search/search.py:57-66](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**修改**：
1. `_PII_PATTERNS` 从 4 个扩展到 7 个，与 `privacy.py` 保持一致
2. 抽出 `_PII_PATTERNS` 到 `modules/search/pii_patterns.py` 共享
3. `privacy.py` 与 `search.py` 都从 `pii_patterns.py` 导入

**验收**：
- 单元测试：log 入站含 7 个 pattern
- 单元测试：privacy + search 共享同一 pattern 列表

#### P4.3.4 arxiv / S2 参与统一评分（问题 F）

**模块**：[modules/search/quality.py](file:///e:/system_folder/.claude/.claude/modules/search/quality.py)

**修改**：
1. `score_results` 新增 `arxiv_results` / `s2_results` 可选参数
2. 内部转换：arxiv_result → unified format `{title, url, snippet, source='arxiv', data_year=year, citation_count=citationCount}`
3. 统一参与 `_score_authoritativeness`（source='arxiv' → L2 评级 2 分）
4. s2 citationCount > 100 → authority bonus +1

**验收**：
- 单元测试：arxiv_results 参与评分
- 单元测试：高引论文 bonus 正确应用

#### P4.3.5 Query Rewriting（问题 G + K）

**模块**：新增 `modules/search/query_rewriter.py`

**新增**：
1. `rewrite_query(query, context=None)` 函数
2. 识别缩写：维护 `markconfig/abbreviation_map.json`
   ```json
   {
     "FIT2004": "FIT2004 Algorithms and Data Structures",
     "RSC": "React Server Components",
     "SSR": "Server-Side Rendering"
   }
   ```
3. 调用 Flash API 做 query expansion（仅 flash 模式）
4. 集成到 planner：sub_queries 在返回前过 `rewrite_query` 一遍

**验收**：
- 单元测试：FIT2004 → FIT2004 Algorithms and Data Structures
- 单元测试：planner sub_queries 经过 rewriter

#### P4.3.6 Circuit Breaker（问题 H）

**模块**：[modules/search/serper_mcp.py](file:///e:/system_folder/.claude/.claude/modules/search/serper_mcp.py)

**修改**：
1. 新增 `CircuitBreaker` 类：
   - 连续 3 次 429 → 开启熔断（60 秒）
   - 熔断期间所有请求直接返回 `success=false, reason='circuit_open'`
   - 60 秒后半开状态，单次试探
2. 集成到 `serper_search`

**验收**：
- 单元测试：3 次 429 触发熔断
- 单元测试：熔断期间请求直接拒绝

#### P4.3.7 stats data_year 分布（问题 N）

**模块**：[modules/search/search.py:518-566](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**修改**：
1. `show_stats` 新增 "数据年份分布" 输出
2. 从 top_results 提取 data_year 统计
3. 输出：
   ```
   数据年份分布:
     2026: 45 条 (30%)
     2025: 60 条 (40%)
     2024: 30 条 (20%)
     2023 或更早: 15 条 (10%)  ← 警告
   ```

**验收**：
- 单元测试：stats 输出含 data_year 分布
- 单元测试：旧数据年份被标记警告

#### P4.3.8 search.py 重构（问题 9）

**模块**：[modules/search/search.py](file:///e:/system_folder/.claude/.claude/modules/search/search.py)

**修改**：
1. 拆分 939 行单文件为：
   - `modules/search/cli.py`：主入口 + argparse
   - `modules/search/log.py`：log / recent / find / save 子命令
   - `modules/search/stats.py`：stats / filter 子命令
   - `modules/search/cache.py`：cache-get / cache-clean 子命令
   - `modules/search/health.py`：health 子命令
2. 保持 CLI 接口不变（`python modules/search/search.py <subcommand>` 仍可用）
3. 提取共用函数到 `modules/search/_common.py`

**验收**：
- 863 个旧测试不破坏
- CLI 接口行为完全一致

#### P4.3.9 MCP Server 基类抽象（问题 9）

**模块**：新增 `modules/search/_base_mcp.py`

**修改**：
1. 抽象 `BaseMcpServer` 基类：
   - `handle_jsonrpc(request)` 模板方法
   - `register_tool(name, handler)` 注册
   - `serve_stdio()` 主循环
2. `serper_mcp.py` / `arxiv_mcp.py` / `semantic_scholar_mcp.py` 继承 `BaseMcpServer`
3. 消除约 200 行重复 JSON-RPC 代码

**验收**：
- 863 个旧测试不破坏
- 代码行数减少 ≥ 30%

---

## 六、验收标准

### 6.1 P4.1 验收（致命修复）

| 验收项 | 标准 |
|--------|------|
| 信息源权威性分层 | `handbook.monash.edu` 评分 ≥ 3；`github.com/student-notes` 评分 ≤ 1 |
| 验证回路 | query 含 "2026" 时触发 L1 二次抓取 |
| 可执行 Pipeline | `python -m modules.search.orchestrator "..." --dry-run` 输出 14 step 执行计划 |
| 单元测试 | P4.1 新增 ≥ 80 测试，旧 863 测试不破坏 |
| Review | 3-stage review 通过，0 critical |

### 6.2 P4.2 验收（严重修复）

| 验收项 | 标准 |
|--------|------|
| 时序对齐 | aggregator 输出含 data_year 跨度标注 |
| Verbatim mode | L1 + LOs 关键词触发 verbatim，temperature=0.0 |
| 日期推理 | 2024 数据在 2026-07 → staleness_warning=True |
| 评分改进 | arxiv_results 参与统一评分；JS 匹配 JavaScript |
| Planner/Aggregator 参数 | --deep-research / --full-report 模式下上限提升 |
| 缓存 timestamp | cache 命中 stale data_year 时返回 stale=true |
| 单元测试 | P4.2 新增 ≥ 120 测试 |
| Review | 3-stage review 通过 |

### 6.3 P4.3 验收（中等修复）

| 验收项 | 标准 |
|--------|------|
| Source attribution | log entry / memory / fallback_summary 含 4 个新字段 |
| 二轮探索 | exploration_needed 触发二轮搜索 |
| PII 对齐 | search.py log 与 privacy.py 共享 7 pattern |
| arxiv/S2 评分 | 学术结果参与统一评分 |
| Query rewriting | FIT2004 自动展开 |
| Circuit breaker | 3 次 429 触发熔断 |
| stats data_year | 输出年份分布 |
| 重构 | search.py 拆分 + MCP 基类抽象，863 测试不破坏 |
| 单元测试 | P4.3 新增 ≥ 150 测试 |

### 6.4 全局验收

- **回归测试**：863 个旧测试 100% 通过
- **新增测试**：P4 全阶段新增 ≥ 350 测试
- **3-stage review**：每阶段通过 review-code / review-structure / review-risk
- **manifest.json**：版本升级到 v4.4.0
- **SKILL.md / search.md**：文档同步更新

### 6.5 功能验收（模拟 Agent-Forge 场景）

构造测试 query：`/search --deep --aggregate --deep-research --full-report "FIT2004 Monash 2026 handbook assessment structure"`

**期望输出**：
- Planner 分解为 ≥ 8 子查询（Assessment / Hurdle / Staff / Contact Hours / Prerequisites / LOs / Synopsis / Weekly）
- 每个子查询触发 L1 验证（访问 handbook.monash.edu）
- aggregator 输出含 source_tier / data_year 标注
- data_year 跨度 ≤ 1 年（全部 2026）
- 置信度评级 ≥ 中（L1 占比 ≥ 50%）
- staleness_warning = False
- exploration_needed = False

---

## 七、风险与回退

### 7.1 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| orchestrator.py 引入新 bug | 中 | 高 | 保留 `/search` Markdown 指令路径作为 fallback；`--legacy` flag 切回 |
| 权威性分层误判 | 中 | 中 | 维护 whitelist，用户可配置；初始保守，L1 仅明确官方域名 |
| Verbatim mode 漏判 | 中 | 中 | 关键词列表保守，仅在明确触发时启用 |
| Flash API 成本上升 | 低 | 低 | 月度预算监控；heuristic 模式仍可用 |
| 重构破坏 863 测试 | 中 | 高 | 分阶段 merge；每阶段跑全套测试 |

### 7.2 回退策略

- **P4.1 失败**：保留旧 `_AUTHORITY_MAP`，orchestrator 走 `--legacy` 路径
- **P4.2 失败**：staleness_warning 仅 warning 不强制重搜；verbatim mode 默认关闭
- **P4.3 失败**：重构可独立回退（不影响 P4.1/P4.2）
- **整体回退**：保留 v4.3.1 tag，必要时 `git revert` 到 P4 之前

### 7.3 兼容性

- **CLI 接口**：所有旧命令保持兼容（`python modules/search/search.py log ...` 仍可用）
- **MCP 协议**：3 个 MCP server 接口不变
- **配置文件**：`markconfig/authority_whitelist.json` / `markconfig/abbreviation_map.json` 为新增可选，未配置走默认值
- **缓存格式**：新增 `data_year` / `source_tier` 字段，旧缓存读取时缺失字段走默认值

---

## 八、执行顺序与依赖

```
P4.1.1 (权威性分层) ─┐
                      ├─→ P4.1.2 (验证回路) ─→ P4.1.3 (orchestrator)
P4.1.1 (权威性分层) ─┘                              │
                                                     ▼
P4.2.1 (时序对齐) ─┐
P4.2.2 (verbatim)  │
P4.2.3 (日期推理) ─┼─→ P4.2.4 (评分改进) ─→ P4.2.5 (参数) ─→ P4.2.6 (缓存)
P4.1.1             │
                   ▼
P4.3.1 (source attribution) ─→ P4.3.2 (二轮探索) ─→ P4.3.3 (PII 对齐)
                                                       ▼
P4.3.4 (arxiv/S2 评分) ─→ P4.3.5 (query rewriting) ─→ P4.3.6 (circuit breaker)
                                                       ▼
P4.3.7 (stats) ─→ P4.3.8 (重构 search.py) ─→ P4.3.9 (MCP 基类)
```

**强依赖**：
- P4.1.1 是 P4.2.4 / P4.3.1 / P4.3.4 的前置
- P4.1.3 是 P4.3.2 的前置（orchestrator 接收 exploration_needed）
- P4.2.3 是 P4.3.2 的前置（staleness_warning 触发 exploration_needed）

**可并行**：
- P4.2.1 / P4.2.2 / P4.2.3 可并行开发
- P4.3.4 / P4.3.5 / P4.3.6 可并行开发
- P4.3.8 / P4.3.9 可并行开发

---

## 九、附录

### 9.1 报告根因 → P4 修复映射总表

| Agent-Forge 报告根因 | 严重度 | 是否存在同源问题 | P4 修复项 |
|---------------------|--------|------------------|-----------|
| 1. 信息源权威性分层缺失 | 致命 | ✅ 存在（致命） | P4.1.1 |
| 2. MCP 时序不对齐 | 致命 | ✅ 存在（严重） | P4.2.1 |
| 3. LLM 幻觉润色 | 严重 | ✅ 存在（严重） | P4.2.2 |
| 4. 验证回路缺失 | 致命 | ✅ 存在（致命） | P4.1.2 |
| 5. 日期推理失效 | 严重 | ✅ 存在（严重） | P4.2.3 |
| 6. 过度依赖学生资料 | 严重 | ✅ 存在（严重） | P4.1.1（同根因 1） |
| 7. 内容丰富度误判 | 严重 | ✅ 存在（严重） | P4.2.4 |
| 8. 缺少 source attribution | 中等 | ✅ 存在（中等） | P4.3.1 |
| 9. 补丁式累积 | 中等 | ⚠️ 部分存在 | P4.3.8 / P4.3.9 |
| 10. 过早"答案已足够" | 中等 | ✅ 存在（中等） | P4.3.2 |

### 9.2 项目特有额外问题 → P4 修复映射总表

| 问题 | 严重度 | P4 修复项 |
|------|--------|-----------|
| A. /search 是 Markdown 指令 | 致命 | P4.1.3 |
| B. PII 脱敏 4 vs 7 | 中等 | P4.3.3 |
| C. 缓存无 timestamp 验证 | 严重 | P4.2.6 |
| D. max_subqueries=5 过低 | 严重 | P4.2.5 |
| E. MAX_OUTPUT_TOKENS=800 过低 | 严重 | P4.2.5 |
| F. arxiv/S2 不参与评分 | 中等 | P4.3.4 |
| G. 无 query rewriting | 中等 | P4.3.5 |
| H. 无 circuit breaker | 中等 | P4.3.6 |
| I. 域名判断粗糙 | 严重 | P4.1.1（同根因 1） |
| J. freshness staleness | 严重 | P4.2.3（同根因 5） |
| K. sub_queries 无 rewriting | 中等 | P4.3.5（同问题 G） |
| L. fallback 无 attribution | 中等 | P4.3.1 |
| M. memory 无 attribution | 中等 | P4.3.1 |
| N. stats 无 data_year 分布 | 轻微 | P4.3.7 |
| O. MindSearch 前端未集成 | 中等 | 延期至 v5.0（成本过高） |

### 9.3 修改文件清单（预估）

| 模块 | P4.1 | P4.2 | P4.3 | 总计 |
|------|------|------|------|------|
| quality.py | 修改 | 修改 | - | 2 阶段 |
| aggregator.py | - | 修改 | 修改 | 2 阶段 |
| parallel.py | - | 修改 | - | 1 阶段 |
| search.py | - | 修改 | 拆分 | 2 阶段 |
| planner.py | - | 修改 | - | 1 阶段 |
| serper_mcp.py | - | - | 修改 | 1 阶段 |
| arxiv_mcp.py | - | - | 修改 | 1 阶段 |
| semantic_scholar_mcp.py | - | - | 修改 | 1 阶段 |
| privacy.py | - | - | 修改 | 1 阶段 |
| result-aggregator.md | - | 修改 | - | 1 阶段 |
| web-planner.md | - | - | - | - |
| search.md | - | - | - | - |
| SKILL.md | 修改 | 修改 | 修改 | 3 阶段 |
| manifest.json | 修改 | 修改 | 修改 | 3 阶段 |
| **新增模块** | | | | |
| verifier.py | 新增 | - | - | 1 阶段 |
| orchestrator.py | 新增 | - | - | 1 阶段 |
| _base_mcp.py | - | - | 新增 | 1 阶段 |
| _common.py | - | - | 新增 | 1 阶段 |
| query_rewriter.py | - | - | 新增 | 1 阶段 |
| pii_patterns.py | - | - | 新增 | 1 阶段 |
| cli.py / log.py / stats.py / cache.py / health.py | - | - | 新增（拆分） | 1 阶段 |
| markconfig/authority_whitelist.json | 新增 | - | - | 1 阶段 |
| markconfig/abbreviation_map.json | - | - | 新增 | 1 阶段 |

**总计**：修改 9 个现有文件 + 新增 11 个模块/配置文件

---

## 十、总结

本 P4 修复规划针对 Agent-Forge 报告暴露的 10 类根因，结合本 Search v4.3.1 代码库的 15 项特有额外问题，形成 3 阶段共 18 个修复项的执行计划：

- **P4.1（致命，2 周）**：3 项 — 权威性分层 / 验证回路 / 可执行 pipeline
- **P4.2（严重，2 周）**：6 项 — 时序对齐 / verbatim / 日期推理 / 评分改进 / 参数调整 / 缓存 timestamp
- **P4.3（中等，2 周）**：9 项 — source attribution / 二轮探索 / PII 对齐 / arxiv 评分 / query rewriting / circuit breaker / stats / 重构 / MCP 基类

**核心思路**：从"信息源权威性"和"时序一致性"两个根本缺陷切入，通过 P4.1 建立权威性分层与验证回路，P4.2 修复评分与聚合机制，P4.3 补齐元信息与重构。

**关键差异**：相比 Agent-Forge 报告的 10 项修复建议，本规划额外识别 15 项项目特有问题（A–O），其中 **问题 A（/search 是 Markdown 指令而非可执行 pipeline）是 Agent-Forge 错误的最深层根因之一**——所有 step 修复都依赖 LLM 按文档执行，存在跳步风险。P4.1.3 orchestrator.py 是修复此问题的核心。

**预期成果**：P4 完成后，重新运行 Agent-Forge 类场景测试 query，期望错误率从 60%+ 降至 ≤ 10%，致命错误率从 25%+ 降至 0%。

---

*规划完毕，待用户确认后进入执行阶段。*
