"""Tests for belief category system (Change #1).

Verifies:
- Four belief types have different decay rates
- Four belief types have different PE sensitivity
- Four belief types have different prune thresholds
- Backward compatibility (default category = FACT)
- Serialization roundtrip preserves category
"""

import json

from mimir.brain.belief_graph import BeliefGraph
from mimir.types import Belief, BeliefCategory, BeliefSource
from mimir.config import MimirConfig


def make_config() -> MimirConfig:
    return MimirConfig()


def make_belief(
    bid: str,
    statement: str,
    confidence: float = 0.8,
    category: BeliefCategory = BeliefCategory.FACT,
    tags: list[str] | None = None,
    cycle: int = 0,
) -> Belief:
    return Belief(
        id=bid,
        statement=statement,
        confidence=confidence,
        source=BeliefSource.SEED,
        created_at=cycle,
        last_updated=cycle,
        last_verified=cycle,
        tags=tags or [],
        category=category,
    )


# ── Decay rate tests ──

def test_fact_decay_rate():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "fact belief", 0.8, BeliefCategory.FACT)
    bg.add_belief(b)
    bg.decay_unverified(current_cycle=5)
    # FACT decay = 0.03
    assert abs(b.confidence - (0.8 - 0.03)) < 1e-9


def test_preference_decay_rate():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "preference belief", 0.8, BeliefCategory.PREFERENCE)
    bg.add_belief(b)
    bg.decay_unverified(current_cycle=5)
    # PREFERENCE decay = 0.005
    assert abs(b.confidence - (0.8 - 0.005)) < 1e-9


def test_procedure_decay_rate():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "procedure belief", 0.8, BeliefCategory.PROCEDURE)
    bg.add_belief(b)
    bg.decay_unverified(current_cycle=5)
    # PROCEDURE decay = 0.01
    assert abs(b.confidence - (0.8 - 0.01)) < 1e-9


def test_hypothesis_decay_rate():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "hypothesis belief", 0.8, BeliefCategory.HYPOTHESIS)
    bg.add_belief(b)
    bg.decay_unverified(current_cycle=5)
    # HYPOTHESIS decay = 0.05
    assert abs(b.confidence - (0.8 - 0.05)) < 1e-9


def test_inference_double_decay_with_category():
    """Inference-sourced beliefs still decay at 2x their category rate."""
    config = make_config()
    bg = BeliefGraph(config)
    b = Belief(
        id="b1", statement="inferred hypothesis", confidence=0.8,
        source=BeliefSource.INFERENCE, created_at=0,
        last_updated=0, last_verified=0,
        category=BeliefCategory.HYPOTHESIS,
    )
    bg.add_belief(b)
    bg.decay_unverified(current_cycle=5)
    # HYPOTHESIS decay = 0.05, doubled for inference = 0.10
    assert abs(b.confidence - (0.8 - 0.10)) < 1e-9


def test_different_categories_decay_differently():
    """All four categories decay at different rates in the same cycle."""
    config = make_config()
    bg = BeliefGraph(config)

    beliefs = {
        "fact": make_belief("bf", "fact", 0.8, BeliefCategory.FACT),
        "pref": make_belief("bp", "pref", 0.8, BeliefCategory.PREFERENCE),
        "proc": make_belief("br", "proc", 0.8, BeliefCategory.PROCEDURE),
        "hypo": make_belief("bh", "hypo", 0.8, BeliefCategory.HYPOTHESIS),
    }
    for b in beliefs.values():
        bg.add_belief(b)

    bg.decay_unverified(current_cycle=5)

    confs = {k: b.confidence for k, b in beliefs.items()}
    # preference decays slowest, hypothesis fastest
    assert confs["pref"] > confs["proc"] > confs["fact"] > confs["hypo"]


# ── PE sensitivity tests ──

def test_fact_pe_sensitivity():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "fact", 0.9, BeliefCategory.FACT)
    bg.add_belief(b)
    bg.update_belief("b1", new_confidence=0.5, pe=0.5, cycle=1)
    # sensitivity=1.0: 0.9 * (1 - 0.5 * 0.3 * 1.0) = 0.9 * 0.85 = 0.765
    assert abs(b.confidence - 0.765) < 1e-9


def test_preference_pe_sensitivity():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "pref", 0.9, BeliefCategory.PREFERENCE)
    bg.add_belief(b)
    bg.update_belief("b1", new_confidence=0.5, pe=0.5, cycle=1)
    # sensitivity=0.3: 0.9 * (1 - 0.5 * 0.3 * 0.3) = 0.9 * 0.955 = 0.8595
    assert abs(b.confidence - 0.8595) < 1e-9


def test_hypothesis_pe_sensitivity():
    config = make_config()
    bg = BeliefGraph(config)
    b = make_belief("b1", "hypo", 0.9, BeliefCategory.HYPOTHESIS)
    bg.add_belief(b)
    bg.update_belief("b1", new_confidence=0.5, pe=0.5, cycle=1)
    # sensitivity=1.5: 0.9 * (1 - 0.5 * 0.3 * 1.5) = 0.9 * 0.775 = 0.6975
    assert abs(b.confidence - 0.6975) < 1e-9


