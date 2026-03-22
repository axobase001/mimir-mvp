# Skuld Beta — 三个 Persona 实例部署指南

> 分支：`beta/personas`
> 最后更新：2026-03-22

## 概述

三个独立的 Skuld 实例，分别面向三种用户画像，部署在阿里云 ECS（4GB RAM + 100GB 存储）。每个实例运行在独立 Docker 容器中，互相隔离，共享一个 SearXNG 搜索代理。

**与生产实例（Aldebaran/Antares）完全隔离** — 代码在 `beta/personas` 分支，部署在不同的 ECS，不共享任何数据。

## 三个实例

| 代号 | Persona | 端口 | 容器名 | Cycle 间隔 |
|------|---------|------|--------|-----------|
| skuld-crypto | 重度赌狗 / 炒币交易员 | 8010 | skuld-beta-crypto | 2分钟 |
| skuld-founder | AI 初创公司 Founder | 8011 | skuld-beta-founder | 5分钟 |
| skuld-phd | AI 视频理解方向 PhD | 8012 | skuld-beta-phd | 5分钟 |

## Skill 分配

### 通用（所有实例都有）
web_search, web_fetch, file_read, file_write, document, data_analysis, translate, summarize_url, json_query, tool_forge

### 赌狗专属（5个）
| Skill | 功能 | API | 需要 Key |
|-------|------|-----|---------|
| crypto_price | BTC/ETH 等实时价格、趋势币 | CoinGecko | 否（免费） |
| price_alert | 设置价格阈值告警 | CoinGecko | 否 |
| onchain_data | 链上余额、交易记录、Gas 费 | Etherscan | 是 |
| sentiment_scan | 社交媒体情绪 + Fear & Greed Index | alternative.me + SearXNG | 否 |
| portfolio_track | 持仓管理、盈亏计算 | CoinGecko | 否 |

### Founder 专属（4个）
| Skill | 功能 | API | 需要 Key |
|-------|------|-----|---------|
| daily_brief | 按主题生成每日简报 | SearXNG | 否 |
| competitor_watch | 监控竞品网站变化 | 直接 HTTP | 否 |
| rss_monitor | RSS/Atom 订阅追踪 | 直接解析 | 否 |
| meeting_prep | 会前准备（搜参会人背景） | SearXNG | 否 |

### PhD 专属（4个）
| Skill | 功能 | API | 需要 Key |
|-------|------|-----|---------|
| arxiv_tracker | arXiv 新论文追踪、关键词订阅 | arXiv API | 否（免费） |
| paper_reader | 下载 arXiv PDF 并提取/总结 | arXiv | 否 |
| citation_graph | 论文引用关系查询 | Semantic Scholar | 否（免费） |
| experiment_log | 结构化实验日志 | 本地存储 | 否 |

## 文件结构

```
mimir/
├── skills/beta/               # 13个 persona 专属 skill
│   ├── __init__.py
│   ├── crypto_price.py
│   ├── price_alert.py
│   ├── onchain_data.py
│   ├── sentiment_scan.py
│   ├── portfolio_track.py
│   ├── daily_brief.py
│   ├── competitor_watch.py
│   ├── rss_monitor.py
│   ├── meeting_prep.py
│   ├── arxiv_tracker.py
│   ├── paper_reader.py
│   ├── citation_graph.py
│   └── experiment_log.py
├── configs/                    # 3个 persona 配置
│   ├── config.beta.crypto_trader.json
│   ├── config.beta.ai_founder.json
│   └── config.beta.ai_phd.json
├── docker-compose.beta.yml     # Beta 部署 compose 文件
└── core/scheduler.py           # _register_persona_skills() 方法
```

## 部署步骤

### 1. 准备 ECS

```bash
# 阿里云 ECS: 4GB RAM, 100GB, Ubuntu 22.04
# 安装 Docker
curl -fsSL https://get.docker.com | sh
systemctl start docker && systemctl enable docker
```

