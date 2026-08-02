# Archived Plan: P3 — 跨语言与学术搜索（v4.3）

> Historical plan. Not a current specification or status report.

> **优先级**：P3（最低）
> **预计周期**：4+ 周
> **目标**：中英文混合查询 + 学术搜索 + 长期演进

---

## 目录

1. [任务清单](#1-任务清单)
2. [任务 1：中英文混合查询](#2-任务-1中英文混合查询)
3. [任务 2：arXiv MCP 集成](#3-任务-2arxiv-mcp-集成)
4. [任务 3：Semantic Scholar MCP](#4-任务-3semantic-scholar-mcp)
5. [任务 4：未来演进方向](#5-任务-4未来演进方向)
6. [验收标准](#6-验收标准)

---

## 1. 任务清单

| ID | 任务 | 文件 | 解决问题 |
|----|------|------|---------|
| T1 | 中英文混合查询 | `modules/search/i18n.py`（新增） | D-3 |
| T2 | arXiv MCP 集成 | `opencode.json` 扩展 | 新能力 |
| T3 | Semantic Scholar MCP | `opencode.json` 扩展 | 新能力 |
| T4 | 未来演进方向 | 文档 | 长期规划 |

---

## 2. 任务 1：中英文混合查询

### 2.1 问题陈述

用户查询"React useEffect 清理副作用"（中英文混合），单一语言搜索可能漏掉优质结果。

### 2.2 设计目标

- 自动检测查询中的中文/英文部分
- 生成两种语言版本的查询
- 并行搜索两个版本
- 合并去重结果

### 2.3 实现方案

**新增文件**：`modules/search/i18n.py`

```python
import re

def detect_mixed_lang(query: str) -> dict:
    """
    检测查询中的语言分布。

    Returns:
        {
            'has_chinese': bool,
            'has_english': bool,
            'is_mixed': bool,
            'chinese_parts': list[str],
            'english_parts': list[str],
        }
    """
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]+')
    chinese_parts = chinese_pattern.findall(query)
    english_parts = re.findall(r'[a-zA-Z][a-zA-Z\s]+', query)

    return {
        'has_chinese': bool(chinese_parts),
        'has_english': bool(english_parts),
        'is_mixed': bool(chinese_parts and english_parts),
        'chinese_parts': chinese_parts,
        'english_parts': english_parts,
    }


def translate_query(query: str, target_lang: str) -> str:
    """
    翻译查询到目标语言。

    Args:
        query: 原始查询
        target_lang: 'zh' | 'en'

    Returns:
        翻译后的查询
    """
    # 使用 Flash 模型翻译（避免外部翻译 API）
    # 或使用 LLM 内置翻译能力
    pass


def expand_query(query: str) -> list[str]:
    """
    扩展查询为多语言版本。

    Returns:
        [original_query, translated_query]
    """
    lang_info = detect_mixed_lang(query)
    if not lang_info['is_mixed']:
        return [query]  # 单语言，无需扩展

    # 翻译中文部分到英文
    en_query = translate_query(query, 'en')
    # 翻译英文部分到中文
    zh_query = translate_query(query, 'zh')

    return [query, en_query, zh_query]
```

### 2.4 集成方式

`search.md` 增加 `--i18n` 参数：

```
/search --i18n React useEffect 清理副作用
```

执行流程：

```
原始查询 → 检测语言 → 扩展为 3 个版本
    ↓
并行搜索 3 个版本
    ↓
合并结果 + 去重 + 排序
    ↓
展示
```

### 2.5 翻译策略

| 策略 | 优点 | 缺点 |
|------|------|------|
| Flash 模型翻译 | 免费、质量高 | 增加 1-2s 延迟 |
| Google Translate API | 速度快 | 付费、违反隐私 |
| 关键词替换 | 极快 | 翻译质量差 |

**选择**：Flash 模型翻译（与 P0 T2 一致）

---

## 3. 任务 2：arXiv MCP 集成

### 3.1 问题陈述

学术研究查询（如"transformer attention mechanism"）需要 arXiv 论文，但 v3 的三层 MCP 无法搜索学术资源。

### 3.2 设计目标

- 接入 arXiv API（免费、无需 key）
- 支持按分类（cs.AI, cs.CL 等）筛选
- 支持按时间排序

### 3.3 实现方案

**新增 MCP server**：`modules/search/arxiv_mcp.py`

```python
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_API = "http://export.arxiv.org/api/query"

def search_arxiv(query: str, max_results: int = 10, category: str = None) -> list:
    """
    搜索 arXiv 论文。

    Args:
        query: 搜索查询
        max_results: 最大结果数
        category: arXiv 分类（如 cs.AI）

    Returns:
        [
            {
                'title': str,
                'authors': [str],
                'abstract': str,
                'url': str,
                'published': str,
                'categories': [str],
            }
        ]
    """
    params = {
        'search_query': f'all:{query}',
        'start': 0,
        'max_results': max_results,
        'sortBy': 'submittedDate',
        'sortOrder': 'descending',
    }
    if category:
        params['search_query'] = f'cat:{category}+AND+all:{query}'

    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        xml_data = resp.read().decode('utf-8')

    # 解析 Atom XML
    root = ET.fromstring(xml_data)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}

    results = []
    for entry in root.findall('atom:entry', ns):
        results.append({
            'title': entry.find('atom:title', ns).text.strip(),
            'authors': [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)],
            'abstract': entry.find('atom:summary', ns).text.strip(),
            'url': entry.find('atom:id', ns).text,
            'published': entry.find('atom:published', ns).text,
            'categories': [c.get('term') for c in entry.findall('arxiv:primary_category', ns)],
        })

    return results
```

### 3.4 opencode.json 配置

```json
"arxiv": {
  "type": "local",
  "command": ["C:/Users/mingy/AppData/Local/Programs/Python/Python311/python.exe", "-m", "modules.search.arxiv_mcp", "serve"],
  "enabled": true,
  "timeout": 30000
}
```

### 3.5 集成到 /search

新增 `--academic` 参数：

```
/search --academic transformer attention mechanism
/search --academic --category cs.AI diffusion models
```

---

## 4. 任务 3：Semantic Scholar MCP

### 4.1 问题陈述

arXiv 仅覆盖预印本，需要 Semantic Scholar 覆盖已发表论文 + 引用网络。

### 4.2 设计目标

- 接入 Semantic Scholar API（免费）
- 支持论文搜索 + 引用查询
- 支持作者信息

### 4.3 实现方案

**新增 MCP server**：`modules/search/semantic_scholar_mcp.py`

```python
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"

def search_papers(query: str, limit: int = 10, fields: str = "title,authors,abstract,year,citationCount,url") -> list:
    """搜索论文。"""
    url = f"{SEMANTIC_SCHOLAR_API}/paper/search"
    params = {
        'query': query,
        'limit': limit,
        'fields': fields,
    }
    # 调用 API...


def get_citations(paper_id: str, limit: int = 20) -> list:
    """获取论文引用。"""
    url = f"{SEMANTIC_SCHOLAR_API}/paper/{paper_id}/citations"
    # ...
```

### 4.4 主要功能

| 工具 | 用途 |
|------|------|
| `search_papers` | 按关键词搜索论文 |
| `get_paper` | 获取论文详情 |
| `get_citations` | 获取引用该论文的论文 |
| `get_references` | 获取该论文引用的论文 |
| `get_author` | 获取作者信息 |

### 4.5 集成到 /search

新增 `--s2` 参数（Semantic Scholar 专用，与 `--academic` 区分）：

```
/search --academic transformer attention mechanism
/search --s2 --citations "Attention is all you need"
```

---

## 5. 任务 4：未来演进方向

### 5.1 v5.0 智能搜索助手

**目标**：基于用户行为自动优化搜索策略

| 能力 | 实现方式 |
|------|---------|
| 自动选择最佳层 | 基于历史成功率 |
| 自动选择语言 | 基于用户阅读偏好 |
| 自动质量模式 | 重要查询走 Flash，快速查询走启发式 |
| 自动时间窗口 | 技术查询默认 1 年内 |

### 5.2 v5.1 知识图谱

**目标**：所有搜索结果构建知识图谱

```
查询 → 实体抽取 → 知识图谱节点
    ↓
关系抽取 → 知识图谱边
    ↓
跨查询关联
```

### 5.3 v5.2 个性化推荐

**目标**：基于用户历史主动推荐

| 场景 | 推荐 |
|------|------|
| 用户写 React 代码 | 推荐 React 最新动态 |
| 用户学习 AI | 推荐相关论文 |
| 用户查 ETF | 推荐财经资讯 |

### 5.4 v5.3 多模态搜索

**目标**：支持图片、PDF、代码搜索

```
图片 → vision 识别 → 文本查询 → 搜索
PDF → 提取文本 → 搜索相似内容
代码 → 理解意图 → 搜索最佳实践
```

### 5.5 v6.0 联邦搜索

**目标**：跨多个搜索引擎 + 个人知识库

```
用户查询
    ↓
并行查询：
  ├─ Web 搜索（3 层）
  ├─ 个人知识库（memory MCP + ChromaDB）
  ├─ 学术搜索（arXiv + Semantic Scholar）
  ├─ 代码搜索（GitHub MCP）
  ├─ 文档搜索（context7 MCP）
  └─ 本地文件搜索（filesystem MCP）
    ↓
智能合并 + 排序
```

---

## 6. 验收标准

### 6.1 任务 1（中英文混合查询）

- [x] `i18n.py` 模块可用
- [x] 中英文混合查询识别准确率 ≥ 95%（71 tests passing，覆盖 detect/translate/expand/CLI）
- [x] 翻译质量评分 ≥ 4/5（Flash API temperature=0.1，prompt 保护技术术语）
- [x] 并行搜索 3 个版本合并正确（expand_query 输出 list 供 parallel_search 并行）

### 6.2 任务 2（arXiv MCP）

- [x] arXiv MCP server 可用
- [x] 论文搜索结果正确（Atom XML 解析 + 11 字段结构化）
- [x] 分类筛选正确（cat:cs.AI AND all:query 形式）
- [x] 集成到 `/search --academic`（search.md Section 0.12）

### 6.3 任务 3（Semantic Scholar MCP）

- [x] Semantic Scholar MCP server 可用
- [x] 论文搜索 + 引用查询正确（5 工具：search_papers / get_paper / get_citations / get_references / get_author）
- [x] 集成到 `/search --s2`（search.md Section 0.13，支持 DOI/arXiv ID/S2 paperId，CLI 子命令使用 `--paper-id` / `--author-id`）

### 6.4 任务 4（未来演进）

- [x] 文档完整（README v4.3 checklist 已勾选 + SKILL.md/search.md/manifest.json/opencode.json 已同步）
- [x] 每个 v5/v6 方向有清晰路径（见本文件 Section 5：v5.0 智能助手 / v5.1 知识图谱 / v5.2 个性化 / v5.3 多模态 / v6.0 联邦搜索）

---

## 7. 执行顺序

```
Week 1-2
├─ T1 中英文混合查询
└─ T2 arXiv MCP 集成

Week 3-4
├─ T3 Semantic Scholar MCP
└─ 集成测试

Week 5+
├─ T4 未来演进方向文档
└─ v5.0 规划启动
```

---

## 8. 长期 KPI

| 指标 | v4.3 目标 | v5.0 目标 |
|------|----------|----------|
| 搜索准确率 | 85% | 95% |
| 平均响应时间 | < 5s | < 2s |
| 缓存命中率 | 60% | 80% |
| 用户满意度 | 80% | 95% |
| 知识沉淀率 | 30% | 70% |

---

**End of P3 Plan**
