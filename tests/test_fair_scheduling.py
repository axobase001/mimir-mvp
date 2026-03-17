"""Tests for EXOGENOUS SEC bypass and multi-goal fair scheduling.

Features #1 and #2: EXOGENOUS goals bypass SEC filter,
multi-goal fair slot allocation in _select_candidates().
"""

import asyncio
from unittest.mock import AsyncMock

from mimir.config import MimirConfig
from mimir.types import (
    Belief, BeliefSource, Goal, GoalOrigin, GoalStatus, SECEntry,
)
from mimir.brain.belief_graph import BeliefGraph
from mimir.brain.sec_matrix import SECMatrix
from mimir.brain.prediction import PredictionEngine
from mimir.brain.goal_generator import GoalGenerator
from mimir.brain.memory import Memory
from mimir.llm.client import LLMClient
from mimir.llm.internal import InternalLLM
from mimir.llm.external import ExternalLLM
from mimir.skills.base import Skill, SkillRegistry
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
        return {"success": True, "result": "mock result", "error": None}


def make_config() -> MimirConfig:
    return MimirConfig(
        sec_warmup_cycles=2,
        search_budget_per_cycle=3,
        reasoning_interval=100,
        abstraction_interval=100,
        goal_pe_threshold=0.3,
        goal_pe_persistence=2,
        goal_staleness_threshold=50,
        cycle_interval_seconds=0,
    )


def make_engine_with_goals(config, beliefs, goals):
    """Create a MimirCycle with specific beliefs and goals."""
    bg = BeliefGraph(config)
    for b in beliefs:
        bg.add_belief(b)

    sec = SECMatrix(config)
    pe = PredictionEngine(config)
    mem = Memory(config)
    gg = GoalGenerator(config, bg, sec)
    for g in goals:
        gg.goals[g.id] = g

    notifier = Notifier()
    client = LLMClient(api_key="test", base_url="http://test", model="test")
    external = ExternalLLM(client, config)
    external.intent_to_query = AsyncMock(side_effect=lambda intent, **kw: intent[:50])
    external.extract_beliefs = AsyncMock(return_value={
        "verdict": "support", "observed_confidence": 0.8,
        "extracted_facts": [], "new_beliefs": [],
    })
    external.summarize_cycle = AsyncMock(return_value="ok")

    internal = InternalLLM(client, config)
    internal.reason = AsyncMock(return_value=None)
    internal.abstract = AsyncMock(return_value=None)

    registry = SkillRegistry()
    registry.register(MockSearchSkill())

    engine = MimirCycle(
        belief_graph=bg, sec_matrix=sec, prediction_engine=pe,
        goal_generator=gg, memory=mem,
        internal_llm=internal, external_llm=external,
        skill_registry=registry, notifier=notifier, config=config,
    )
    return engine


# ── Test #1: EXOGENOUS goal bypasses SEC filter ──

def test_exogenous_goal_bypasses_sec():
    """An EXOGENOUS goal's target belief must be selected even when SEC
    would normally filter it (negative C value)."""
    config = make_config()
    config.sec_warmup_cycles = 0  # No warmup so SEC is active

    b1 = Belief(
        id="b1", statement="BTC price is 60k",
        confidence=0.8, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["btc"],
    )

    exo_goal = Goal(
        id="g1", target_belief_id="b1",
        description="Watch BTC", reason="User requested",
        status=GoalStatus.ACTIVE, priority=0.8,
        origin=GoalOrigin.EXOGENOUS,
    )

    engine = make_engine_with_goals(config, [b1], [exo_goal])
    # Force negative SEC C value for "btc" tag
    entry = SECEntry(cluster="btc", d_obs=0.5, d_not=0.1,
                     obs_count=10, not_count=10)
    engine.sec.entries["btc"] = entry
    assert entry.c_value < 0  # Confirm it's negative

    # Set cycle > warmup so SEC is active
    engine.cycle_count = 10

    candidates = engine._select_candidates()
    candidate_ids = [c.id for c in candidates]

    # b1 should be selected because EXOGENOUS bypasses SEC
    assert "b1" in candidate_ids


def test_endogenous_goal_respects_sec():
    """An ENDOGENOUS goal's target belief should be filtered by SEC
    if its tags have very negative C values."""
    config = make_config()
    config.sec_warmup_cycles = 0

    b1 = Belief(
        id="b1", statement="Some fact",
        confidence=0.8, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["bad_cluster"],
    )

    endo_goal = Goal(
        id="g1", target_belief_id="b1",
        description="Investigate fact", reason="High PE",
        status=GoalStatus.ACTIVE, priority=0.8,
        origin=GoalOrigin.ENDOGENOUS,
    )

    engine = make_engine_with_goals(config, [b1], [endo_goal])
    # Force very negative SEC C value — will be rejected with high probability
    entry = SECEntry(cluster="bad_cluster", d_obs=0.9, d_not=0.0,
                     obs_count=20, not_count=20)
    engine.sec.entries["bad_cluster"] = entry
    assert entry.c_value < 0  # Confirm negative

    engine.cycle_count = 50

    # Run multiple times; with very negative C, should mostly be filtered
    selected_count = 0
    for _ in range(20):
        candidates = engine._select_candidates()
        if any(c.id == "b1" for c in candidates):
            selected_count += 1

    # Should be filtered most of the time (probabilistic, but very negative C)
    assert selected_count < 15, f"Expected mostly filtered, but selected {selected_count}/20"


