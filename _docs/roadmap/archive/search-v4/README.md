# Archived Overview: Search 能力 v4 升级规划总览

> Historical overview. Use `modules/search/manifest.json` and `_docs/ARCHITECTURE.md` for current state.

> **版本基线**：v3（2026-07-19 完成）
> **目标版本**：v4（2026-Q3 ~ Q4 完成）
> **规划日期**：2026-07-19
> **所有者**：韩铭洋（Mark）
> **文档定位**：高质量紧迫规划，分阶段执行

---

## 目录

1. [规划背景](#1-规划背景)
2. [v3 残留问题清单](#2-v3-残留问题清单)
3. [升级路线图](#3-升级路线图)
4. [优先级矩阵](#4-优先级矩阵)
5. [阶段交付物](#5-阶段交付物)
6. [文档索引](#6-文档索引)
7. [关键决策](#7-关键决策)
8. [风险与缓解](#8-风险与缓解)

---

## 1. 规划背景

### 1.1 当前状态（v3 已完成）

| 能力 | 状态 |
|------|------|
| 三层搜索编排（DuckDuckGo / SearXNG / Google） | ✅ |
| 缓存 24h TTL | ✅ |
| URL 跨层去重 | ✅ |
| PII 日志脱敏（4 模式） | ✅ |
| 按日切分日志 | ✅ |
| 8 个子命令 | ✅ |
| 位置感知（China / Malaysia） | ✅ |
| Bypass 通道（context7 / github） | ✅ |
| MCP 健康检查 | ✅ |
| 收藏标记 | ✅ |

### 1.2 残留的核心瓶颈

1. **隐私出境风险**：MCP 调用时查询原文发送给 SearXNG / Google，PII 未脱敏
2. **质量评估主观**：评分靠 agent 启发式，无客观标准
3. **无语义检索**：历史日志只能关键词匹配，无法语义查询
4. **依赖第三方实例**：SearXNG 用 `searx.be` 公共实例，无 SLA
5. **Google ToS 风险**：g-search 用 Playwright 抓取，IP 易被封
6. **无并行能力**：三层 MCP 仍串行，多层场景延迟叠加
7. **无实时反馈**：搜索中断或慢时无中间状态
8. **无结果摘要**：agent 需二次调用 fetch 才能看内容

### 1.3 设计原则

延续 v3 的轻量化理念：

- **核心功能不简化**：每项升级都增强而非替代
- **单文件优先**：Python 模块继续聚合，避免文件膨胀
- **零外部依赖**：仅用标准库 + 必要时引入少量第三方
- **隐私优先**：所有出境数据必须可脱敏
- **异常兜底**：所有新功能 try/except，绝不阻塞主流程
- **可观测**：所有升级都通过 stats 命令可观测

---

## 2. v3 残留问题清单

按问题类型分类：

### A. 隐私与安全（3 项）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| A-1 | MCP 调用时 PII 未脱敏 | 高 | 所有用户查询 |
| A-2 | SearXNG 公共实例可见查询内容 | 中 | SearXNG 层 |
| A-3 | Google Playwright 抓取违反 ToS | 中 | g-search 层 |

### B. 质量与精度（4 项）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| B-1 | 评分靠 agent 主观判断 | 中 | 所有搜索 |
| B-2 | 无结果来源权威性自动判断 | 中 | 技术查询 |
| B-3 | 无时效性过滤（结果可能过时） | 中 | 技术文档 |
| B-4 | 无语言匹配（中文查询可能返回英文结果） | 低 | 跨语言场景 |

### C. 性能与并发（3 项）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| C-1 | 三层 MCP 串行调用 | 中 | 多层场景 |
| C-2 | 无流式结果输出 | 低 | 长查询等待 |
| C-3 | 缓存无预加载（冷启动慢） | 低 | 首次启动 |

### D. 检索能力（3 项）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| D-1 | 历史日志仅关键词匹配 | 中 | 历史复用 |
| D-2 | 无相似查询推荐 | 低 | 主动学习 |
| D-3 | 无跨语言查询（中英文混合） | 低 | 多语言场景 |

### E. 集成与扩展（3 项）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| E-1 | 搜索结果不写入 memory MCP | 中 | 知识沉淀 |
| E-2 | 无自动 fetch Top URL 摘要 | 低 | 决策成本 |
| E-3 | 无与 ROADMAP v3.0 自进化的接口 | 中 | 长期演进 |

### F. 可靠性（2 项）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| F-1 | 公共 SearXNG 实例限流无降级 | 中 | 中国大陆主路径 |
| F-2 | Google Playwright 封 IP 无备用 | 中 | 马来西亚高质量路径 |

### G. 查询智能（3 项，新增 — 来自 MindSearch 对比）

| ID | 问题 | 严重度 | 影响范围 |
|----|------|--------|---------|
| G-1 | 复杂查询不分解，单次 MCP 调用信息覆盖不全 | 中 | 研究类查询 |
| G-2 | 无子查询并行执行，深度查询串行慢 | 中 | 多子查询场景 |
| G-3 | 搜索结果仅 URL 列表，无 LLM 智能聚合摘要 | 中 | 所有深度查询 |

**合计：21 项残留问题**

---

## 3. 升级路线图

### v4.0 — 隐私与质量基线（P0，最紧迫）

**目标**：封堵隐私漏洞 + 引入客观质量评估 + MindSearch Planner 思想集成

| 任务 | 文件 | 解决 |
|------|------|------|
| 出境 PII 脱敏层 | `modules/search/privacy.py`（新增） | A-1 |
| Flash 模型自动评分 | `modules/search/quality.py`（新增） | B-1, B-2 |
| 时效性 + 语言过滤 | `modules/search/search.py` 扩展 | B-3, B-4 |
| 自建 SearXNG 实例指南 | `upgrade_plan/SELF_HOSTED_SEARXNG.md`（新增） | A-2, F-1 |
| **MindSearch Planner 集成** | `.opencode/prompts/web-planner.md`（新增）+ `search.md` 扩展 | **G-1, G-2** |

### v4.1 — 检索与集成（P1）

**目标**：语义检索 + 与记忆系统集成 + MindSearch Aggregator 集成

| 任务 | 文件 | 解决 |
|------|------|------|
| 语义检索历史（ChromaDB） | `modules/search/semantic.py`（新增） | D-1, D-2 |
| 自动 fetch Top URL 摘要 | `modules/search/summarize.py`（新增） | E-2 |
| 搜索结果写入 memory MCP | `.opencode/commands/search.md` 扩展 | E-1 |
| v3.0 自进化数据接口 | `modules/search/export.py`（新增） | E-3 |
| **MindSearch Aggregator 集成** | `.opencode/prompts/result-aggregator.md`（新增） | **G-3** |

### v4.2 — 性能与可靠性（P2）

**目标**：并行化 + 自建基础设施

| 任务 | 文件 | 解决 |
|------|------|------|
| 并行三层调用 | `modules/search/parallel.py`（新增） | C-1 |
| 流式结果输出 | `modules/search/stream.py`（新增） | C-2 |
| 缓存预热（启动时加载高频查询） | `modules/search/prewarm.py`（新增） | C-3 |
| Google API 合规替代（Serper） | `markconfig/secrets.json` 扩展 | A-3, F-2 |

### v4.3 — 跨语言与未来（P3）

**目标**：多语言 + 学术搜索

| 任务 | 文件 | 解决 |
|------|------|------|
| 中英文混合查询 | `modules/search/i18n.py`（新增） | D-3 |
| 学术搜索 MCP（arXiv） | `opencode.json` 扩展 | 新能力 |
| Semantic Scholar MCP | `opencode.json` 扩展 | 新能力 |

---

## 4. 优先级矩阵

| 优先级 | 版本 | 任务 | 价值 | 难度 | ROI |
|--------|------|------|------|------|-----|
| P0 | v4.0 | 出境 PII 脱敏 | 隐私保护（核心） | 低 | 极高 |
| P0 | v4.0 | Flash 模型评分 | 质量客观化 | 中 | 高 |
| P0 | v4.0 | 时效性 + 语言过滤 | 技术查询精度 | 低 | 高 |
| P0 | v4.0 | MindSearch Planner 集成 | 复杂查询分解能力 | 低 | 高 |
| P1 | v4.0 | 自建 SearXNG 指南 | 摆脱第三方依赖 | 中 | 中 |
| P1 | v4.1 | 语义检索 | 历史复用率提升 | 中 | 高 |
| P1 | v4.1 | 自动 fetch 摘要 | 决策成本降低 | 低 | 高 |
| P1 | v4.1 | MindSearch Aggregator 集成 | LLM 智能聚合 | 低 | 高 |
| P2 | v4.1 | memory MCP 集成 | 知识沉淀 | 低 | 中 |
| P2 | v4.2 | 并行三层调用 | 多层场景延迟减半 | 中 | 中 |
| P2 | v4.2 | 缓存预热 | 冷启动优化 | 低 | 中 |
| P3 | v4.2 | Serper API 替代 g-search | 合规 + 稳定 | 低 | 中 |
| P3 | v4.3 | 中英文混合查询 | 多语言 | 中 | 低 |
| P3 | v4.3 | 学术搜索 MCP | 新能力 | 中 | 中 |

---

## 5. 阶段交付物

### v4.0（P0，预计 1-2 周）

- [x] `modules/search/privacy.py` — 出境 PII 脱敏（7 patterns + Layer 0/3 双层防护）
- [x] `modules/search/quality.py` — Flash 模型自动评分（双轨 heuristic + flash，7 天缓存）
- [x] `modules/search/search.py` 扩展 `--recent` `--lang` 参数（filter 子命令）
- [x] `upgrade_plan/SELF_HOSTED_SEARXNG.md` — Docker 部署指南
- [x] `.opencode/prompts/web-planner.md` — MindSearch Planner prompt 模板
- [x] `.opencode/commands/search.md` 扩展 `--deep` 参数（Section 0.5）
- [x] 单元测试覆盖（privacy 67 tests + quality 80 tests + planner 51 tests + filter 33 tests）

### v4.1（P1，预计 2-3 周）

- [x] `modules/search/semantic.py` — ChromaDB 向量检索（fallback 到 keyword 搜索）
- [x] `modules/search/summarize.py` — 自动 fetch + 摘要（3 URL 并发 + Flash）
- [x] `modules/search/export.py` — v3.0 自进化数据接口（JSON/CSV，UTF-8 BOM）
- [x] `.opencode/prompts/result-aggregator.md` — MindSearch Aggregator prompt
- [x] `/search` 命令集成新模块（`--remember` / `--aggregate` flags）
- [x] SKILL.md 文档更新（v4.1 sections: semantic / summarize / memory / export / aggregator）

### v4.2（P2，预计 3-4 周）

- [x] `modules/search/parallel.py` — 并行三层调用（ThreadPoolExecutor + first_completed/all）
- [x] `modules/search/stream.py` — 流式输出（generator + JSON Lines 事件流）
- [x] `modules/search/prewarm.py` — 缓存预热（Top N 高频查询 + 原子写入）
- [x] Serper API 接入（替代 g-search，ToS 合规，错误 body API key 自动脱敏）
- [x] 性能基准测试（59 + 46 + 51 + 73 = 229 P2 tests passing）

### v4.3（P3，预计 4+ 周）

- [x] `modules/search/i18n.py` — 跨语言查询（71 tests passing）
- [x] `modules/search/arxiv_mcp.py` — arXiv MCP 集成（66 tests passing）
- [x] `modules/search/semantic_scholar_mcp.py` — Semantic Scholar MCP 集成（86 tests passing）
- [x] 文档完整更新（SKILL.md / search.md / manifest.json / opencode.json）

---

## 6. 文档索引

| 文档 | 内容 | 受众 |
|------|------|------|
| [README.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/README.md) | 本文件 — 总览 | 所有人 |
| [ACCEPTANCE.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/ACCEPTANCE.md) | **最终验收报告**（v4.3 发布签字） | 所有人 |
| [P0-PRIVACY_AND_QUALITY.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/P0-PRIVACY_AND_QUALITY.md) | v4.0 详细执行方案（含 MindSearch Planner） | 开发者 |
| [P1-SEMANTIC_AND_INTEGRATION.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/P1-SEMANTIC_AND_INTEGRATION.md) | v4.1 详细执行方案（含 MindSearch Aggregator） | 开发者 |
| [P2-PERFORMANCE_AND_RELIABILITY.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/P2-PERFORMANCE_AND_RELIABILITY.md) | v4.2 详细执行方案 | 开发者 |
| [P3-I18N_AND_ACADEMIC.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/P3-I18N_AND_ACADEMIC.md) | v4.3 详细执行方案 + 未来演进（v5/v6） | 开发者 |
| [SELF_HOSTED_SEARXNG.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/SELF_HOSTED_SEARXNG.md) | 自建 SearXNG 部署指南（可选未来任务） | 运维 |
| [MINDSEARCH_INTEGRATION.md](file:///e:/system_folder/.claude/.claude/upgrade_plan/MINDSEARCH_INTEGRATION.md) | MindSearch 方案 D 集成设计技术参考 | 开发者 |

---

## 7. 关键决策

### 决策 1：出境 PII 脱敏 vs 完全禁用搜索 PII

**选择**：出境前正则脱敏 + 文档警告

**原因**：
- 完全禁用太严格，影响搜索体验
- 正则脱敏覆盖 95% 场景（邮箱/手机/身份证）
- SKILL.md 明确警告剩余 5% 风险

### 决策 2：自建 SearXNG vs 继续用公共实例

**选择**：自建 Docker 实例（v4.0 P1）

**原因**：
- 公共实例 `searx.be` 无 SLA
- 中国大陆访问不稳定
- 自建实例查询完全本地化，隐私提升
- Docker 部署成本 < 1 小时

### 决策 3：Flash 模型评分 vs 启发式评分

**选择**：双轨并行（v4.0 P0）

**原因**：
- Flash 评分客观但成本高
- 启发式免费但主观
- 重要查询走 Flash，快速查询走启发式
- `--quality-mode flash` 参数控制

### 决策 4：ChromaDB vs FAISS

**选择**：ChromaDB（v4.1 P1）

**原因**：
- ChromaDB 原生支持持久化
- API 更友好
- 集成成本低
- 社区活跃

### 决策 5：并行调用 vs 串行优化

**选择**：并行（v4.2 P2）

**原因**：
- 三层 MCP 独立无依赖
- 并行后多层场景延迟减半
- oh-my-opencode plugin 支持异步 agent

### 决策 6：MindSearch 集成方式（新增）

**选择**：方案 D — 选择性集成核心思想（Planner + Aggregator）

**原因**：
- 完整部署（方案 A）违背轻量化原则，引入 FastAPI + Lagent + 前端依赖
- MCP 封装（方案 C）依赖 MindSearch 完整代码，维护成本高
- Subagent 重实现（方案 B）OpenCode 无原生并行，收益受限
- 方案 D 仅用 2 个 prompt 文件 + 命令扩展，保留 v3 全部优势
- Planner 用 DeepSeek V4 Flash，成本极低（单次 ~$0.001）
- 可逆性好：删除 prompt 文件即可回退

**对比**：

| 方案 | 复杂度 | 依赖 | 与 v3 兼容 | 推荐度 |
|------|--------|------|-----------|--------|
| A 完整部署 | 高 | 多 | 差 | ⭐⭐ |
| B Subagent | 中 | 零 | 好 | ⭐⭐⭐⭐ |
| C MCP 封装 | 高 | 多 | 中 | ⭐⭐⭐ |
| **D 选择性集成** | **低** | **零** | **极好** | **⭐⭐⭐⭐⭐** |

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ChromaDB 集成复杂 | 中 | 中 | 先做 FAISS PoC 验证 |
| Flash 评分成本高 | 中 | 中 | 仅 `--quality-mode flash` 触发 |
| 自建 SearXNG 维护成本 | 低 | 中 | Docker + 自动更新 |
| Serper API 付费 | 中 | 低 | 免费额度 2500/月，够用 |
| 并行调用引入竞态 | 中 | 高 | 用 asyncio.Lock 保护缓存写入 |
| 出境 PII 正则遗漏 | 中 | 高 | 双层防护（正则 + 黑名单） |
| MindSearch Planner 拆解过度 | 中 | 低 | 限制最多 5 个子查询 |
| Planner prompt 在 DeepSeek 上效果不佳 | 低 | 中 | 双轨 `--planner-mode flash\|pro` |
| 子查询串行延迟叠加 | 中 | 中 | 与 v4.2 P2 并行能力结合 |

---

## 9. 成功指标

### v4.0 KPI

- PII 出境脱敏覆盖率 ≥ 95%
- Flash 评分准确率 ≥ 80%（与人工标注对比）
- 时效性过滤准确率 ≥ 90%
- Planner 查询分解准确率 ≥ 85%
- `--deep` 模式响应时间 < 40s（含 Planner + 5 子查询）

### v4.1 KPI

- 历史复用率提升 ≥ 30%（语义检索后）
- 自动 fetch 摘要准确率 ≥ 85%
- memory MCP 集成完成
- Aggregator 聚合质量评分 ≥ 4/5（人工评估）

### v4.2 KPI

- 多层场景平均延迟降低 ≥ 40%（并行后）
- 缓存命中率 ≥ 60%（预热后）
- Google 封 IP 概率降低 ≥ 80%（Serper 替代后）

### v4.3 KPI

- 跨语言查询准确率 ≥ 85%
- 学术搜索可用率 ≥ 95%
- 文档完整度 100%

---

**End of Search Upgrade Plan Overview**
