import json

from mimir.brain.belief_graph import BeliefGraph
from mimir.dtypes import Belief, BeliefSource
from mimir.config import MimirConfig


def make_config() -> MimirConfig:
    return MimirConfig()


def make_seed_belief(bid: str, statement: str, confidence: float = 0.8,
                     tags: list[str] | None = None, cycle: int = 0) -> Belief:
    return Belief(
        id=bid,
        statement=statement,
        confidence=confidence,
        source=BeliefSource.SEED,
        created_at=cycle,
        last_updated=cycle,
        last_verified=cycle,
        tags=tags or [],
    )


def test_add_and_get():
    bg = BeliefGraph(make_config())
    b = make_seed_belief("b1", "GDPR fines exceeded $1M in 2024", tags=["gdpr"])
    bg.add_belief(b)

    got = bg.get_belief("b1")
    assert got is not None
    assert got.statement == "GDPR fines exceeded $1M in 2024"
    assert got.confidence == 0.8


def test_update_low_pe_preserves_confidence():
    bg = BeliefGraph(make_config())
    bg.add_belief(make_seed_belief("b1", "test", confidence=0.9))

    bg.update_belief("b1", new_confidence=0.9, pe=0.0, cycle=1)
    b = bg.get_belief("b1")
    assert b.confidence == 0.9  # PE=0 -> no change
    assert b.pe_history == [0.0]
    assert b.last_verified == 1


def test_update_high_pe_drops_confidence():
    bg = BeliefGraph(make_config())
    bg.add_belief(make_seed_belief("b1", "test", confidence=0.9))

    bg.update_belief("b1", new_confidence=0.4, pe=0.5, cycle=1)
    b = bg.get_belief("b1")
    # 0.9 * (1 - 0.5 * 0.3) = 0.9 * 0.85 = 0.765
    assert abs(b.confidence - 0.765) < 1e-9


def test_propagate_update():
    bg = BeliefGraph(make_config())
    bg.add_belief(make_seed_belief("parent", "A", confidence=0.9))
    bg.add_belief(make_seed_belief("child", "B depends on A", confidence=0.8))
    bg.add_dependency("parent", "child", weight=1.0)

    # Update parent with high PE
    bg.update_belief("parent", new_confidence=0.5, pe=0.6, cycle=1)
    affected = bg.propagate_update("parent")

    assert "child" in affected
    child = bg.get_belief("child")
    # child.confidence = 0.8 - (0.6 * 1.0 * 0.1) = 0.8 - 0.06 = 0.74
    assert abs(child.confidence - 0.74) < 1e-9


def test_decay_unverified():
    config = make_config()
    bg = BeliefGraph(config)
    bg.add_belief(make_seed_belief("obs", "observed", confidence=0.8,
                                    cycle=0))
    bg.add_belief(Belief(
        id="inf", statement="inferred", confidence=0.8,
        source=BeliefSource.INFERENCE,
        created_at=0, last_updated=0, last_verified=0,
    ))

    # Verify obs at cycle 5, leave inf unverified
    bg.update_belief("obs", new_confidence=0.8, pe=0.0, cycle=5)

    decayed = bg.decay_unverified(current_cycle=5)
    assert "inf" in decayed
    assert "obs" not in decayed

    inf = bg.get_belief("inf")
    # inference decays at 2x category rate: FACT=0.03, 0.8 - 0.03*2 = 0.74
    assert abs(inf.confidence - 0.74) < 1e-9


def test_prune_removes_low_confidence_leaf():
    bg = BeliefGraph(make_config())
    bg.add_belief(make_seed_belief("low", "low conf", confidence=0.03))
    bg.add_belief(make_seed_belief("ok", "ok conf", confidence=0.5))

    pruned = bg.prune()
    assert "low" in pruned
    assert "ok" not in pruned
    assert bg.get_belief("low") is None
    assert bg.get_belief("ok") is not None


def test_prune_preserves_belief_with_dependents():
    bg = BeliefGraph(make_config())
    bg.add_belief(make_seed_belief("parent", "parent", confidence=0.03))
    bg.add_belief(make_seed_belief("child", "child", confidence=0.5))
    bg.add_dependency("parent", "child")

    pruned = bg.prune()
    # parent has out_degree > 0, should not be pruned
    assert "parent" not in pruned
    assert bg.get_belief("parent") is not None