### 2. 克隆代码

```bash
git clone https://github.com/axobase001/mimir-mvp.git skuld-beta
cd skuld-beta
git checkout beta/personas
```

### 3. 设置环境变量

```bash
export LLM_API_KEY="sk-your-deepseek-key"
# 赌狗用户如果提供了 Etherscan key，写入对应 config：
# vim configs/config.beta.crypto_trader.json → "etherscan_api_key": "xxx"
```

### 4. 启动

```bash
docker compose -f docker-compose.beta.yml up -d
```

### 5. 验证

```bash
# 赌狗
curl -s http://localhost:8010/api/dashboard | head -c 100
# Founder
curl -s http://localhost:8011/api/dashboard | head -c 100
# PhD
curl -s http://localhost:8012/api/dashboard | head -c 100
```

## Onboarding 流程

前端页面在 `_Projects/Skuld/beta/index.html`，部署到 `skuldbrain.com/beta/`。

用户流程：
1. 打开 beta 页面，选择 CN/EN
2. 回答 5 个问题（自然语言）
3. 后端将回答转化为种子信念（调 LLM）
4. 启动对应 persona 的容器
5. 用户跳转到自己的 Dashboard

### 5 个 Onboarding 问题
1. 你希望 Skuld 是你的工作伙伴还是生活助理？
2. 你最近在关注什么领域？
3. 你的职业或研究方向是什么？
4. 有什么你一直想搞清楚但没有结论的问题？
5. 你希望 AI 帮你做什么？

## 用户配置自定义

每个用户的 config JSON 支持以下自定义：

```json
{
  "persona": "crypto_trader",       // 决定加载哪些专属 skill
  "seed_beliefs": [...],            // 从 onboarding 回答生成
  "cycle_interval_seconds": 120,    // cycle 频率
  "confidence_decay_rate": 0.02,    // 信念衰减率（不要超过 0.03！）
  "etherscan_api_key": "",          // 用户提供的 API key
  "sandbox": true                   // beta 实例强制开启 sandbox
}
```

## 成本估算

| 项目 | 单实例/天 | 3 实例/天 |
|------|----------|----------|
| DeepSeek API（~10 calls/cycle, $0.14/M tokens） | ~$0.3-0.5 | ~$1-1.5 |
| ECS（4GB, 按量） | — | ~$1.5 |
| SearXNG | 免费 | 免费 |
| CoinGecko / arXiv / Semantic Scholar | 免费 | 免费 |
| **总计** | — | **~$2.5-3/天** |

## 已知限制

- **CoinGecko 免费 API** 限流 10-30 req/min，赌狗实例 2分钟/cycle 足够
- **Semantic Scholar** 无 key 限 100 req/5min，PhD 实例 5分钟/cycle 足够
- **arXiv API** 要求 ≤1 req/3s，arxiv_tracker 已内置延迟
- **onchain_data** 需要用户自己提供 Etherscan key，否则返回错误提示
- **paper_reader** 的 PDF 解析是基础版（regex 提取文本流），复杂排版的论文可能丢格式
- **衰减率** 已锁定 0.02，config 里可改但 README 和代码注释都警告不要超过 0.03

## 与生产实例的关系

| | 生产（Aldebaran/Antares） | Beta |
|---|---|---|
| 分支 | main | beta/personas |
| 服务器 | 本地 Docker + 阿里云新加坡 | 新阿里云 ECS |
| 数据 | 各自的 brain data | 各自的 Docker volume |
| Skills | 18 通用 + tool_forge + forged tools | 通用 + persona 专属 |
| Sandbox | 老大 Docker、老二 Podman | 全部 Docker + sandbox=true |
| 互通 | sibling_message 兄弟信箱 | 无（各自隔离） |

合并 main 的 bug 修复到 beta：
```bash
git checkout beta/personas
git merge main
# 解决冲突（主要在 scheduler.py 和 config.py）
```

---

*文档作者：Wren · 2026-03-22*
