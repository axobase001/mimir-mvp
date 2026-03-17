import asyncio
from unittest.mock import AsyncMock

from mimir.llm.client import LLMClient
from mimir.llm.external import ExternalLLM
from mimir.types import Belief, BeliefSource
from mimir.config import MimirConfig


def make_external(mock_response: str) -> ExternalLLM:
    client = LLMClient(api_key="test", base_url="http://test", model="test")
    client.complete = AsyncMock(return_value=mock_response)
    return ExternalLLM(client, MimirConfig())


def make_belief(bid: str, statement: str, confidence: float = 0.8) -> Belief:
    return Belief(
        id=bid, statement=statement, confidence=confidence,
        source=BeliefSource.SEED, created_at=0,
        last_updated=0, last_verified=0, tags=[],
    )


def test_intent_to_query():
    ext = make_external("GDPR fines 2024 trends")
    query = asyncio.run(ext.intent_to_query("GDPR fines exceeded $1M in 2024"))
    assert "GDPR" in query


def test_extract_beliefs_support():
    ext = make_external(
        '{"verdict": "support", "observed_confidence": 0.85, '
        '"extracted_facts": ["GDPR fines reached $1.2M"], '
        '"new_beliefs": [{"statement": "GDPR fines hit $1.2M in Q3 2024", '
        '"tags": ["gdpr"], "confidence": 0.9}]}'
    )
    belief = make_belief("b1", "GDPR fines exceeded $1M in 2024")

    result = asyncio.run(ext.extract_beliefs("search results here", belief))

    assert result["verdict"] == "support"
    assert result["observed_confidence"] == 0.85
    assert len(result["extracted_facts"]) == 1
    assert len(result["new_beliefs"]) == 1
    assert result["new_beliefs"][0]["statement"] == "GDPR fines hit $1.2M in Q3 2024"


def test_extract_beliefs_contradict():
    ext = make_external(
        '{"verdict": "contradict", "observed_confidence": 0.9, '
        '"extracted_facts": ["GDPR fines were only $500K"], '
        '"new_beliefs": []}'
    )
    belief = make_belief("b1", "GDPR fines exceeded $1M in 2024")

    result = asyncio.run(ext.extract_beliefs("search results", belief))

    assert result["verdict"] == "contradict"
    assert result["observed_confidence"] == 0.9


def test_extract_beliefs_malformed():
    ext = make_external("I don't understand the question")
    belief = make_belief("b1", "test")

    result = asyncio.run(ext.extract_beliefs("search results", belief))

    # Should return defaults
    assert result["verdict"] == "irrelevant"
    assert result["observed_confidence"] == 0.5
    assert result["new_beliefs"] == []


def test_summarize_cycle():
    ext = make_external("Cycle 5 saw GDPR fine data confirmed with low PE.")

    note = asyncio.run(ext.summarize_cycle({"cycle": 5, "pe": 0.1}))

    assert "Cycle" in note or "cycle" in note
    assert isinstance(note, str)
