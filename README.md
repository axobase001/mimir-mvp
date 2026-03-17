<div align="center">

<img src="assets/skuld_wordmark.png" alt="Skuld" width="600">

<br><br>

**Your AI doesn't think. Skuld does.**

*The brain is not the LLM. The LLM is a tool the brain uses.*

[English](#the-problem) | [中文](#问题)

---

**241 tests** · **11 skills** · **$0.028 for 33 cognitive cycles** · **Token cost ↓50% with use**

[Paper](https://arxiv.org/abs/2603.09476) · NeurIPS 2026 Submitted · ALIFE 2026 Accepted

</div>

---

## The Problem

Every AI agent framework puts the LLM at the center and bolts memory around it. Manus, AutoGen, CrewAI, LangChain — they're all the same architecture: a stateless language model pretending to remember.

**When you swap the model, the "memory" breaks. When the context window fills, the agent forgets. When the API goes down, the brain goes dark.**

These aren't bugs. They're architectural consequences of building on the wrong primitive.

## The Insight

We asked a different question: **what if attention comes before loss?**

Three years of research. Three forward-learning mechanisms that failed. Then we found it — a reverse association principle where observation priority emerges from prediction error, not from training.

We published the math. We proved it in simulation. Then we built a product on it.

## What Skuld Is

Skuld is a cognitive engine where **the Brain owns state and the LLM is a tool**.

```
┌─────────────────────────── BRAIN ────────────────────────────┐
│                                                               │
│   Belief Graph          SEC Matrix         Goal Generator     │
│   ┌─────────┐          ┌─────────┐        ┌─────────┐       │
│   │ Nodes:  │          │ C = Δ   │        │ PE →    │       │
│   │ beliefs │          │ between │        │ goals   │       │
│   │ Edges:  │          │ observe │        │ emerge  │       │
│   │ deps    │          │ vs not  │        │         │       │
│   └─────────┘          └─────────┘        └─────────┘       │
│                              │                               │
│              ┌───────────────┴────────────────┐              │
│              ▼                                ▼              │
│   ┌──────────────────┐            ┌──────────────────┐      │
│   │   Internal LLM   │            │   External LLM   │      │
│   │   reason / plan  │            │   search / extract│      │
│   └──────────────────┘            └──────────────────┘      │
│              │                                │              │
│              └───────────────┬────────────────┘              │
│                              ▼                               │
│   ┌──────────────────────────────────────────────────┐      │
│   │  search · code · docs · email · fetch · analyze  │      │
│   └──────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

Swap the LLM. The Brain survives. We tested it: **belief survival rate 100%, SEC correlation 1.0, procedural memory 100% preserved.**

## The Data

We don't have pitch slides. We have numbers.

### Learning Curve — Same Task × 10

| | Iteration 1 | Iteration 10 | Change |
|---|---|---|---|
| **Token consumption** | 3,285 | 1,639 | **↓ 50.1%** |
| **LLM calls** | 9 | 4 | **↓ 55.6%** |
| **Quality score** | 0.95 | 0.95 | **Unchanged** |

The inflection point is iteration 3→4. After three executions, the belief graph accumulates enough knowledge to skip search entirely. The system answers from what it already knows.

**No competitor has published comparable data.** We checked: Mem0 ($24M raised), Cognee (€7.5M), Personal.ai, Lindy.ai, Kin — none have measurable learning curves.

### 33-Cycle Life Test

| Metric | Value |
|---|---|
| Starting beliefs | 5 seeds |
| Peak beliefs | 45 |
| Final beliefs (after pruning) | 26 |
| SEC clusters | 141 |
| SEC differentiation | 8 positive C, 5 negative C |
| Autonomous goals created | 4 |
| Autonomous goals abandoned | 3 |
| **Total cost** | **$0.028** |

The Brain grew, learned which directions to observe, generated its own investigation goals, completed them, and abandoned the ones that stopped being useful. Nobody told it to do any of this.

### First Autonomous Learning Event

On cycle 64, a user sent non-instructional feedback about Skuld's work output. The message explicitly said "this is not an instruction."

Skuld's response:
- Classified the feedback as a query (not an action)
- Triggered an instant web search for best practices
- Extracted 9 new beliefs, 5 of them PROCEDURE type
- Generated a self-improvement plan **without being asked**
- Self-critiqued: *"focusing on format over persuasion was the fatal flaw"*

This is the first recorded instance of a live cognitive system autonomously forming meta-cognitive beliefs from natural language feedback.

## The Science

**Paper:** [Attention Before Loss: Endogenous Observation Priority from World-Model Prediction Error](https://arxiv.org/abs/2603.09476)

**Core mechanism — SEC (Staleness-Error Correlation):**

```
C(i) = E[PE | cluster i not observed] - E[PE | cluster i observed]
```

Positive C = observing this direction reduces prediction error (useful).
Negative C = observing this direction doesn't help (waste of attention).

The Brain learns where to look. No reward function. No RLHF. No human labels. Attention emerges from prediction error.

**Validation:**
- SEC vs independent π: **p = 0.004**
- Goal anticipation: **p = 5.49 × 10⁻¹⁴**
- CMAPSS industrial validation: **p = 0.0002**

## What's Open, What's Not

| Layer | Status | Description |
|---|---|---|
| LLM integration | **Open** | Dual-channel (internal reasoning + external extraction) |
| Skill system | **Open** | 11 skills: search, code, docs, email, fetch, analyze, API, PDF... |
| Cognitive cycle | **Open** | 10-phase loop: wake → predict → select → observe → PE → update → reason → goals → reflect → prune |
| Multi-step orchestrator | **Open** | LLM decomposes complex tasks into skill chains |
| Dashboard | **Open** | D3.js belief graph + Chart.js SEC matrix + real-time WebSocket |
| Multi-user + Auth | **Open** | JWT, per-user Brain isolation, usage limits, Docker |
| **SEC matrix** | **Proprietary** | The attention allocation mechanism. Interface stubs provided. |
| **Belief graph** | **Proprietary** | Bayesian confidence updates + dependency propagation. Interface stubs provided. |

The engineering is open. The science is the moat.

## Quick Start

```bash
pip install -r requirements.txt

cat > config.json << 'EOF'
{
    "llm_api_key": "your-deepseek-or-openai-key",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-chat",
    "brave_api_key": "your-brave-search-key",
    "seed_beliefs": [
        {"statement": "AI regulation is increasing globally", "tags": ["regulation"]},
        {"statement": "Federal Reserve may cut rates in 2026", "tags": ["finance"]}
    ]
}
EOF

python -m mimir.main --config config.json --port 8000
# Open http://localhost:8000
```

## Docker

```bash
docker-compose up -d
# Dashboard at http://localhost
```

---

## 问题

所有AI agent框架都把LLM放在中心，外面套记忆模块。Manus、AutoGen、CrewAI、LangChain——全是同一个架构：一个无状态的语言模型假装自己有记忆。

**换模型，记忆断。上下文满，开始忘。API挂了，大脑灭。**

这不是bug。这是把房子建在错误地基上的必然结果。

## 洞察

我们问了一个不同的问题：**如果注意力先于损失函数呢？**

三年研究。三种前向学习机制全部失败。然后我们找到了——一个反向关联原理，观测优先级从预测误差中涌现，不需要训练。

数学发表了。仿真证明了。然后我们把它做成了产品。

## Skuld是什么

Skuld是一个认知引擎。**Brain拥有状态，LLM是工具。**

换LLM，Brain的积累不丢。我们测了：信念存活率100%，SEC相关性1.0，程序记忆100%保留。

## 数据

### 学习曲线——同任务×10

| | 第1次 | 第10次 | 变化 |
|---|---|---|---|
| **Token消耗** | 3,285 | 1,639 | **↓ 50.1%** |
| **LLM调用** | 9 | 4 | **↓ 55.6%** |
| **质量评分** | 0.95 | 0.95 | **不变** |

拐点在第3→4次。3次执行后信念图积累了足够的知识，直接跳过搜索，从已知中回答。

**没有任何竞品发布过类似数据。** 我们查了：Mem0（融了$24M）、Cognee（€7.5M）、Personal.ai、Lindy.ai、Kin——全都没有可度量的学习曲线。

### 33周期生命周期测试

5个种子信念 → 信念图增长到45 → 修剪到26。SEC自主分化出8个正C方向（值得看）和5个负C方向（浪费注意力）。自主生成4个目标，完成3个，abandon了不再有价值的。总成本 **$0.028**。

没有人告诉它做这些。

### 第一次自主学习事件

Cycle 64，用户发送了非指令性反馈。消息明确说"这不是指令"。

Skuld的反应：
- 把反馈分类为query（不是action）
- 即时搜索冷邮件最佳实践
- 提取了9条新信念，其中5条是PROCEDURE类型
- **在没有被要求的情况下**生成了自我改进计划
- 自我批评：*"侧重格式而非说服力是致命伤"*

这是产品历史上第一次记录到：一个活的认知系统从自然语言反馈中自主形成元认知信念。

## 科学

**论文：** [Attention Before Loss: Endogenous Observation Priority from World-Model Prediction Error](https://arxiv.org/abs/2603.09476)

**核心机制——SEC（差分错误相关性）：**

```
C(i) = E[PE | 簇i未被观测] - E[PE | 簇i被观测]
```

正C = 观测这个方向降低了预测误差（有用）。
负C = 观测这个方向没有帮助（浪费注意力）。

Brain自己学会了往哪看。没有reward function。没有RLHF。没有人工标注。注意力从预测误差中涌现。

## 什么开源了，什么没有

| 层 | 状态 | 说明 |
|---|---|---|
| LLM集成 | **开源** | 双通道（对内推理 + 对外提取） |
| 技能系统 | **开源** | 11个技能：搜索、代码、文档、邮件、抓取、分析、API、PDF… |
| 认知周期 | **开源** | 10阶段闭环 |
| 多步编排器 | **开源** | LLM把复杂任务拆成技能链 |
| Dashboard | **开源** | D3信念图 + Chart.js SEC矩阵 + WebSocket实时推送 |
| 多用户+认证 | **开源** | JWT、Brain隔离、用量限制、Docker |
| **SEC矩阵** | **专有** | 注意力分配机制。提供接口定义。 |
| **信念图** | **专有** | 贝叶斯置信度更新 + 依赖传播。提供接口定义。 |

工程开源。科学是护城河。

---

<div align="center">

**Noogenesis Research** · Penang, Malaysia

*Loss is not primordial. Attention is.*

arXiv:2603.09476 · NeurIPS 2026 · ALIFE 2026

<br>

<img src="assets/skuld_main.png" alt="Skuld" width="200">

</div>
