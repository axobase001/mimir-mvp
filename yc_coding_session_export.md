# Coding Agent Session: Building Skuld's Brain Core in One Day

**Date:** March 17, 2026
**Tools:** Claude (claude.ai) for architecture + Claude Code CLI ("Wren") for implementation
**Operator:** Zhuoran Deng (solo founder)
**What was built:** Skuld's complete Brain core — belief graph, SEC matrix, prediction engine, goal generator, memory system, LLM dual-channel, full cycle engine, web dashboard, multi-user auth, Docker deployment. From zero files to 88 passing tests.

---

## Session Context

I had just finished a NeurIPS 2026 paper on SEC (Staleness-Error Correlation) — a mechanism that learns what to pay attention to from prediction error. I'd been running experiments with "Adam," an autonomous LLM agent with SEC as an external filter on its search pipeline. After three versions, I realized the fundamental problem: SEC was bolted onto the outside. The LLM was still the brain. SEC was a suggestion box that the brain could ignore.

The question that changed everything: "What if the LLM isn't the brain?"

This session documents what happened next.

---

## Phase 1: Architecture Design (Claude, claude.ai)

### The Key Insight

I told Claude (my theory/analysis partner, codename "沈晚") that I wanted to define AGI's minimal form. We went back and forth:

**Me:** "一步一步来 你想想我们最初的设计 最关键的组件是世界模型、u函数和自目标机制 现在u函数和自目标机制被整合成了SEC 这个是外挂的信息层/目标层 能够让agi有行动闭环和自主目标的能力 那下一步是构建一个简单的世界模型。"

**Claude:** Proposed two paths — DreamerV3's RSSM (neural network world model) or a Bayesian belief graph (lightweight, interpretable, runs on CPU).

**Me:** "你先去搜下RSSM对硬件和计算的要求 我怕我的破笔记本撑不起来"

Claude searched, confirmed DreamerV3 needs GPU (A100 in the paper, OOM on RTX 3090). Recommended the Bayesian belief graph — runs on numpy, interpretable, compatible with SEC's statistical framework.

Then the critical architectural decision:

**Me:** "我们得想办法把llm变成大脑的一部分 ——有的时候是工具 有的时候是大脑的一部分"

**Claude's response:** "对。我之前切得太干净了。LLM不是外挂的翻译官。LLM是大脑的语言皮层。"

This became the dual-channel architecture:
- **Internal calls** (LLM as part of brain): reasoning, simulation, planning, abstraction. Output is tagged as INFERENCE, confidence discounted, must be verified by observation later.
- **External calls** (LLM as tool): translating search queries, extracting structured data from results, summarizing. Output feeds directly into belief updates.

No one in the literature has made this distinction. Every agent framework (CoALA, AutoGen, CrewAI, Manus) treats all LLM calls the same. We split them into two pipelines with different trust levels. This is operationalized metacognition — the system knows the difference between "what I thought" and "what I saw."

### Architecture Verification