def test_get_high_pe_beliefs():
    bg = BeliefGraph(make_config())
    b = make_seed_belief("b1", "test", confidence=0.8)
    b.pe_history = [0.5, 0.6, 0.4, 0.5]
    bg.add_belief(b)

    # threshold=0.3, persistence=3 -> last 3 are [0.6, 0.4, 0.5], all > 0.3
    results = bg.get_high_pe_beliefs(threshold=0.3, min_persistence=3)
    assert len(results) == 1
    assert results[0].id == "b1"

    # threshold=0.5, persistence=3 -> last 3 are [0.6, 0.4, 0.5], 0.4 <= 0.5
    results = bg.get_high_pe_beliefs(threshold=0.5, min_persistence=3)
    assert len(results) == 0


def test_get_stale_beliefs():
    bg = BeliefGraph(make_config())
    bg.add_belief(make_seed_belief("fresh", "fresh", confidence=0.9, cycle=0))
    bg.add_belief(make_seed_belief("stale", "stale", confidence=0.9, cycle=0))

    bg.update_belief("fresh", new_confidence=0.9, pe=0.0, cycle=25)

    results = bg.get_stale_beliefs(current_cycle=25, staleness_threshold=20)
    assert len(results) == 1
    assert results[0].id == "stale"


def test_serialization_roundtrip():
    bg = BeliefGraph(make_config())
    b1 = make_seed_belief("b1", "GDPR fines", confidence=0.8, tags=["gdpr"])
    b2 = make_seed_belief("b2", "GDPR enforced", confidence=0.7, tags=["gdpr"])
    b1.pe_history = [0.1, 0.2, 0.3]
    bg.add_belief(b1)
    bg.add_belief(b2)
    bg.add_dependency("b1", "b2", weight=0.5)

    data = bg.to_dict()
    json_str = json.dumps(data)
    restored_data = json.loads(json_str)

    bg2 = BeliefGraph.from_dict(restored_data, make_config())

    rb1 = bg2.get_belief("b1")
    assert rb1 is not None
    assert rb1.statement == "GDPR fines"
    assert rb1.confidence == 0.8
    assert rb1.pe_history == [0.1, 0.2, 0.3]
    assert rb1.tags == ["gdpr"]

    rb2 = bg2.get_belief("b2")
    assert rb2 is not None

    # Check edge preserved
    assert bg2.graph.has_edge("b1", "b2")
    assert bg2.graph["b1"]["b2"]["weight"] == 0.5


def test_full_lifecycle():
    """Simulate 10 cycles: add beliefs, update, propagate, decay, prune."""
    config = make_config()
    bg = BeliefGraph(config)

    # 5 seed beliefs
    seeds = [
        make_seed_belief("b1", "GDPR fines > $1M in 2024", 0.8, ["gdpr"]),
        make_seed_belief("b2", "AI regulation increasing", 0.7, ["ai_reg"]),
        make_seed_belief("b3", "Cloud costs rising 20% YoY", 0.6, ["cloud"]),
        make_seed_belief("b4", "Zero trust adoption > 60%", 0.75, ["security"]),
        make_seed_belief("b5", "Remote work declining", 0.5, ["workforce"]),
    ]
    for s in seeds:
        bg.add_belief(s)
    bg.add_dependency("b1", "b2", weight=0.5)  # GDPR -> AI regulation

    # Simulate 10 cycles
    for cycle in range(1, 11):
        # Verify b1 every other cycle with varying PE
        if cycle % 2 == 0:
            pe = 0.1 if cycle < 6 else 0.5
            bg.update_belief("b1", new_confidence=0.8, pe=pe, cycle=cycle)
            bg.propagate_update("b1")

        # Verify b3 once at cycle 3 with high PE
        if cycle == 3:
            bg.update_belief("b3", new_confidence=0.3, pe=0.7, cycle=cycle)

        bg.decay_unverified(current_cycle=cycle)

    # b1: verified at cycles 2,4,6,8,10 -> PE history [0.1,0.1,0.5,0.5,0.5]
    b1 = bg.get_belief("b1")
    assert len(b1.pe_history) == 5
    assert b1.last_verified == 10

    # b5: never verified, decayed 10 times at 0.02 = 0.2 total
    b5 = bg.get_belief("b5")
    assert b5.confidence < 0.5  # 0.5 - 0.2 = 0.3

    # b3: verified once at cycle 3, then decayed 7 more times
    b3 = bg.get_belief("b3")
    assert b3.confidence < 0.6

    # Prune anything below threshold
    pruned = bg.prune()
    for pid in pruned:
        assert bg.get_belief(pid) is None
