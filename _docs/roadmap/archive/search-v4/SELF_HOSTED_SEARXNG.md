# Archived Guide: Self-Hosted SearXNG Deployment

> Historical optional deployment guide; no self-hosted instance is included or guaranteed.

> **目标**：摆脱公共 SearXNG 实例依赖，本地部署查询完全私密
> **适用场景**：v4.0 P0 任务 4
> **预计耗时**：< 1 小时

---

## 目录

1. [部署优势](#1-部署优势)
2. [前置要求](#2-前置要求)
3. [Docker Compose 部署](#3-docker-compose-部署)
4. [配置与安全](#4-配置与安全)
5. [集成到 OpenCode](#5-集成到-opencode)
6. [验证与测试](#6-验证与测试)
7. [维护与更新](#7-维护与更新)
8. [故障排查](#8-故障排查)

---

## 1. 部署优势

| 维度 | 公共实例（searx.be） | 自建实例 |
|------|-------------------|---------|
| 隐私 | 查询可见于第三方 | 完全本地，无出境 |
| SLA | 无保证 | 自控 |
| 限流 | 共享配额 | 独占 |
| 速度 | 200-500ms（公网） | 50-100ms（本地） |
| 定制 | 固定引擎列表 | 可选引擎 |
| 维护 | 无需 | 需要（极少） |

---

## 2. 前置要求

### 2.1 硬件要求

- CPU：双核以上
- 内存：1GB 空闲
- 磁盘：500MB

### 2.2 软件要求

- Docker Desktop（Windows）
- 或 Docker Engine + Docker Compose（Linux）

### 2.3 网络要求

- 本地端口 8080 可用
- 出站访问（SearXNG 后端引擎需要）

---

## 3. Docker Compose 部署

### 3.1 目录结构

```
D:\docker-services\searxng\
├─ docker-compose.yml
├─ settings.yml
├─ .env
└─ data/                # 持久化数据
```

### 3.2 docker-compose.yml

```yaml
version: '3.8'

services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"   # 仅本地访问
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml:ro
      - ./data:/etc/searxng/data
    environment:
      - SEARXNG_BASE_URL=http://localhost:8080/
      - SEARXNG_SECRET=${SEARXNG_SECRET}
    networks:
      - searxng

networks:
  searxng:
    driver: bridge
```

### 3.3 settings.yml

```yaml
use_default_settings: true

general:
  instance_name: "Mark Local SearXNG"
  debug: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json      # 必须启用，MCP 调用需要

server:
  secret_key: "${SEARXNG_SECRET}"
  bind_address: "127.0.0.1"
  port: 8080
  limiter: false   # 本地实例无需限流
  image_proxy: true

engines:
  # 启用主要引擎
  - name: google
    engine: google
    shortcut: g
    disabled: false

  - name: bing
    engine: bing
    shortcut: b
    disabled: false

  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    disabled: false

  - name: wikipedia
    engine: wikipedia
    shortcut: wp
    disabled: false

  - name: github
    engine: github
    shortcut: gh
    disabled: false

  # 禁用不必要的引擎
  - name: yahoo
    engine: yahoo
    disabled: true

  - name: baidu
    engine: baidu
    disabled: true   # 国内环境可启用

outgoing:
  request_timeout: 10
  max_request_timeout: 15
  # 代理配置（如有需要）
  # proxies:
  #   all://: 'http://proxy:8080'
```

### 3.4 .env

```bash
# 生成随机 secret key
# Linux:  openssl rand -hex 32
# Windows: 用 Python 生成
#   python -c "import secrets; print(secrets.token_hex(32))"

SEARXNG_SECRET=your_generated_secret_key_here
```

### 3.5 启动

```bash
# 进入目录
cd D:\docker-services\searxng

# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f searxng

# 验证
curl http://localhost:8080/healthz
```

---

## 4. 配置与安全

### 4.1 仅本地访问

`docker-compose.yml` 中端口映射为 `127.0.0.1:8080:8080`，确保不对外暴露。

### 4.2 JSON API 启用

SearXNG MCP 调用需要 JSON API。`settings.yml` 中：

```yaml
search:
  formats:
    - html
    - json
```

### 4.3 禁用限流

本地实例无需限流：

```yaml
server:
  limiter: false
```

### 4.4 Secret Key 保护

`.env` 文件加入 `.gitignore`：

```
.env
data/
```

### 4.5 引擎选择

根据地理位置选择引擎：

| 位置 | 推荐启用 |
|------|---------|
| 中国珠海 | google, bing, baidu, duckduckgo |
| 马来西亚 | google, bing, duckduckgo, wikipedia |

---

## 5. 集成到 OpenCode

### 5.1 修改 opencode.json

替换原 `searxng` MCP 配置：

```json
"searxng": {
  "type": "local",
  "command": ["npx", "-y", "mcp-searxng"],
  "environment": {
    "SEARXNG_URL": "http://localhost:8080"
  },
  "enabled": true,
  "timeout": 15000
}
```

### 5.2 验证 MCP 连接

```bash
# 启动 OpenCode 后测试
/search --layer 2 React useEffect best practices
```

### 5.3 更新 SKILL.md

修改 `.opencode/skills/search-orchestration/SKILL.md`：

```markdown
## Layer 2 — SearXNG Meta Search (Local Instance)

- **MCP**: `searxng` (local instance at localhost:8080)
- **Strengths**: Aggregates 70+ search engines, broader coverage
- **Privacy**: ✅ Queries sent to local instance, no third-party exposure
- **When to use**:
  - DuckDuckGo unavailable or returned low-quality results
  - Cross-source verification needed
  - User in mainland China (local instance usually accessible)
```

---

## 6. 验证与测试

### 6.1 基础功能测试

```bash
# 1. 容器健康检查
curl http://localhost:8080/healthz
# 预期: OK

# 2. HTML 搜索
curl "http://localhost:8080/search?q=React+useEffect&format=html"

# 3. JSON 搜索
curl "http://localhost:8080/search?q=React+useEffect&format=json"
```

### 6.2 性能基准

```bash
# 测量响应时间
time curl -s "http://localhost:8080/search?q=test&format=json" > /dev/null
# 预期: < 500ms
```

### 6.3 引擎可用性

```bash
# 查看启用的引擎
curl -s "http://localhost:8080/configengines" | python -m json.tool
```

### 6.4 隐私验证

```bash
# 检查访问日志（应只有本地 IP）
docker-compose logs searxng | grep "GET /search"
```

---

## 7. 维护与更新

### 7.1 自动更新

```bash
# 每月更新一次
cd D:\docker-services\searxng
docker-compose pull
docker-compose up -d
```

### 7.2 数据备份

```bash
# 备份配置和数据
tar -czf searxng-backup-$(date +%Y%m%d).tar.gz settings.yml .env data/
```

### 7.3 监控

```bash
# 健康检查脚本（加入 Windows 任务计划）
@echo off
curl -s http://localhost:8080/healthz > nul
if errorlevel 1 (
    docker-compose -f D:\docker-services\searxng\docker-compose.yml restart
)
```

### 7.4 日志清理

```bash
# 定期清理日志
docker-compose logs --tail=1000 searxng > searxng.log
docker-compose logs --tail=0 searxng
```

---

## 8. 故障排查

### 8.1 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 容器无法启动 | 端口冲突 | 检查 8080 是否占用 |
| JSON API 403 | 未启用 json 格式 | 检查 settings.yml |
| 搜索结果为空 | 引擎被 ban | 切换引擎或用代理 |
| 响应慢 | 引擎多 | 减少启用引擎数 |
| Secret key 错误 | .env 未加载 | 检查 .env 文件 |

### 8.2 调试命令

```bash
# 查看容器状态
docker ps | grep searxng

# 查看容器日志
docker-compose logs searxng

# 进入容器
docker-compose exec searxng sh

# 检查配置
docker-compose exec searxng cat /etc/searxng/settings.yml
```

### 8.3 重置

```bash
# 完全重置
docker-compose down -v
rm -rf data/
docker-compose up -d
```

---

## 9. 进阶配置（可选）

### 9.1 Redis 缓存

提升搜索速度：

```yaml
# docker-compose.yml 增加
services:
  redis:
    image: redis:alpine
    container_name: searxng-redis
    restart: unless-stopped
    volumes:
      - ./redis-data:/data

  searxng:
    depends_on:
      - redis
    environment:
      - REDIS_URL=redis://redis:6379/0
```

### 9.2 反向代理（如需远程访问）

```nginx
server {
    listen 443 ssl;
    server_name search.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 9.3 Tor 隐藏服务（极致隐私）

参考 SearXNG 官方文档：https://docs.searxng.org/admin/installation-docker.html

---

## 10. 部署后 Checklist

> **状态**：文档已完成，部署为可选未来任务。当前 v4.x 使用公共 SearXNG 实例 `searx.be`，Mark 可在任何时候按本指南自建实例。以下 checklist 在实际部署时勾选。

- [ ] Docker Compose 启动成功
- [ ] `http://localhost:8080/healthz` 返回 OK
- [ ] JSON API 可用
- [ ] 至少 3 个引擎返回结果
- [ ] 响应时间 < 500ms
- [ ] `opencode.json` 更新指向本地实例
- [ ] SKILL.md 更新 Layer 2 描述
- [ ] `/search --layer 2` 测试通过
- [ ] 防火墙仅允许 127.0.0.1 访问 8080
- [ ] .env 文件已加入 .gitignore

---

**End of Self-Hosted SearXNG Guide**