I asked Claude to search whether anyone had built this before. After extensive search across agent memory papers (Mem0, MAGMA, AgeMem, MemoryOS), cognitive architectures (Soar, ACT-R, CoALA), and world model research (LeCun's AMI Labs, DreamerV3):

**Claude:** "没有人在做你要做的东西。"

Specifically: no one has built a non-LLM brain that owns persistent state, uses differential prediction error (SEC) for attention, and calls the LLM through differentiated internal/external channels.

---

## Phase 2: Spec Writing → Wren Implementation (Claude Code CLI)

I wrote detailed implementation specs in Claude (claude.ai), then fed them to Wren (Claude Code CLI) for implementation. Here's the actual workflow:

### Step 1: Brain Core

**Spec delivered to Wren:**
- `types.py`: Belief, SECEntry, Episode, Procedure, Goal dataclasses
- `belief_graph.py`: networkx DiGraph with Bayesian confidence updates, dependency propagation, pruning
- `sec_matrix.py`: Differential SEC (D_obs, D_not, C values), filter logic with C=0 probe detection
- `prediction.py`: Prediction engine (no LLM — pure brain)
- `goal_generator.py`: Goals emerge from PE patterns (same mechanism as Cambria D_generative)
- `memory.py`: Episodic + procedural memory

**Wren output:** All 6 modules + tests. 29/29 tests passing.

Key test — SEC matrix correctly separates positive and negative C values from simulated observation data:

```
> python -m pytest tests/ -v
tests/test_belief_graph.py::test_add_and_retrieve PASSED
tests/test_belief_graph.py::test_confidence_update_with_low_pe PASSED
tests/test_belief_graph.py::test_confidence_update_with_high_pe PASSED
tests/test_belief_graph.py::test_propagate_update PASSED
tests/test_belief_graph.py::test_decay_unverified PASSED
tests/test_belief_graph.py::test_prune_low_confidence PASSED
tests/test_belief_graph.py::test_serialization_roundtrip PASSED
tests/test_belief_graph.py::test_full_lifecycle PASSED
tests/test_sec_matrix.py::test_initial_c_value_zero PASSED
tests/test_sec_matrix.py::test_c_value_sign_with_mixed_observation PASSED
tests/test_sec_matrix.py::test_filter_warmup_period PASSED
tests/test_sec_matrix.py::test_filter_negative_c PASSED
tests/test_sec_matrix.py::test_probe_high_coverage_zero_c PASSED
tests/test_sec_matrix.py::test_serialization_roundtrip PASSED
...
29/29 passed in 0.51s
```

### Step 2: LLM Dual-Channel + Cycle Engine + Skills

**Spec delivered to Wren:**
- `llm/client.py`: Unified LLM API (DeepSeek/Claude/any OpenAI-compatible)
- `llm/internal.py`: Internal calls — reason(), simulate(), plan(), abstract()
- `llm/external.py`: External calls — intent_to_query(), extract_beliefs(), summarize_cycle()
- `skills/base.py`: Skill base class + registry
- `skills/search.py`: Brave Search API
- `skills/file_io.py`: File read/write
- `core/cycle.py`: The 10-phase cognitive cycle (wake → predict → select → observe → PE → update → reason → goal → reflect → sleep)
- `core/notifier.py`: Proactive notification queue

**Wren output:** Full implementation + tests.

**First live run — 5 seed beliefs, 5 cycles:**
```
Cycle 1: 5 seeds → 17 beliefs (12 new from search)
Cycle 2: 17 → 20 beliefs (9 deduplicated, 3 new)
Cycle 3: 20 → 31 beliefs, SEC matrix: 51 clusters
Cycle 4: 31 → 48 beliefs, 2 autonomous goals generated
         Goal 1: "Investigate: 美联储降息趋势" (high PE for 3 cycles)
         Goal 2: "Investigate: AI regulation impact" (PE > threshold)
Cycle 5: URGENT notification — seed_002 PE jumped to 0.568
         67 beliefs, 153 SEC clusters, $0.0006 total cost
```

The system generated goals I never specified. It detected an anomaly I didn't expect. It cost less than a penny.

### Step 3: Dashboard + Chat + WebSocket

**Spec delivered to Wren:**
- FastAPI server with D3.js belief graph, Chart.js SEC matrix, real-time WebSocket
- Chat interface that answers from the belief graph (not LLM hallucination)
- Semantic deduplication (fixing Step 2's duplicate belief problem)
- Goal management UI

**Wren output:** 59/59 tests passing. Dashboard live at localhost:8000.

### Step 4: Multi-User + Auth + Docker Deployment

**Spec delivered to Wren:**
- User registration/login with JWT + bcrypt
- Per-user Brain isolation (separate state directories)
- Onboarding flow with 5 preset templates (Financial Analyst, Developer, Researcher, Entrepreneur, Custom)
- Scheduler for multi-Brain cycle management
- Docker + docker-compose + nginx
- API key encryption (Fernet)
- Rate limiting + usage quotas (free: 3 cycles/day, pro: 20)

**Wren output:** Full implementation. Multi-user product running.

---

## Phase 3: Validation

### Research Validation
- SEC mechanism: p = 0.004, d = 0.45, 200 seeds (matches oracle, p = 0.18)
- Goal anticipation: median 1 tick vs 15 ticks (p = 5.49 × 10⁻¹⁴)
- Paper: arXiv:2603.09476, ALIFE 2026 accepted, NeurIPS 2026 under review

### Product Validation
- 88/88 tests passing
- 5 seed beliefs → 67 nodes in 16 cycles
- Autonomous goal generation confirmed
- SEC matrix differentiation (positive and negative C values) confirmed
- Multi-user isolation confirmed
- Total LLM cost for full test run: $0.05
- Deployment cost: €4.5/month (Hetzner VPS)

### Architecture Validation
The key claim — "Brain independent of LLM" — is testable: swap DeepSeek for Claude in config.json, restart. Belief graph, SEC matrix, all memory intact. The brain didn't lose a single node. Only the language quality of new inferences changed.

No existing agent framework can do this. Every other system's "knowledge" is entangled with the LLM's context window.

---

## What This Session Demonstrates

1. **AI-assisted development at extreme speed.** From architectural concept to deployed multi-user product in 8 days, solo, using Claude as both architect and engineer.

2. **The specs matter more than the code.** Every module was built from a detailed spec I wrote in Claude (theory instance), then handed to Wren (code instance). The specs contained method signatures, logic descriptions, test requirements, and acceptance criteria. Wren rarely needed corrections because the specs were precise. This is how one person builds what normally takes a team of 5-10.

3. **Research-to-product pipeline.** The SEC mechanism went from NeurIPS paper to production feature in the same week. The belief graph went from whiteboard concept to 67-node live system in one day. Academic rigor and product velocity aren't tradeoffs — the research made the engineering decisions obvious.

4. **The architecture is the moat.** Not the code (which AI can replicate), not the LLM (which is swappable), but the specific combination of belief graph + differential SEC + LLM dual-channel + procedural memory — born from 3 versions of failed experiments and 7 validated experimental systems.

---

*Total tokens used in this session: ~400K input, ~200K output across both Claude instances.*
*Total cost: ~$3 (Claude API) + $0.05 (DeepSeek for Skuld's own cycles)*
*Total time: ~14 hours of active work*
*Lines of code produced: ~6,000*
*Tests: 88/88 passing*
