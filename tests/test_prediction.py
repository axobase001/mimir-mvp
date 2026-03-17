from mimir.brain.prediction import PredictionEngine
from mimir.types import Belief, BeliefSource
from mimir.config import MimirConfig


def make_belief(bid: str, confidence: float) -> Belief:
    return Belief(
        id=bid, statement="test", confidence=confidence,
        source=BeliefSource.SEED, created_at=0,
        last_updated=0, last_verified=0,
    )


def test_predictions_match_current_confidence():
    engine = PredictionEngine(MimirConfig())
    beliefs = [make_belief("b1", 0.8), make_belief("b2", 0.3)]

    preds = engine.generate_predictions(beliefs)
    assert preds["b1"] == 0.8
    assert preds["b2"] == 0.3


def test_compute_pe():
    engine = PredictionEngine(MimirConfig())

    pe0 = engine.compute_pe("b1", predicted=0.8, observed=0.8)
    assert pe0.value == 0.0
    pe1 = engine.compute_pe("b1", predicted=0.8, observed=0.3)
    assert abs(pe1.value - 0.5) < 1e-9
    pe2 = engine.compute_pe("b1", predicted=0.2, observed=0.9)
    assert abs(pe2.value - 0.7) < 1e-9


def test_aggregate_pe():
    engine = PredictionEngine(MimirConfig())

    assert engine.compute_aggregate_pe({}) == 0.0
    assert engine.compute_aggregate_pe({"b1": 0.2}) == 0.2
    assert abs(engine.compute_aggregate_pe({"b1": 0.2, "b2": 0.4}) - 0.3) < 1e-9


def test_pe_symmetry():
    engine = PredictionEngine(MimirConfig())
    # PE should be the same regardless of direction
    pe1 = engine.compute_pe("b1", predicted=0.8, observed=0.3)
    pe2 = engine.compute_pe("b1", predicted=0.3, observed=0.8)
    assert abs(pe1.value - pe2.value) < 1e-9
