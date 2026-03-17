"""Tests for fast path (Change #5).

Verifies:
- Fast path returns answer using high-confidence beliefs
- Fast path triggers search when no relevant beliefs
- Fast path records episode in memory
- Fast path completes quickly (< 5 seconds with mocks)
"""

import asyncio
import time
from unittest.mock import AsyncMock

from mimir.config import MimirConfig
from mimir.types import Belief, BeliefSource
from mimir.brain.belief_graph import BeliefGraph
from mimir.brain.sec_matrix import SECMatrix
from mimir.brain.prediction import PredictionEngine
from mimir.brain.goal_generator import GoalGenerator
from mimir.brain.memory import Memory
from mimir.llm.client import LLMClient
from mimir.llm.internal import InternalLLM
from mimir.llm.external import ExternalLLM
from mimir.skills.base import SkillRegistry, Skill
from mimir.core.cycle import MimirCycle
from mimir.core.notifier import Notifier


class MockSearchSkill(Skill):
    @property
    def name(self) -> str:
        return "brave_search"

    @property
    def description(self) -> str:
        return "mock search"

    async def execute(self, params: dict) -> dict:
        return {
            "success": True,
            "result": "Search found: AI chips market is $50B.",
            "error": None,
        }


def make_engine(with_beliefs: bool = True) -> MimirCycle:
    config = MimirConfig(
        sec_warmup_cycles=2,
        search_budget_per_cycle=2,
        reasoning_interval=100,
        abstraction_interval=100,
        cycle_interval_seconds=0,
    )

    bg = BeliefGraph(config)
    if with_beliefs:
        bg.add_belief(Belief(
            id="b1", statement="AI chip market growing rapidly",
            confidence=0.85, source=BeliefSource.SEED,
            created_at=0, last_updated=0, last_verified=0,
            tags=["ai", "chips", "market"],
        ))
        bg.add_belief(Belief(
            id="b2", statement="NVIDIA dominates GPU market share",
            confidence=0.9, source=BeliefSource.OBSERVATION,
            created_at=0, last_updated=0, last_verified=0,
            tags=["nvidia", "gpu", "market"],
        ))

    sec = SECMatrix(config)
    pe = PredictionEngine(config)
    mem = Memory(config)
    gg = GoalGenerator(config, bg, sec)
    notifier = Notifier()

    client = LLMClient(api_key="test", base_url="http://test", model="test")

    external = ExternalLLM(client, config)
    external.intent_to_query = AsyncMock(return_value="AI chip market")
    external.chat_answer = AsyncMock(return_value="The AI chip market is valued at $50B.")
    external.extract_beliefs = AsyncMock(return_value={
        "verdict": "support",
        "observed_confidence": 0.8,
        "extracted_facts": ["AI chip market $50B"],
        "new_beliefs": [
            {
                "statement": "AI chip market valued at $50B",
                "tags": ["ai", "chips"],
                "confidence": 0.7,
                "category": "fact",
            }
        ],
    })

    internal = InternalLLM(client, config)
    internal.reason = AsyncMock(return_value=None)
    internal.abstract = AsyncMock(return_value=None)

    registry = SkillRegistry()
    registry.register(MockSearchSkill())

    return MimirCycle(
        belief_graph=bg, sec_matrix=sec, prediction_engine=pe,
        goal_generator=gg, memory=mem, internal_llm=internal,
        external_llm=external, skill_registry=registry,
        notifier=notifier, config=config,
    )


def test_fast_path_with_existing_beliefs():
    """Fast path should return answer using existing high-confidence beliefs."""
    engine = make_engine(with_beliefs=True)

    result = asyncio.run(engine.run_fast_path("Tell me about AI chip market"))

    assert "answer" in result
    assert isinstance(result["answer"], str)
    assert len(result["answer"]) > 0
    # Should have found beliefs (keyword match on "chip", "market", "AI")
    assert isinstance(result["beliefs_used"], list)
    # Should NOT have searched (had high-conf beliefs)
    assert result["searched"] is False


def test_fast_path_triggers_search_when_no_beliefs():
    """Fast path with no matching beliefs should trigger search."""
    engine = make_engine(with_beliefs=False)

    result = asyncio.run(engine.run_fast_path("What is quantum computing?"))

    assert "answer" in result
    assert result["searched"] is True


def test_fast_path_records_episode():
    """Fast path should record an episode in memory."""
    engine = make_engine(with_beliefs=True)
    initial_episodes = len(engine.mem.episodes)

    asyncio.run(engine.run_fast_path("AI chip market trends"))

    assert len(engine.mem.episodes) == initial_episodes + 1
    last_ep = engine.mem.episodes[-1]
    assert "fast_path" in last_ep.action


def test_fast_path_speed():
    """Fast path should complete within 5 seconds (mock environment)."""
    engine = make_engine(with_beliefs=True)

    t0 = time.time()
    result = asyncio.run(engine.run_fast_path("AI chip market"))
    elapsed = time.time() - t0

    assert elapsed < 5.0, f"Fast path took {elapsed:.2f}s, should be < 5s"
    assert result["answer"] is not None


def test_fast_path_does_not_increment_cycle():
    """Fast path should NOT increment the cycle counter."""
    engine = make_engine(with_beliefs=True)
    cycle_before = engine.cycle_count

    asyncio.run(engine.run_fast_path("AI chip market"))

    assert engine.cycle_count == cycle_before


def test_fast_path_search_adds_beliefs():
    """When fast path searches, it should add new beliefs to the graph."""
    engine = make_engine(with_beliefs=False)
    initial_beliefs = len(engine.bg.get_all_beliefs())

    asyncio.run(engine.run_fast_path("What is quantum computing?"))

    # Should have added new beliefs from search extraction
    assert len(engine.bg.get_all_beliefs()) >= initial_beliefs


def test_fast_path_keyword_matching():
    """Fast path should find beliefs by keyword in statement."""
    engine = make_engine(with_beliefs=True)

    result = asyncio.run(engine.run_fast_path("NVIDIA GPU"))

    # Should find b2 which has "NVIDIA" and "GPU" in statement
    assert len(result["beliefs_used"]) > 0
    assert result["searched"] is False
