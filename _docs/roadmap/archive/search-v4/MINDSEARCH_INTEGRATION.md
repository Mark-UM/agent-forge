# Archived Design: MindSearch 集成设计文档（方案 D）

> Historical design. The referenced vendored MindSearch tree is not maintained in this repository.

> **方案**：D — 选择性集成核心思想（Planner + Aggregator）
> **来源**：[InternLM/MindSearch](https://github.com/InternLM/MindSearch)（Apache 2.0）
> **集成位置**：v4.0 P0 任务 5（Planner）+ v4.1 P1 任务 5（Aggregator）
> **文档定位**：独立技术参考，详细说明方案 D 的设计依据与实现细节

---

## 目录

1. [方案选择依据](#1-方案选择依据)
2. [MindSearch 架构剖析](#2-mindsearch-架构剖析)
3. [方案 D 完整设计](#3-方案-d-完整设计)
4. [Planner Prompt 详细设计](#4-planner-prompt-详细设计)
5. [Aggregator Prompt 详细设计](#5-aggregator-prompt-详细设计)
6. [集成实现清单](#6-集成实现清单)
7. [成本与性能模型](#7-成本与性能模型)
8. [降级与容错策略](#8-降级与容错策略)
9. [与其他任务的协同](#9-与其他任务的协同)
10. [对比 MindSearch 完整部署](#10-对比-mindsearch-完整部署)

---

## 1. 方案选择依据

### 1.1 四方案评估回顾

| 方案 | 描述 | 复杂度 | 依赖 | 与 v3 兼容 | 推荐度 |
|------|------|--------|------|-----------|--------|
| A | 完整部署 MindSearch FastAPI 服务 | 高 | 多 | 差 | ⭐⭐ |
| B | OpenCode Subagent 重实现 | 中 | 零 | 好 | ⭐⭐⭐⭐ |
| C | 封装 MindSearch 为 MCP Server | 高 | 多 | 中 | ⭐⭐⭐ |
| **D** | **选择性集成核心思想** | **低** | **零** | **极好** | **⭐⭐⭐⭐⭐** |

### 1.2 方案 D 胜出理由

1. **轻量化原则**：用户明确要求"快捷方便轻量化"，方案 D 仅新增 2 个 prompt 文件
2. **零依赖**：不引入 FastAPI / Lagent / 前端依赖
3. **完全兼容**：保留 v3 所有优势（缓存、PII 脱敏、日志、位置感知、健康检查）
4. **核心价值保留**：获得 MindSearch 80% 价值（查询分解 + 结果聚合）
5. **可逆性**：删除 prompt 文件即可回退
6. **与 v4.0 规划无缝融合**：作为 P0 任务 5（Planner）和 P1 任务 5（Aggregator）

### 1.3 放弃其他方案的原因

| 方案 | 放弃原因 |
|------|---------|
| A 完整部署 | 引入 FastAPI + Lagent + 前端依赖，违背轻量化原则 |
| B Subagent | OpenCode Subagent 无原生并行，失去 MindSearch 核心优势 |
| C MCP 封装 | 依赖 MindSearch 完整代码，MCP 进程启动慢 5-10s |

---

## 2. MindSearch 架构剖析

### 2.1 原始架构

```
用户查询
   ↓
┌─────────────────────────────────┐
│  Web Planner（规划者 Agent）      │
│  - 拆解复杂查询为子查询树          │
│  - 决定搜索顺序与依赖关系          │
│  - 基于 Lagent v0.5 框架          │
│  - 使用 InternLM2.5 / GPT-4       │
└─────────────────────────────────┘
   ↓ 子查询列表
┌─────────────────────────────────┐
│  Web Searcher（搜索者 Agent）     │
│  - 并发执行多个子查询             │
│  - 每个子查询独立调用搜索引擎      │
│  - 智能聚合 + 去重 + 摘要         │
│  - 支持 DuckDuckGo/Bing/Brave/Google │
└─────────────────────────────────┘
   ↓ 结构化答案
最终输出
```

### 2.2 核心创新点

| 创新 | 价值 | 方案 D 如何获取 |
|------|------|----------------|
| 查询分解（Planner） | 复杂查询信息覆盖更全 | ✅ 用 DeepSeek Flash + prompt 模板 |
| 并行执行（Searcher） | 多子查询并发，延迟降低 | ⚠️ v4.0 串行，v4.2 P2 并行 |
| LLM 聚合 | 智能合成结构化答案 | ✅ 用 DeepSeek Flash + prompt 模板 |

### 2.3 性能数据（论文）

| 维度 | MindSearch | Perplexity Pro | ChatGPT |
|------|-----------|----------------|---------|
| 深度 | 95.32 | 87.91 | 79.20 |
| 广度 | 87.33 | 82.53 | 76.90 |
| 准确度 | 65.40 | 61.00 | 51.20 |

方案 D 预期能达到 MindSearch 80% 的质量提升。

---

## 3. 方案 D 完整设计

### 3.1 架构图

```
┌─────────────────────────────────────────────────────┐
│  OpenCode Agent (DeepSeek V4 Pro)                    │
│  - 接收用户查询                                       │
│  - 判断是否需要 --deep 模式                           │
│  - 调用 /search 命令                                  │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  /search --deep [--aggregate] query                  │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  Step 1: Web Planner (v4.0 T5)                       │
│  - 加载 .opencode/prompts/web-planner.md             │
│  - 调用 DeepSeek V4 Flash                            │
│  - 输出 JSON：sub_queries 数组（≤5 个）              │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  Step 2: 子查询执行（串行 v4.0 / 并行 v4.2）         │
│  对每个 sub_query:                                   │
│  ├─ PII 脱敏（privacy.py）                           │
│  ├─ 缓存检查（search.py cache-get）                  │
│  ├─ 三层 MCP 搜索（DuckDuckGo → SearXNG → Google）   │
│  └─ 结果收集                                          │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  Step 3: 合并 + URL 去重（search.py log auto-dedup） │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  Step 4: Result Aggregator (v4.1 T5，仅 --aggregate) │
│  - 加载 .opencode/prompts/result-aggregator.md      │
│  - 调用 DeepSeek V4 Flash                            │
│  - 输出结构化答案（核心发现 + 详细分析 + 来源）       │
└─────────────────────┬───────────────────────────────┘
                      ↓
┌─────────────────────────────────────────────────────┐
│  Step 5: 输出 + 日志记录                              │
│  ├─ 结构化答案（如有聚合）                            │
│  ├─ 原始结果列表                                      │
│  └─ 日志：deep_search: true, aggregated: true        │
└─────────────────────────────────────────────────────┘
```

### 3.2 参数矩阵

| 参数 | 版本 | 作用 |
|------|------|------|
| `--deep` | v4.0 | 触发 Planner 查询分解 |
| `--aggregate` | v4.1 | 触发 Aggregator 结果聚合（依赖 --deep） |
| `--planner-mode flash\|pro` | v4.0 | 选择 Planner 模型（默认 flash） |
| `--max-subqueries N` | v4.0 | 限制最大子查询数（默认 5） |

### 3.3 执行示例

#### 简单查询（不触发 --deep）
```
/search Python list sort method
→ 直接走 v3 三层搜索（单次）
→ 响应时间 5-15s
```

#### 深度查询（v4.0，仅 Planner）
```
/search --deep React Server Components 与传统 SSR 的区别和性能对比
→ Planner 分解为 4 子查询
→ 串行执行 4 子查询（每个走 v3 三层搜索）
→ 合并 + URL 去重
→ 输出原始结果列表
→ 响应时间 30-60s
```

#### 深度查询 + 聚合（v4.1，Planner + Aggregator）
```
/search --deep --aggregate React Server Components 与传统 SSR 的区别和性能对比
→ Planner 分解为 4 子查询
→ 串行执行 4 子查询
→ 合并 + URL 去重
→ Aggregator 生成结构化答案
→ 输出聚合答案 + 原始结果
→ 响应时间 35-70s
```

---

## 4. Planner Prompt 详细设计

### 4.1 设计原则

- **输出 JSON**：便于程序解析
- **限制子查询数**：≤5，避免过度分解
- **保持原文语言**：中文查询生成中文子查询
- **子查询独立可搜索**：每个都能单独输入搜索引擎
- **PII 安全**：prompt 明确禁止包含 PII

### 4.2 完整 Prompt 模板

见 [P0-PRIVACY_AND_QUALITY.md 任务 5.3](file:///e:/system_folder/.claude/.claude/upgrade_plan/P0-PRIVACY_AND_QUALITY.md#6-任务-5mindsearch-planner-集成)

### 4.3 关键决策点

| 决策 | 选择 | 理由 |
|------|------|------|
| 输出格式 | JSON | 便于解析，避免 Markdown 噪声 |
| 子查询上限 | 5 | MindSearch 默认值，平衡覆盖与成本 |
| 子查询下限 | 1 | 简单查询不分解 |
| 语言策略 | 跟随原文 | 避免翻译损失 |
| priority 字段 | high/medium/low | 供未来优先级排序用 |
| depends_on 字段 | 不采用 | v4.0 串行，v4.2 并行无需依赖 |

### 4.4 示例输出

**实现决策（2026-07-20 更新）**：实际 prompt (`web-planner.md`) 输出 `sub_queries: list[str]` 简化格式（更省 token），但 Planner 模块 (`planner.py:_parse_planner_json`) **同时兼容** spec 原始设计 `list[dict]` 格式（含 `query/priority/rationale` 字段），自动归一化为 `list[str]`。`priority` 字段保留供 v5.0+ 优先级排序使用。

**复杂查询**（spec 原始格式，仍兼容）：
```json
{
  "intent": "comparative",
  "complexity": "complex",
  "decompose": true,
  "sub_queries": [
    {"query": "React Server Components 原理", "priority": "high", "rationale": "理解 RSC 核心机制"},
    {"query": "传统 SSR 工作机制", "priority": "high", "rationale": "对比基准"},
    {"query": "RSC vs SSR 性能对比 benchmark", "priority": "high", "rationale": "直接回答性能问题"},
    {"query": "RSC 实际案例分析", "priority": "medium", "rationale": "补充实践视角"}
  ],
  "rationale": "比较类查询按主题拆分 + 一个聚合查询"
}
```

**简单查询**（实际简化格式，默认）：
```json
{
  "intent": "factual",
  "complexity": "simple",
  "decompose": false,
  "sub_queries": ["Python list sort method"],
  "rationale": "Atomic factual query, no decomposition needed"
}
```

---

## 5. Aggregator Prompt 详细设计

### 5.1 设计原则

- **强制来源标注**：每个事实必须标注 URL
- **矛盾信息识别**：不同来源冲突时明确指出
- **置信度评估**：高/中/低 + 理由
- **不编造**：仅基于搜索结果
- **语言跟随原文**

### 5.2 完整 Prompt 模板

见 [P1-SEMANTIC_AND_INTEGRATION.md 任务 5.3](file:///e:/system_folder/.claude/.claude/upgrade_plan/P1-SEMANTIC_AND_INTEGRATION.md#6-任务-5mindsearch-aggregator-集成)

### 5.3 关键决策点

| 决策 | 选择 | 理由 |
|------|------|------|
| 输出格式 | Markdown | 人类可读，OpenCode TUI 渲染 |
| 来源上限 | 10 | 避免过长 |
| 置信度 | 高/中/低 | 简单实用 |
| 矛盾处理 | 明确指出 | 避免误导 |
| 输入预处理 | 仅传 title+url+snippet | 控制 token 成本 |

### 5.4 示例输出

```markdown
## 综合答案

### 核心发现
- React Server Components (RSC) 是 React 18 引入的零捆绑架构（来源：[react.dev](https://react.dev/...)）
- 传统 SSR 在每次请求时完整渲染 HTML（来源：[Next.js docs](https://nextjs.org/...)）
- RSC 减少了 30-50% 的 JavaScript 打包体积（来源：[Vercel benchmark](https://vercel.com/...)）

### 详细分析
（基于多源结果的综合性分析...）

### 关键差异 / 对比
| 维度 | RSC | 传统 SSR |
|------|-----|---------|
| 渲染时机 | 构建时 + 运行时 | 仅运行时 |
| JS 体积 | 小 | 大 |
| ... | ... | ... |

### 来源列表
1. [React Server Components 官方文档](https://react.dev/...) — RSC 设计理念
2. [Next.js SSR 文档](https://nextjs.org/...) — SSR 实现细节
3. [Vercel 性能基准](https://vercel.com/...) — 量化对比数据

### 置信度
- 高（RSC 原理部分基于官方文档）
- 中（性能数据基于单一 benchmark，可能因场景而异）
```

---

## 6. 集成实现清单

### 6.1 v4.0 P0 交付物

| 文件 | 类型 | 说明 |
|------|------|------|
| `.opencode/prompts/web-planner.md` | 新增 | Planner prompt 模板 |
| `.opencode/commands/search.md` | 修改 | 新增 `--deep` 参数流程 |

### 6.2 v4.1 P1 交付物

| 文件 | 类型 | 说明 |
|------|------|------|
| `.opencode/prompts/result-aggregator.md` | 新增 | Aggregator prompt 模板 |
| `.opencode/commands/search.md` | 修改 | 新增 `--aggregate` 参数流程 |

### 6.3 代码变更量

| 文件 | 行数 | 说明 |
|------|------|------|
| `web-planner.md` | ~80 行 | Planner prompt + 示例 |
| `result-aggregator.md` | ~60 行 | Aggregator prompt + 示例 |
| `search.md` 扩展 | ~50 行 | 新参数流程描述 |
| **总计** | ~190 行 | 极轻量 |

对比方案 A（完整部署）：需要 clone MindSearch 仓库（数千行）+ Docker 配置 + 依赖管理。

---

## 7. 成本与性能模型

### 7.1 LLM 调用成本

| 场景 | Flash 调用 | Tokens | 单次成本 | 月度成本（100 次） |
|------|-----------|--------|---------|-------------------|
| 简单查询（不触发 --deep） | 0 | 0 | $0 | $0 |
| 深度查询（仅 Planner） | 1 | ~800 | $0.0008 | $0.08 |
| 深度查询 + 聚合 | 2 | ~4000 | $0.004 | $0.40 |

### 7.2 响应时间模型

| 场景 | v3 基线 | v4.0 --deep | v4.1 --deep --aggregate |
|------|---------|------------|----------------------|
| 简单查询 | 5-15s | 5-15s（不变） | 5-15s（不变） |
| 中等查询（3 子查询） | 15-30s | 30-50s | 35-60s |
| 复杂查询（5 子查询） | 30-60s | 50-100s | 55-120s |

### 7.3 性能优化路径

| 优化 | 版本 | 效果 |
|------|------|------|
| 缓存命中 | v4.0 | 子查询重复时 < 100ms |
| 并行子查询 | v4.2 P2 | 延迟减半 |
| Planner 结果缓存 | v4.0 | 相同查询 0 LLM 调用 |
| Aggregator 结果缓存 | v4.1 | 相同结果集 0 LLM 调用 |

---

## 8. 降级与容错策略

### 8.1 降级链

```
/search --deep --aggregate query
   ↓
Planner 调用成功？
   ├─ 是 → 继续执行子查询
   └─ 否 → 降级为 /search query（普通 v3 流程）
   ↓
子查询全部成功？
   ├─ 是 → 继续聚合
   └─ 否 → 跳过失败子查询，用成功的结果继续
   ↓
Aggregator 调用成功？
   ├─ 是 → 输出结构化答案
   └─ 否 → 降级为原始结果列表输出
   ↓
输出
```

### 8.2 容错规则

| 故障 | 处理 |
|------|------|
| Planner API 超时（30s） | 降级到普通 /search |
| Planner JSON 解析失败 | 降级到普通 /search |
| 子查询数 > 5 | 截断到前 5 个 |
| 子查询数 = 0 | 降级到普通 /search |
| 单个子查询失败 | 跳过，继续其他 |
| 所有子查询失败 | 提示用户重试 |
| Aggregator API 超时（30s） | 输出原始结果列表 |
| Aggregator 输出为空 | 输出原始结果列表 |

### 8.3 日志记录

所有降级事件都记录到日志：

```json
{
  "query": "...",
  "deep_search": true,
  "aggregated": false,
  "degradation": "aggregator_timeout",
  "sub_queries_planned": 4,
  "sub_queries_succeeded": 3,
  "sub_queries_failed": 1
}
```

---

## 9. 与其他任务的协同

### 9.1 v4.0 协同

| 任务 | 协同方式 |
|------|---------|
| T1 PII 脱敏 | Planner 输出的子查询也走 PII 脱敏 |
| T2 Flash 评分 | 每个子查询的结果独立评分 |
| T3 时效性过滤 | 每个子查询的结果独立过滤 |
| T4 自建 SearXNG | 子查询调用 SearXNG 时用本地实例 |

### 9.2 v4.1 协同

| 任务 | 协同方式 |
|------|---------|
| T1 语义检索 | 历史中的深度查询可被语义检索 |
| T2 自动 fetch 摘要 | Aggregator 输入可包含 fetch 摘要 |
| T3 memory MCP | 聚合答案可写入 memory |
| T4 自进化接口 | 导出数据包含 deep_search 字段 |

### 9.3 v4.2 协同

| 任务 | 协同方式 |
|------|---------|
| T1 并行三层调用 | 子查询并行执行（核心性能提升） |
| T2 流式输出 | 每个子查询完成即输出 |
| T3 缓存预热 | 预热高频深度查询 |

---

## 10. 对比 MindSearch 完整部署

### 10.1 功能对比

| 功能 | 方案 D | 方案 A（完整部署） |
|------|--------|------------------|
| 查询分解 | ✅ DeepSeek Flash | ✅ InternLM2.5 |
| 并行执行 | ⚠️ v4.0 串行，v4.2 并行 | ✅ 原生 async |
| LLM 聚合 | ✅ DeepSeek Flash | ✅ InternLM2.5 |
| 缓存 | ✅ 24h TTL | ❌ |
| PII 脱敏 | ✅ 4 模式正则 | ❌ |
| 历史日志 | ✅ 按日切分 | ❌ |
| 位置感知 | ✅ China/Malaysia | ❌ |
| 健康检查 | ✅ 8 子命令 | ❌ |
| Bypass 通道 | ✅ context7/github | ❌ |
| 前端 UI | ❌（用 OpenCode TUI） | ✅ React/Gradio/Streamlit |

### 10.2 非功能对比

| 维度 | 方案 D | 方案 A |
|------|--------|--------|
| 依赖数量 | 0 | 多（FastAPI+Lagent+前端） |
| 部署时间 | < 1 小时 | 2-4 小时 |
| 维护成本 | 低 | 中-高 |
| 磁盘占用 | ~10KB（2 个 prompt） | ~500MB（完整 MindSearch） |
| 内存占用 | 0（无独立服务） | ~500MB（FastAPI + Lagent） |
| 端口占用 | 0 | 1（8002） |
| 与 v3 兼容 | 极好 | 差 |
| 可逆性 | 极好（删除文件） | 差（需卸载服务） |

### 10.3 结论

方案 D 在功能上获得 MindSearch 80% 核心价值（查询分解 + 结果聚合），在非功能维度全面优于完整部署。仅在前端 UI 和原生并行上略逊，但：
- 前端 UI 不需要（用 OpenCode TUI）
- 并行能力在 v4.2 P2 补齐

**方案 D 是最优选择。**

---

## 11. 未来演进

### 11.1 v5.0 智能决策

未来 Agent 可自动判断是否需要 `--deep`：

```
用户查询 → Agent 分析查询复杂度
   ├─ 简单查询 → 普通 /search
   ├─ 中等查询 → /search --deep
   └─ 复杂查询 → /search --deep --aggregate
```

### 11.2 v5.1 多模型 Planner

支持不同模型做 Planner：

| 模型 | 场景 |
|------|------|
| DeepSeek V4 Flash | 默认（成本低） |
| DeepSeek V4 Pro | 高质量需求 |
| InternLM2.5 | 中文优化 |
| Qwen | 阿里云生态 |

### 11.3 v6.0 与 MindSearch 深度融合

如果未来需要 MindSearch 的完整能力（如 Web Planner 的树状依赖、Searcher 的多轮迭代），可评估升级到方案 C（MCP 封装）。

---

## 12. 参考资料

- [MindSearch GitHub](https://github.com/InternLM/MindSearch)
- [MindSearch 论文 (arXiv:2407.20183)](https://arxiv.org/abs/2407.20183)
- [Lagent 框架](https://github.com/InternLM/lagent)
- [MindSearch 源码解析](https://docs.feishu.cn/article/wiki/TKhRwElU2irk2zkJhWycO8JVnlf)

---

**End of MindSearch Integration Design (Plan D)**
