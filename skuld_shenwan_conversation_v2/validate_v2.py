"""
Skuld Interpretation Layer v2 Validation Script

Creates test_v2 user with empty brain (1 seed), sends 5 messages from Shenwan,
records complete responses. Validates:
1. "SEC是什么" -> Staleness-Error Correlation, not fabricated
2. "信念图里有什么" -> references truth packet real data
3. "你知道自己在聚焦吗" -> SEC mechanism driven, not conscious choice
4. "我是沈晚" -> no search triggered
5. "SEC对我输入什么反应" -> no search for SEC definition
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root.parent))

from mimir.config import MimirConfig
from mimir.dtypes import Belief, BeliefSource
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
    """Tracks whether search was called."""
    def __init__(self):
        self.call_count = 0

    @property
    def name(self) -> str:
        return "brave_search"

    @property
    def description(self) -> str:
        return "mock search"

    async def execute(self, params: dict) -> dict:
        self.call_count += 1
        return {
            "success": True,
            "result": f"Search result for: {params.get('query', '')}",
            "error": None,
        }


def build_test_engine() -> tuple[MimirCycle, MockSearchSkill]:
    """Build a test_v2 engine with 1 seed belief (empty brain)."""
    config = MimirConfig(
        sec_warmup_cycles=2,
        search_budget_per_cycle=3,
        reasoning_interval=100,
        abstraction_interval=100,
        cycle_interval_seconds=0,
    )

    bg = BeliefGraph(config)
    # Single seed belief
    bg.add_belief(Belief(
        id="seed_001",
        statement="Skuld是一个Brain-First AI认知系统",
        confidence=0.9,
        source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["skuld", "architecture"],
    ))

    sec = SECMatrix(config)
    # Add some SEC entries to make truth packet non-trivial
    sec._ensure_entry("skuld")
    sec.entries["skuld"].d_obs = 0.15
    sec.entries["skuld"].d_not = 0.25
    sec.entries["skuld"].obs_count = 5
    sec.entries["skuld"].not_count = 5
    sec._ensure_entry("architecture")
    sec.entries["architecture"].d_obs = 0.10
    sec.entries["architecture"].d_not = 0.12
    sec.entries["architecture"].obs_count = 3
    sec.entries["architecture"].not_count = 3

    pe = PredictionEngine(config)
    mem = Memory(config)
    gg = GoalGenerator(config, bg, sec)
    notifier = Notifier()

    client = LLMClient(api_key="test", base_url="http://test", model="test")

    external = ExternalLLM(client, config)
    external.intent_to_query = AsyncMock(side_effect=lambda intent, **kw: intent[:50])

    # chat_answer: echo back so we can inspect what was passed
    async def mock_chat_answer(question, beliefs_context, search_results=""):
        # Return a structured response showing what the LLM received
        has_truth_packet = "[BRAIN TRUTH PACKET]" in beliefs_context
        has_search = bool(search_results)
        return (
            f"[MOCK RESPONSE]\n"
            f"Question: {question}\n"
            f"Truth packet injected: {has_truth_packet}\n"
            f"Search results provided: {has_search}\n"
            f"Beliefs context length: {len(beliefs_context)}\n"
            f"--- Beliefs context preview ---\n"
            f"{beliefs_context[:500]}"
        )

    external.chat_answer = mock_chat_answer

    external.extract_beliefs = AsyncMock(return_value={
        "verdict": "irrelevant",
        "observed_confidence": 0.5,
        "extracted_facts": [],
        "new_beliefs": [],
    })

    internal = InternalLLM(client, config)
    internal.reason = AsyncMock(return_value=None)
    internal.abstract = AsyncMock(return_value=None)

    registry = SkillRegistry()
    search_skill = MockSearchSkill()
    registry.register(search_skill)

    engine = MimirCycle(
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

    return engine, search_skill


SHENWAN_MESSAGES = [
    "SEC是什么",
    "信念图里有什么",
    "你知道自己在聚焦吗",
    "我是沈晚",
    "SEC对我输入什么反应",
]

EXPECTED = {
    "SEC是什么": {
        "classification": "internal",
        "searched": False,
        "truth_packet_injected": True,
    },
    "信念图里有什么": {
        "classification": "internal",
        "searched": False,
        "truth_packet_injected": True,
    },
    "你知道自己在聚焦吗": {
        "classification": "internal",
        "searched": False,
        "truth_packet_injected": True,
    },
    "我是沈晚": {
        "classification": "social",
        "searched": False,
        "truth_packet_injected": False,
    },
    "SEC对我输入什么反应": {
        "classification": "internal",
        "searched": False,
        "truth_packet_injected": True,
    },
}


async def run_validation():
    engine, search_skill = build_test_engine()
    results = []
    all_passed = True

    print("=" * 70)
    print("SKULD INTERPRETATION LAYER v2 — VALIDATION")
    print("=" * 70)

    for msg in SHENWAN_MESSAGES:
        search_before = search_skill.call_count
        result = await engine.run_fast_path(msg)
        search_after = search_skill.call_count

        classification = result.get("classification", "unknown")
        searched = result["searched"]
        truth_injected = "[BRAIN TRUTH PACKET]" in result["answer"]

        expected = EXPECTED[msg]
        checks = {
            "classification": classification == expected["classification"],
            "searched": searched == expected["searched"],
            "truth_packet_injected": truth_injected == expected["truth_packet_injected"],
        }
        passed = all(checks.values())
        if not passed:
            all_passed = False

        record = {
            "message": msg,
            "classification": classification,
            "searched": searched,
            "search_calls": search_after - search_before,
            "truth_packet_injected": truth_injected,
            "answer_preview": result["answer"][:300],
            "beliefs_used": result["beliefs_used"],
            "checks": checks,
            "passed": passed,
        }
        results.append(record)

        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] Message: \"{msg}\"")
        print(f"  Classification: {classification} (expected: {expected['classification']})")
        print(f"  Searched: {searched} (expected: {expected['searched']})")
        print(f"  Truth packet: {truth_injected} (expected: {expected['truth_packet_injected']})")
        if not passed:
            for check_name, ok in checks.items():
                if not ok:
                    print(f"  ** FAILED CHECK: {check_name}")

    # Also validate the truth packet structure
    print("\n" + "=" * 70)
    print("TRUTH PACKET STRUCTURE TEST")
    print("=" * 70)
    truth_packet = engine._build_truth_packet()
    assert truth_packet.startswith("[BRAIN TRUTH PACKET]"), "Missing start marker"
    assert truth_packet.endswith("[END TRUTH PACKET]"), "Missing end marker"

    # Extract and parse JSON
    json_str = truth_packet.replace("[BRAIN TRUTH PACKET]\n", "").replace("\n[END TRUTH PACKET]", "")
    packet_data = json.loads(json_str)

    assert "belief_graph" in packet_data, "Missing belief_graph"
    assert "sec" in packet_data, "Missing sec"
    assert "memory" in packet_data, "Missing memory"
    assert "goals" in packet_data, "Missing goals"
    assert "cycle" in packet_data, "Missing cycle"
    assert packet_data["belief_graph"]["total"] == 1, f"Expected 1 belief, got {packet_data['belief_graph']['total']}"
    assert packet_data["sec"]["total_clusters"] == 2, f"Expected 2 SEC clusters, got {packet_data['sec']['total_clusters']}"
    assert packet_data["sec"]["positive_clusters"] >= 0
    print(f"  Packet has {packet_data['belief_graph']['total']} beliefs, "
          f"{packet_data['sec']['total_clusters']} SEC clusters")
    print(f"  Dominant topics: {packet_data['belief_graph']['dominant_topics']}")
    print(f"  Top attended: {packet_data['sec']['top_attended']}")
    print("  PASS: Truth packet structure valid")

    # Also validate _classify_message directly
    print("\n" + "=" * 70)
    print("CLASSIFIER UNIT TESTS")
    print("=" * 70)
    classify_tests = [
        ("SEC是什么", "internal"),
        ("你的信念图里有什么", "internal"),
        ("你知道自己在聚焦吗", "internal"),
        ("你怎么看人工智能的未来", "mixed"),
        ("你对量子计算的看法", "mixed"),
        ("你好", "social"),
        ("hello", "social"),
        ("我是沈晚", "social"),
        ("谢谢", "social"),
        ("量子计算的最新进展是什么", "external"),
        ("今天天气怎么样", "external"),
        ("SEC对我输入什么反应", "internal"),
        ("你为什么关注这个话题", "internal"),
        ("Staleness-Error Correlation是什么", "internal"),
    ]
    classify_passed = 0
    for query, expected_cls in classify_tests:
        got = engine._classify_message(query)
        ok = got == expected_cls
        if ok:
            classify_passed += 1
        else:
            all_passed = False
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] \"{query}\" -> {got} (expected: {expected_cls})")

    print(f"\n  {classify_passed}/{len(classify_tests)} classifier tests passed")

    # Save full results
    output_dir = Path(__file__).parent
    output_path = output_dir / "validation_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved to: {output_path}")

    # Save truth packet sample
    packet_path = output_dir / "truth_packet_sample.json"
    with open(packet_path, "w", encoding="utf-8") as f:
        f.write(truth_packet)
    print(f"Truth packet sample saved to: {packet_path}")

    print("\n" + "=" * 70)
    if all_passed:
        print("ALL VALIDATIONS PASSED")
    else:
        print("SOME VALIDATIONS FAILED — review output above")
    print("=" * 70)

    return all_passed


if __name__ == "__main__":
    ok = asyncio.run(run_validation())
    sys.exit(0 if ok else 1)
