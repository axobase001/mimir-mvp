import json

from mimir.brain.sec_matrix import SECMatrix
from mimir.config import MimirConfig


def make_config() -> MimirConfig:
    return MimirConfig(sec_alpha=0.3, sec_warmup_cycles=3)


def test_basic_update_and_c_value():
    sec = SECMatrix(make_config())
    clusters = {"A", "B", "C"}

    # 10 cycles: A always observed with low PE, B never observed
    for cycle in range(1, 11):
        observed = {"A"}
        pe = 0.1  # low PE when A is observed
        sec.update(observed, clusters, pe, cycle)

    # A: always observed, d_obs tracks PE=0.1, d_not=0, but not_count=0 -> C=0
    assert sec.entries["A"].obs_count == 10
    assert sec.entries["A"].not_count == 0
    assert sec.get_c_value("A") == 0.0  # insufficient counterfactual

    # B: never observed, d_obs=0, d_not tracks PE=0.1
    assert sec.entries["B"].obs_count == 0
    assert sec.entries["B"].not_count == 10
    assert sec.get_c_value("B") == 0.0  # insufficient observations


def test_c_value_sign_with_mixed_observation():
    """Cluster observed in low-PE cycles should have positive C."""
    sec = SECMatrix(MimirConfig(sec_alpha=0.3, sec_warmup_cycles=0))
    clusters = {"useful", "useless"}

    for cycle in range(1, 21):
        if cycle <= 10:
            # First 10 cycles: observe "useful", PE is low
            sec.update({"useful"}, clusters, pe=0.1, cycle=cycle)
        else:
            # Next 10 cycles: observe "useless", PE is high
            sec.update({"useless"}, clusters, pe=0.8, cycle=cycle)

    # "useful": observed in low-PE cycles, absent in high-PE cycles
    # d_obs should be low, d_not should be high -> C = d_not - d_obs > 0
    c_useful = sec.get_c_value("useful")
    assert c_useful > 0, f"Expected positive C for 'useful', got {c_useful}"

    # "useless": observed in high-PE cycles, absent in low-PE cycles
    # d_obs should be high, d_not should be low -> C < 0
    c_useless = sec.get_c_value("useless")
    assert c_useless < 0, f"Expected negative C for 'useless', got {c_useless}"


def test_filter_warmup_allows():
    sec = SECMatrix(MimirConfig(sec_warmup_cycles=5))
    sec.entries["bad"] = __import__("mimir.types", fromlist=["SECEntry"]).SECEntry(
        cluster="bad", d_obs=0.9, d_not=0.1, obs_count=5, not_count=5
    )
    # During warmup, even negative C should be allowed
    assert sec.filter_action("bad", cycle=3) is True


def test_filter_positive_c_allows():
    from mimir.types import SECEntry
    sec = SECMatrix(MimirConfig(sec_warmup_cycles=0))
    sec.entries["good"] = SECEntry(
        cluster="good", d_obs=0.1, d_not=0.5, obs_count=5, not_count=5
    )
    # C = 0.5 - 0.1 = 0.4 > 0 -> allow
    assert sec.filter_action("good", cycle=10) is True


def test_filter_unknown_cluster_allows():
    sec = SECMatrix(MimirConfig(sec_warmup_cycles=0))
    assert sec.filter_action("never_seen", cycle=10) is True


def test_filter_negative_c_rejects_probabilistically():
    from mimir.types import SECEntry
    import random

    random.seed(42)
    sec = SECMatrix(MimirConfig(sec_warmup_cycles=0))
    sec.entries["bad"] = SECEntry(
        cluster="bad", d_obs=0.8, d_not=0.2, obs_count=5, not_count=5
    )
    # C = 0.2 - 0.8 = -0.6, this is the only negative cluster -> reject_prob = 1.0

    results = [sec.filter_action("bad", cycle=10) for _ in range(100)]
    # With reject_prob = 1.0, should always reject
    assert all(r is False for r in results)


def test_filter_probe_c_zero_high_coverage():
    from mimir.types import SECEntry
    import random

    random.seed(42)
    config = MimirConfig(sec_warmup_cycles=5, sec_probe_reject_rate=0.4)
    sec = SECMatrix(config)
    sec.entries["stuck"] = SECEntry(
        cluster="stuck", d_obs=0.3, d_not=0.0, obs_count=10, not_count=0
    )
    # C=0 (not_count < 2), coverage = 10/(15-5) = 1.0 >= 0.8 -> probe at 40%

    results = [sec.filter_action("stuck", cycle=15) for _ in range(1000)]
    reject_count = results.count(False)
    # Should be approximately 40% rejections
    assert 300 < reject_count < 500, f"Expected ~400 rejections, got {reject_count}"


def test_serialization_roundtrip():
    sec = SECMatrix(make_config())
    clusters = {"A", "B"}
    for cycle in range(1, 6):
        sec.update({"A"}, clusters, pe=0.2, cycle=cycle)

    data = sec.to_dict()
    json_str = json.dumps(data)
    restored = json.loads(json_str)

    sec2 = SECMatrix.from_dict(restored, make_config())

    assert sec2.entries["A"].obs_count == sec.entries["A"].obs_count
    assert sec2.entries["A"].d_obs == sec.entries["A"].d_obs
    assert sec2.entries["B"].not_count == sec.entries["B"].not_count
    assert abs(sec2.get_c_value("A") - sec.get_c_value("A")) < 1e-9
