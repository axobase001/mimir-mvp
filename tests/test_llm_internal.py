import asyncio
from unittest.mock import AsyncMock

from mimir.llm.client import LLMClient
from mimir.llm.internal import InternalLLM
from mimir.types import Belief, BeliefSource, Goal, GoalStatus
from mimir.config import MimirConfig


def make_belief(bid: str, statement: str, confidence: float = 0.8,
                tags: list[str] | None = None) -> Belief:
    return Belief(
        id=bid, statement=statement, confidence=confidence,
        source=BeliefSource.SEED, created_at=0,
        last_updated=0, last_verified=0, tags=tags or [],
    )


def make_internal(mock_response: str) -> InternalLLM:
    client = LLMClient(api_key="test", base_url="http://test", model="test")
    client.complete = AsyncMock(return_value=mock_response)
    return InternalLLM(client, MimirConfig())


def test_reason_success():
    internal = make_internal(
        '{"statement": "AI regulation will increase GDPR fines", '
        '"tags": ["gdpr", "ai_reg"], "reasoning": "combined trends"}'
    )
    b1 = make_belief("b1", "GDPR fines exceeded $1M", 0.8, ["gdpr"])
    b2 = make_belief("b2", "AI regulation increasing", 0.7, ["ai_reg"])

    result = asyncio.run(internal.reason(b1, b2, cycle=1))

    assert result is not None
    assert "GDPR" in result.statement or "regulation" in result.statement.lower()
    assert result.source == BeliefSource.INFERENCE
    assert result.parent_ids == ["b1", "b2"]
    # confidence = 0.8 * 0.7 * 0.7 = 0.392
    assert abs(result.confidence - 0.392) < 1e-3


def test_reason_none():
    internal = make_internal('{"result": "none"}')
    b1 = make_belief("b1", "GDPR fines", 0.8)
    b2 = make_belief("b2", "Weather is nice", 0.9)

    result = asyncio.run(internal.reason(b1, b2, cycle=1))
    assert result is None


def test_simulate_sorting():
    internal = make_internal(
        '[{"action": "search GDPR", "expected_pe_change": -0.3, '
        '"affected_beliefs": ["b1"], "reasoning": "direct"},'
        '{"action": "search weather", "expected_pe_change": 0.1, '
        '"affected_beliefs": ["b2"], "reasoning": "unrelated"}]'
    )

    results = asyncio.run(internal.simulate("beliefs summary", ["search GDPR", "search weather"]))

    assert len(results) == 2
    # Sorted by expected_pe_change ascending (most PE reduction first)
    assert results[0]["expected_pe_change"] <= results[1]["expected_pe_change"]


def test_plan():
    internal = make_internal(
        '["Search for GDPR fine trends 2024", '
        '"Extract fine amounts by sector", '
        '"Compare with 2023 data"]'
    )
    goal = Goal(
        id="g1", target_belief_id="b1",
        description="Investigate GDPR fine trends",
        reason="High PE", status=GoalStatus.ACTIVE,
    )

    steps = asyncio.run(internal.plan(goal, "beliefs summary", ["brave_search"]))

    assert len(steps) == 3
    assert all(isinstance(s, str) for s in steps)


def test_abstract_preconditions():
    internal = make_internal('{"statement": "test", "tags": ["t"]}')

    # Too few beliefs
    beliefs = [make_belief(f"b{i}", f"test {i}", 0.8) for i in range(2)]
    result = asyncio.run(internal.abstract(beliefs, cycle=1))
    assert result is None

    # Low average confidence
    beliefs = [make_belief(f"b{i}", f"test {i}", 0.3) for i in range(4)]
    result = asyncio.run(internal.abstract(beliefs, cycle=1))
    assert result is None


def test_abstract_success():
    internal = make_internal(
        '{"statement": "Regulatory pressure on tech is increasing globally", '
        '"tags": ["regulation", "tech"]}'
    )
    beliefs = [
        make_belief("b1", "GDPR fines increasing", 0.8, ["gdpr"]),
        make_belief("b2", "AI regulation coming", 0.7, ["ai_reg"]),
        make_belief("b3", "Data privacy laws expanding", 0.9, ["privacy"]),
    ]

    result = asyncio.run(internal.abstract(beliefs, cycle=10))

    assert result is not None
    assert result.source == BeliefSource.ABSTRACTION
    assert result.parent_ids == ["b1", "b2", "b3"]
    # confidence = mean(0.8, 0.7, 0.9) * 0.9 = 0.8 * 0.9 = 0.72
    assert abs(result.confidence - 0.72) < 1e-3
