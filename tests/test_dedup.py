import asyncio
from unittest.mock import AsyncMock

from mimir.core.dedup import BeliefDeduplicator
from mimir.brain.belief_graph import BeliefGraph
from mimir.llm.client import LLMClient
from mimir.types import Belief, BeliefSource
from mimir.config import MimirConfig


def make_belief(bid, statement, conf=0.8, tags=None):
    return Belief(
        id=bid, statement=statement, confidence=conf,
        source=BeliefSource.OBSERVATION, created_at=0,
        last_updated=0, last_verified=0, tags=tags or [],
    )


def make_dedup(mock_response):
    client = LLMClient(api_key="test", base_url="http://test", model="test")
    client.complete = AsyncMock(return_value=mock_response)
    return BeliefDeduplicator(client, MimirConfig())


def test_is_duplicate_true():
    dedup = make_dedup('{"duplicate": true, "match_id": "b1"}')
    existing = [make_belief("b1", "GDPR fines totaled 1.2B in 2024", tags=["gdpr"])]

    is_dup, match_id = asyncio.run(
        dedup.is_duplicate("GDPR fines reached €1.2 billion in 2024", existing)
    )
    assert is_dup is True
    assert match_id == "b1"


def test_is_duplicate_false():
    dedup = make_dedup('{"duplicate": false}')
    existing = [make_belief("b1", "GDPR fines totaled 1.2B", tags=["gdpr"])]

    is_dup, match_id = asyncio.run(
        dedup.is_duplicate("AI regulation increasing globally", existing)
    )
    assert is_dup is False
    assert match_id is None


def test_is_duplicate_empty_existing():
    dedup = make_dedup('{"duplicate": false}')

    is_dup, match_id = asyncio.run(dedup.is_duplicate("test", []))
    assert is_dup is False


def test_merge_beliefs():
    config = MimirConfig()
    bg = BeliefGraph(config)

    b1 = make_belief("b1", "GDPR fines 1.2B", 0.9, ["gdpr"])
    b2 = make_belief("b2", "GDPR fines reached 1.2B", 0.7, ["gdpr"])
    b3 = make_belief("b3", "Depends on GDPR data", 0.8, ["gdpr"])

    b1.pe_history = [0.1, 0.2]
    b2.pe_history = [0.3, 0.4]

    bg.add_belief(b1)
    bg.add_belief(b2)
    bg.add_belief(b3)
    bg.add_dependency("b2", "b3", weight=0.5)

    client = LLMClient(api_key="test", base_url="http://test", model="test")
    dedup = BeliefDeduplicator(client, config)

    primary_id = asyncio.run(dedup.merge_beliefs(["b1", "b2"], bg))

    assert primary_id == "b1"  # highest confidence
    assert bg.get_belief("b2") is None  # merged away
    assert bg.get_belief("b1") is not None
    assert len(bg.get_belief("b1").pe_history) == 4  # merged PE histories
    # Edge from b2→b3 redirected to b1→b3
    assert bg.graph.has_edge("b1", "b3")
