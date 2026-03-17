<div align="center">

# Mimir

**Brain-first AI cognitive system**

*Not a chatbot. Not a copilot. A system that thinks.*

[English](#english) | [中文](#中文)

</div>

---

## English

### What is Mimir?

Mimir is a cognitive system where **the Brain is the decision-maker, and the LLM is the tool**.

Every existing AI agent framework (Manus, AutoGen, CrewAI) puts the LLM at the center and bolts memory modules around it. We invert this: the Brain owns state, makes decisions, and accumulates knowledge. The LLM is called by the Brain — sometimes to think (internal: reasoning, simulation, planning), sometimes to act (external: translating queries, extracting data). Swap the LLM for a worse model, and the Brain's accumulated knowledge survives.

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        BRAIN                            │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │  Belief   │  │   SEC    │  │   Goal   │              │
│  │  Graph    │  │  Matrix  │  │ Generator│              │
│  └──────────┘  └──────────┘  └──────────┘              │
│  ┌──────────┐  ┌──────────┐                             │
│  │Prediction│  │  Memory  │                             │
│  │ Engine   │  │(episodic │                             │
│  │          │  │+procedural)                            │
│  └──────────┘  └──────────┘                             │
│                     │                                   │
│         ┌───────────┴───────────┐                       │
│         ▼                       ▼                       │
│  ┌─────────────┐       ┌─────────────┐                  │
│  │ Internal LLM│       │ External LLM│                  │
│  │  (reason,   │       │  (translate, │                  │
│  │   simulate, │       │   extract,   │                  │
│  │   plan)     │       │   summarize) │                  │
│  └─────────────┘       └─────────────┘                  │
│                     │                                   │
│         ┌───────────┴───────────┐                       │
│         ▼                       ▼                       │
│  ┌─────────────┐       ┌─────────────┐                  │
│  │   Search    │       │  Code Exec  │  ...more skills  │
│  │   Email     │       │  Documents  │                  │
│  │   Web Fetch │       │  Data Anal. │                  │
│  └─────────────┘       └─────────────┘                  │
└─────────────────────────────────────────────────────────┘
```

### The Cognitive Cycle

Every cycle, Mimir runs a 10-phase loop:

```
WAKE → PREDICT → SELECT → OBSERVE → PE → UPDATE → REASON → GOALS → REFLECT → PRUNE
```

1. **Wake** — Load state, pick focus goal
2. **Predict** — Brain predicts each belief's confidence will hold (status quo hypothesis)
3. **Select** — SEC matrix filters which directions to observe
4. **Observe** — Search the web, extract structured data
5. **Prediction Error** — |predicted - observed| for each belief
6. **Update** — Bayesian confidence adjustment, propagate through dependency graph
7. **Reason** — Internal LLM infers new beliefs, abstracts patterns (periodic)
8. **Goals** — Generate investigation/refresh goals from PE distribution
9. **Reflect** — Summarize cycle into episodic memory
10. **Prune** — Remove dead beliefs

### Key Concepts

- **Belief Graph** — Directed graph of beliefs with confidence scores and dependency edges. Beliefs decay if unverified. Low-confidence orphans get pruned.
- **SEC Matrix** — Staleness-Error Correlation. Tracks which observation directions actually reduce prediction error. Positive C = useful direction. Negative C = waste of attention. The Brain learns where to look without being told.
- **Prediction Error as Drive Signal** — No hardcoded reward. The system is driven by surprise — the gap between what it expected and what it found.
- **Goal Generation** — Goals emerge from persistent high PE or stale high-confidence beliefs. Not programmed, not prompted — emergent.

### What's Included

| Layer | Status | Description |
|-------|--------|-------------|
| Brain core | Interface only | Belief graph, SEC matrix, prediction, goals, memory |
| LLM dual-channel | Full | Internal (reason/plan) + External (translate/extract) |
| Skill system | Full | Search, code exec, documents, email, web fetch, data analysis |
| Cycle engine | Full | 10-phase cognitive loop with SEC filtering |
| Dashboard | Full | D3 belief graph, Chart.js SEC visualization, real-time WebSocket |
| Chat | Full | Belief-grounded answers with live search fallback |
| Multi-user | Full | JWT auth, per-user Brain isolation, usage limits |
| Deployment | Full | Docker + nginx + SSL ready |

### What's NOT Included

**The SEC matrix and belief graph implementations are proprietary.**

These are the core research contributions from the [Noogenesis](https://arxiv.org/abs/2603.09476) project. The interface stubs (`brain/sec_matrix_interface.py`, `brain/belief_graph_interface.py`) define the full API — you can provide your own implementation.

The rest of the system (LLM integration, skills, server, dashboard, auth, scheduler) is fully open-sourced.

### Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Create config.json with your API keys
cat > config.json << 'EOF'
{
    "llm_api_key": "your-deepseek-or-openai-key",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-chat",
    "brave_api_key": "your-brave-search-key",
    "seed_beliefs": [
        {"statement": "AI regulation is increasing globally", "tags": ["regulation", "ai"]},
        {"statement": "Federal Reserve may cut rates in 2026", "tags": ["finance", "macro"]}
    ]
}
EOF

# Run (single-user mode)
python -m mimir.main --config config.json --port 8000

# Open http://localhost:8000
```

### Docker

```bash
export JWT_SECRET="your-secret-key"
export LLM_API_KEY="your-llm-key"
export BRAVE_API_KEY="your-brave-key"

docker-compose up -d
```

### Tech Stack

- Python 3.11+, async/await
- FastAPI + uvicorn
- D3.js + Chart.js (no build step)
- SQLite (user DB)
- DeepSeek / Claude / any OpenAI-compatible LLM
- Brave Search API

### Tests

```bash
cd Desktop && python -m pytest mimir/tests/ -v
# 155 tests passing
```

---

## 中文

### Mimir是什么？

Mimir是一个认知系统。**Brain是决策者，LLM是工具。**

现在所有AI agent框架（Manus、AutoGen、CrewAI）都把LLM当大脑，外面套记忆模块。我们反过来——Brain拥有状态、做决策、积累知识。LLM被Brain调用：有时候用来思考（对内调用：推理、模拟、规划），有时候用来做事（对外调用：翻译搜索query、提取结构化数据）。

拔掉LLM换一个更差的模型，Brain的积累不丢失。

### 核心概念

- **信念图（Belief Graph）** — 有向图，节点是信念（带置信度和依赖边）。未验证的信念会衰减，低置信度孤立信念会被剪枝。
- **SEC矩阵（Staleness-Error Correlation）** — 追踪哪些观测方向真正降低了预测误差。正C值=有用的方向，负C值=浪费注意力。Brain自己学会往哪看，不需要人告诉它。
- **预测误差驱动** — 没有硬编码的奖励函数。系统被"惊讶"驱动——预期和观测之间的差距。
- **目标内生** — 目标从持续高PE或过期高置信度信念中涌现。不是编程的，不是prompt的，是涌现的。

### 认知周期

每个周期跑一个完整的10阶段闭环：

```
唤醒 → 预测 → 选择 → 观测 → PE计算 → 更新 → 推理 → 目标 → 反思 → 剪枝
```

### 开源的和没开源的

**SEC矩阵和信念图的实现没有开源。这是核心护城河。**

这两个模块来自[Noogenesis](https://arxiv.org/abs/2603.09476)研究项目——证明注意力优先级函数可以从预测误差中内生，SEC可以在无监督条件下发现变量重要性。这不是工程问题，是科学发现。

接口定义（`brain/sec_matrix_interface.py`、`brain/belief_graph_interface.py`）完整公开，你可以自己实现。系统的其他部分（LLM集成、技能系统、服务器、Dashboard、认证、调度器）全部开源。

### 快速开始

```bash
pip install -r requirements.txt

# 配置API密钥
cat > config.json << 'EOF'
{
    "llm_api_key": "你的DeepSeek或OpenAI密钥",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-chat",
    "brave_api_key": "你的Brave Search密钥",
    "seed_beliefs": [
        {"statement": "帮我在金融市场赚钱", "tags": ["finance", "goal"]},
        {"statement": "美联储2026年可能降息", "tags": ["finance", "macro"]}
    ]
}
EOF

python -m mimir.main --config config.json --port 8000
# 打开 http://localhost:8000
```

---

<div align="center">

Built by [Noogenesis](https://arxiv.org/abs/2603.09476) research team.

*Loss is not primordial. Attention is.*

</div>
