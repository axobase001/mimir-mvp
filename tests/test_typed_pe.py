"""Tests for typed PE system (Change #2).

Verifies:
- Three PE types (OBSERVATION, ACTION, INTERACTION) are correctly generated
- TypedPE supports float() conversion
- Weighted aggregate PE works correctly
- Backward compatibility with plain float dicts
"""

from mimir.brain.prediction import PredictionEngine
from mimir.types import Belief, BeliefSource, PEType, TypedPE
from mimir.config import MimirConfig


def make_belief(bid: str, confidence: float) -> Belief:
    return Belief(
        id=bid, statement="test", confidence=confidence,
        source=BeliefSource.SEED, created_at=0,
        last_updated=0, last_verified=0,
    )


def make_engine() -> PredictionEngine:
    return PredictionEngine(MimirConfig())


# ── TypedPE creation tests ──

def test_compute_pe_returns_typed_pe():
    engine = make_engine()
    result = engine.compute_pe("b1", predicted=0.8, observed=0.3)
    assert isinstance(result, TypedPE)
    assert result.pe_type == PEType.OBSERVATION
    assert abs(result.value - 0.5) < 1e-9


def test_compute_pe_with_explicit_type():
    engine = make_engine()
    result = engine.compute_pe(
        "b1", predicted=0.8, observed=0.3,
        pe_type=PEType.OBSERVATION, cycle=5,
    )
    assert result.pe_type == PEType.OBSERVATION
    assert result.cycle == 5
    assert result.source_id == "b1"


def test_compute_action_pe():
    engine = make_engine()
    result = engine.compute_action_pe(
        expected=0.0, actual=0.5, cycle=3, source_id="skill_search",
    )
    assert isinstance(result, TypedPE)
    assert result.pe_type == PEType.ACTION
    assert abs(result.value - 0.5) < 1e-9
    assert result.cycle == 3
    assert result.source_id == "skill_search"


def test_compute_interaction_pe():
    engine = make_engine()
    result = engine.compute_interaction_pe(
        expected=0.0, actual=0.7, cycle=10, source_id="chat_feedback",
    )
    assert isinstance(result, TypedPE)
    assert result.pe_type == PEType.INTERACTION
    assert abs(result.value - 0.7) < 1e-9


# ── Float conversion ──

def test_typed_pe_float_conversion():
    pe = TypedPE(pe_type=PEType.OBSERVATION, value=0.42)
    assert float(pe) == 0.42


def test_typed_pe_value_attribute():
    pe = TypedPE(pe_type=PEType.ACTION, value=0.33, cycle=5, source_id="test")
    assert pe.value == 0.33


# ── Weighted aggregate tests ──

def test_aggregate_with_typed_pe():
    engine = make_engine()
    pe_dict = {
        "b1": TypedPE(pe_type=PEType.OBSERVATION, value=0.4),
        "b2": TypedPE(pe_type=PEType.ACTION, value=0.4),
        "b3": TypedPE(pe_type=PEType.INTERACTION, value=0.4),
    }
    result = engine.compute_aggregate_pe(pe_dict)
    # weights: obs=1.0, action=0.5, interaction=0.3
    # weighted: 0.4*1.0 + 0.4*0.5 + 0.4*0.3 = 0.4 + 0.2 + 0.12 = 0.72
    # total_weight: 1.0 + 0.5 + 0.3 = 1.8
    # result: 0.72 / 1.8 = 0.4
    assert abs(result - 0.4) < 1e-9


def test_aggregate_observation_only():
    engine = make_engine()
    pe_dict = {
        "b1": TypedPE(pe_type=PEType.OBSERVATION, value=0.2),
        "b2": TypedPE(pe_type=PEType.OBSERVATION, value=0.4),
    }
    result = engine.compute_aggregate_pe(pe_dict)
    # Both weight 1.0: (0.2 + 0.4) / 2.0 = 0.3
    assert abs(result - 0.3) < 1e-9


def test_aggregate_weighted_asymmetry():
    """OBSERVATION PE weighs more than ACTION and INTERACTION."""
    engine = make_engine()
    # Same value, different types
    obs = TypedPE(pe_type=PEType.OBSERVATION, value=1.0)
    act = TypedPE(pe_type=PEType.ACTION, value=1.0)
    inter = TypedPE(pe_type=PEType.INTERACTION, value=1.0)

    # Observation-only
    agg_obs = engine.compute_aggregate_pe({"b1": obs})
    # Action-only
    agg_act = engine.compute_aggregate_pe({"b1": act})
    # Interaction-only
    agg_inter = engine.compute_aggregate_pe({"b1": inter})

    # All should be 1.0 (single entry, weight cancels out)
    assert abs(agg_obs - 1.0) < 1e-9
    assert abs(agg_act - 1.0) < 1e-9
    assert abs(agg_inter - 1.0) < 1e-9

    # But in aggregate, observation pulls harder
    mixed = engine.compute_aggregate_pe({
        "obs": TypedPE(pe_type=PEType.OBSERVATION, value=0.8),
        "act": TypedPE(pe_type=PEType.ACTION, value=0.2),
    })
    # (0.8*1.0 + 0.2*0.5) / (1.0+0.5) = 0.9/1.5 = 0.6
    assert abs(mixed - 0.6) < 1e-9


# ── Backward compatibility tests ──

def test_aggregate_with_plain_floats():
    """Existing code passing dict[str, float] should still work."""
    engine = make_engine()
    pe_dict = {"b1": 0.2, "b2": 0.4}
    result = engine.compute_aggregate_pe(pe_dict)
    assert abs(result - 0.3) < 1e-9


def test_aggregate_empty():
    engine = make_engine()
    assert engine.compute_aggregate_pe({}) == 0.0


def test_aggregate_mixed_types():
    """Mix of TypedPE and plain float values."""
    engine = make_engine()
    pe_dict = {
        "b1": TypedPE(pe_type=PEType.OBSERVATION, value=0.4),
        "b2": 0.4,  # plain float
    }
    result = engine.compute_aggregate_pe(pe_dict)
    # obs: 0.4*1.0, float: 0.4*1.0 -> (0.4+0.4) / 2.0 = 0.4
    assert abs(result - 0.4) < 1e-9


# ── PE symmetry still holds ──

def test_pe_symmetry_with_typed():
    engine = make_engine()
    pe1 = engine.compute_pe("b1", predicted=0.8, observed=0.3)
    pe2 = engine.compute_pe("b1", predicted=0.3, observed=0.8)
    assert abs(pe1.value - pe2.value) < 1e-9


# ── Predictions still work ──

def test_predictions_unchanged():
    engine = make_engine()
    beliefs = [make_belief("b1", 0.8), make_belief("b2", 0.3)]
    preds = engine.generate_predictions(beliefs)
    assert preds["b1"] == 0.8
    assert preds["b2"] == 0.3