def test_pe_sensitivity_varies_by_category():
    """Same PE applied to different categories produces different confidence drops."""
    config = make_config()

    results = {}
    for cat in BeliefCategory:
        bg = BeliefGraph(config)
        b = make_belief("b1", f"{cat.value}", 0.9, cat)
        bg.add_belief(b)
        bg.update_belief("b1", new_confidence=0.5, pe=0.5, cycle=1)
        results[cat.value] = b.confidence

    # HYPOTHESIS should drop most (highest sensitivity)
    # PREFERENCE should drop least (lowest sensitivity)
    assert results["preference"] > results["procedure"] > results["fact"] > results["hypothesis"]


# ── Prune threshold tests ──

def test_fact_prune_threshold():
    config = make_config()
    bg = BeliefGraph(config)
    # FACT threshold = 0.05
    b_below = make_belief("b1", "fact below", 0.04, BeliefCategory.FACT)
    b_above = make_belief("b2", "fact above", 0.06, BeliefCategory.FACT)
    bg.add_belief(b_below)
    bg.add_belief(b_above)
    pruned = bg.prune()
    assert "b1" in pruned
    assert "b2" not in pruned


def test_preference_prune_threshold():
    config = make_config()
    bg = BeliefGraph(config)
    # PREFERENCE threshold = 0.2
    b_below = make_belief("b1", "pref below", 0.15, BeliefCategory.PREFERENCE)
    b_above = make_belief("b2", "pref above", 0.25, BeliefCategory.PREFERENCE)
    bg.add_belief(b_below)
    bg.add_belief(b_above)
    pruned = bg.prune()
    assert "b1" in pruned
    assert "b2" not in pruned


def test_hypothesis_prune_threshold():
    config = make_config()
    bg = BeliefGraph(config)
    # HYPOTHESIS threshold = 0.03
    b_below = make_belief("b1", "hypo below", 0.02, BeliefCategory.HYPOTHESIS)
    b_above = make_belief("b2", "hypo above", 0.04, BeliefCategory.HYPOTHESIS)
    bg.add_belief(b_below)
    bg.add_belief(b_above)
    pruned = bg.prune()
    assert "b1" in pruned
    assert "b2" not in pruned


def test_different_categories_pruned_at_different_thresholds():
    """A confidence of 0.1 prunes PREFERENCE but not FACT or HYPOTHESIS."""
    config = make_config()
    bg = BeliefGraph(config)
    # 0.1 > 0.05 (FACT threshold) -> not pruned
    # 0.1 < 0.2 (PREFERENCE threshold) -> pruned
    # 0.1 > 0.03 (HYPOTHESIS threshold) -> not pruned
    bg.add_belief(make_belief("bf", "fact", 0.1, BeliefCategory.FACT))
    bg.add_belief(make_belief("bp", "pref", 0.1, BeliefCategory.PREFERENCE))
    bg.add_belief(make_belief("bh", "hypo", 0.1, BeliefCategory.HYPOTHESIS))
    pruned = bg.prune()
    assert "bp" in pruned
    assert "bf" not in pruned
    assert "bh" not in pruned


# ── Backward compatibility tests ──

def test_default_category_is_fact():
    b = Belief(
        id="b1", statement="test", confidence=0.8,
        source=BeliefSource.SEED, created_at=0,
        last_updated=0, last_verified=0,
    )
    assert b.category == BeliefCategory.FACT


def test_serialization_roundtrip_with_category():
    config = make_config()
    bg = BeliefGraph(config)
    bg.add_belief(make_belief("b1", "fact", 0.8, BeliefCategory.FACT, ["t1"]))
    bg.add_belief(make_belief("b2", "hypo", 0.7, BeliefCategory.HYPOTHESIS, ["t2"]))
    bg.add_belief(make_belief("b3", "pref", 0.6, BeliefCategory.PREFERENCE, ["t3"]))

    data = bg.to_dict()
    json_str = json.dumps(data)
    restored_data = json.loads(json_str)

    bg2 = BeliefGraph.from_dict(restored_data, config)

    assert bg2.get_belief("b1").category == BeliefCategory.FACT
    assert bg2.get_belief("b2").category == BeliefCategory.HYPOTHESIS
    assert bg2.get_belief("b3").category == BeliefCategory.PREFERENCE


def test_from_dict_missing_category_defaults_to_fact():
    """Old serialized data without category field should default to FACT."""
    config = make_config()
    data = {
        "nodes": {
            "b1": {
                "id": "b1",
                "statement": "old belief",
                "confidence": 0.8,
                "source": "seed",
                "created_at": 0,
                "last_updated": 0,
                "last_verified": 0,
                "pe_history": [],
                "tags": [],
                "parent_ids": [],
                # No "category" key
            }
        },
        "edges": [],
        "counter": 1,
    }
    bg = BeliefGraph.from_dict(data, config)
    assert bg.get_belief("b1").category == BeliefCategory.FACT