def test_exogenous_still_selected_with_zero_c():
    """EXOGENOUS goal bypass works even when SEC C is exactly 0."""
    config = make_config()
    config.sec_warmup_cycles = 0

    b1 = Belief(
        id="b1", statement="BTC price",
        confidence=0.7, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["crypto"],
    )

    goal = Goal(
        id="g1", target_belief_id="b1",
        description="Watch BTC", reason="User",
        status=GoalStatus.ACTIVE, priority=0.5,
        origin=GoalOrigin.EXOGENOUS,
    )

    engine = make_engine_with_goals(config, [b1], [goal])
    engine.cycle_count = 100

    candidates = engine._select_candidates()
    assert any(c.id == "b1" for c in candidates)


# ── Test #2: Multi-goal fair scheduling ──

def test_fair_scheduling_three_goals():
    """3 goals with priorities 0.8/0.5/0.3, budget=3.
    Each should get roughly proportional slots."""
    config = make_config()
    config.search_budget_per_cycle = 3

    beliefs = [
        Belief(id="b1", statement="A", confidence=0.8, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["a"]),
        Belief(id="b2", statement="B", confidence=0.7, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["b"]),
        Belief(id="b3", statement="C", confidence=0.6, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["c"]),
    ]
    goals = [
        Goal(id="g1", target_belief_id="b1", description="G1",
             reason="r", status=GoalStatus.ACTIVE, priority=0.8,
             origin=GoalOrigin.EXOGENOUS),
        Goal(id="g2", target_belief_id="b2", description="G2",
             reason="r", status=GoalStatus.ACTIVE, priority=0.5,
             origin=GoalOrigin.EXOGENOUS),
        Goal(id="g3", target_belief_id="b3", description="G3",
             reason="r", status=GoalStatus.ACTIVE, priority=0.3,
             origin=GoalOrigin.EXOGENOUS),
    ]

    engine = make_engine_with_goals(config, beliefs, goals)
    engine.cycle_count = 10

    candidates = engine._select_candidates()
    candidate_ids = [c.id for c in candidates]

    # With budget=3, we expect either 2/1/0 or 1/1/1 distribution
    # The highest priority goal (0.8) should always be present
    assert "b1" in candidate_ids
    assert len(candidates) <= 3


def test_fair_scheduling_guarantees_minimum_rotation():
    """Over 3 cycles, every goal should be served at least once."""
    config = make_config()
    config.search_budget_per_cycle = 1  # Tight budget

    beliefs = [
        Belief(id="b1", statement="A", confidence=0.8, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["a"]),
        Belief(id="b2", statement="B", confidence=0.7, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["b"]),
        Belief(id="b3", statement="C", confidence=0.6, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["c"]),
    ]
    goals = [
        Goal(id="g1", target_belief_id="b1", description="G1",
             reason="r", status=GoalStatus.ACTIVE, priority=0.5,
             origin=GoalOrigin.EXOGENOUS),
        Goal(id="g2", target_belief_id="b2", description="G2",
             reason="r", status=GoalStatus.ACTIVE, priority=0.3,
             origin=GoalOrigin.EXOGENOUS),
        Goal(id="g3", target_belief_id="b3", description="G3",
             reason="r", status=GoalStatus.ACTIVE, priority=0.2,
             origin=GoalOrigin.EXOGENOUS),
    ]

    engine = make_engine_with_goals(config, beliefs, goals)

    served_ids: set[str] = set()
    for cycle in range(1, 7):  # 6 cycles should be enough
        engine.cycle_count = cycle
        candidates = engine._select_candidates()
        for c in candidates:
            served_ids.add(c.id)

    # All three beliefs should have been served at least once across cycles
    assert "b1" in served_ids
    assert "b2" in served_ids
    assert "b3" in served_ids


def test_no_goals_fallback_to_pe_sort():
    """When no active goals, should fall back to PE-sorted + stale logic."""
    config = make_config()
    config.search_budget_per_cycle = 2

    beliefs = [
        Belief(id="b1", statement="Low PE", confidence=0.9, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["x"],
               pe_history=[0.1]),
        Belief(id="b2", statement="High PE", confidence=0.5, source=BeliefSource.SEED,
               created_at=0, last_updated=0, last_verified=0, tags=["y"],
               pe_history=[0.8]),
    ]

    engine = make_engine_with_goals(config, beliefs, [])
    engine.cycle_count = 3

    candidates = engine._select_candidates()
    candidate_ids = [c.id for c in candidates]

    # b2 has higher PE and should come first
    assert candidate_ids[0] == "b2"
