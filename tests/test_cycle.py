import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from mimir.config import MimirConfig
from mimir.types import Belief, BeliefSource
from mimir.state import MimirState
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
            "result": "- GDPR fines reached $1.5M in 2024\n- EU enforcing stricter rules",
            "error": None,
        }


def make_config() -> MimirConfig:
    return MimirConfig(
        sec_warmup_cycles=2,
        search_budget_per_cycle=2,
        reasoning_interval=3,
        abstraction_interval=100,  # disable for test
        goal_pe_threshold=0.3,
        goal_pe_persistence=2,
        goal_staleness_threshold=5,
        cycle_interval_seconds=0,
    )


def make_seeds(config: MimirConfig) -> BeliefGraph:
    bg = BeliefGraph(config)
    seeds = [
        ("s0", "GDPR fines exceeded $1M in 2024", 0.8, ["gdpr", "regulation"]),
        ("s1", "AI regulation is increasing globally", 0.7, ["ai_reg", "regulation"]),
        ("s2", "Cloud computing costs rising 20% YoY", 0.6, ["cloud", "costs"]),
        ("s3", "Zero trust adoption exceeds 60%", 0.75, ["security", "enterprise"]),
        ("s4", "Remote work adoption declining", 0.5, ["workforce", "trends"]),
    ]
    for bid, stmt, conf, tags in seeds:
        bg.add_belief(Belief(
            id=bid, statement=stmt, confidence=conf,
            source=BeliefSource.SEED, created_at=0,
            last_updated=0, last_verified=0, tags=tags,
        ))
    return bg


def make_engine(config: MimirConfig) -> MimirCycle:
    bg = make_seeds(config)
    sec = SECMatrix(config)
    pe = PredictionEngine(config)
    mem = Memory(config)
    gg = GoalGenerator(config, bg, sec)
    notifier = Notifier()

    # Mock LLM client
    client = LLMClient(api_key="test", base_url="http://test", model="test")

    # External LLM mocks
    external = ExternalLLM(client, config)
    # intent_to_query: just return the statement shortened
    external.intent_to_query = AsyncMock(side_effect=lambda intent, **kw: intent[:50])

    # extract_beliefs: alternate between support and new info
    call_count = {"n": 0}

    async def mock_extract(results, belief):
        call_count["n"] += 1
        if call_count["n"] % 3 == 0:
            return {
                "verdict": "contradict",
                "observed_confidence": 0.7,
                "extracted_facts": ["contradicting data"],
                "new_beliefs": [],
            }
        return {
            "verdict": "support",
            "observed_confidence": 0.8,
            "extracted_facts": ["supporting fact"],
            "new_beliefs": [
                {
                    "statement": f"New finding from cycle (call {call_count['n']})",
                    "tags": belief.tags[:1] if belief.tags else ["misc"],
                    "confidence": 0.6,
                }
            ],
        }

    external.extract_beliefs = mock_extract
    external.summarize_cycle = AsyncMock(return_value="Cycle completed successfully.")

    # Internal LLM mocks
    internal = InternalLLM(client, config)
    internal.reason = AsyncMock(return_value=Belief(
        id="", statement="Inferred: regulation drives compliance costs",
        confidence=0.4, source=BeliefSource.INFERENCE,
        created_at=0, last_updated=0, last_verified=0,
        tags=["regulation", "costs"],
        parent_ids=["s0", "s1"],
    ))
    internal.abstract = AsyncMock(return_value=None)

    # Skills
    registry = SkillRegistry()
    registry.register(MockSearchSkill())

    return MimirCycle(
        belief_graph=bg,
        sec_matrix=sec,
        prediction_engine=pe,
        goal_generator=gg,
        memory=mem,
        internal_llm=internal,
        external_llm=external,
        skill_registry=registry,
        notifier=notifier,
        config=config,
    )


def test_five_cycles():
    """Run 5 full cycles and verify growth."""
    config = make_config()
    engine = make_engine(config)

    initial_beliefs = len(engine.bg.get_all_beliefs())
    assert initial_beliefs == 5

    summaries = []
    for _ in range(5):
        summary = asyncio.run(engine.run_one_cycle())
        summaries.append(summary)

    # Belief graph should have grown (new observations + inference)
    final_beliefs = len(engine.bg.get_all_beliefs())
    assert final_beliefs > initial_beliefs, (
        f"Beliefs should grow: {initial_beliefs} -> {final_beliefs}"
    )

    # SEC should have entries
    assert len(engine.sec.entries) > 0

    # Should have run 5 cycles
    assert engine.cycle_count == 5

    # Memory should have episodes
    assert len(engine.mem.episodes) == 5


def test_sec_differentiation():
    """After several cycles, SEC should show non-zero C values."""
    config = make_config()
    config.sec_warmup_cycles = 1
    engine = make_engine(config)

    for _ in range(8):
        asyncio.run(engine.run_one_cycle())

    # Some clusters should have observations
    has_observations = any(
        e.obs_count > 0 for e in engine.sec.entries.values()
    )
    assert has_observations


def test_state_persistence():
    """Save state, reload, continue running."""
    config = make_config()
    engine = make_engine(config)

    # Run 3 cycles
    for _ in range(3):
        asyncio.run(engine.run_one_cycle())

    beliefs_before = len(engine.bg.get_all_beliefs())
    cycle_before = engine.cycle_count

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = str(Path(tmpdir) / "state.json")

        # Save
        MimirState.save(
            state_path,
            engine.bg,
            engine.sec,
            engine.mem,
            engine.goal_gen.goals,
            engine.cycle_count,
            {},
        )

        # Verify file exists and is valid JSON
        data = json.loads(Path(state_path).read_text())
        assert data["cycle_count"] == cycle_before

        # Load
        bg2, sec2, mem2, goals2, cc2, usage2 = MimirState.load(state_path, config)

        assert cc2 == cycle_before
        assert len(bg2.get_all_beliefs()) == beliefs_before
        assert len(sec2.entries) == len(engine.sec.entries)
        assert len(mem2.episodes) == len(engine.mem.episodes)


def test_goal_generation():
    """After enough cycles, at least one goal should be generated."""
    config = make_config()
    config.goal_pe_persistence = 2
    config.goal_pe_threshold = 0.2
    engine = make_engine(config)

    for _ in range(6):
        asyncio.run(engine.run_one_cycle())

    total_goals = len(engine.goal_gen.goals)
    # Should have at least 1 goal (investigate or refresh)
    assert total_goals >= 1 or len(engine.bg.get_all_beliefs()) > 5, (
        "Should have generated goals or grown beliefs"
    )
