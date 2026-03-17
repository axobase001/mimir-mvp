from mimir.brain.belief_graph import BeliefGraph
from mimir.brain.sec_matrix import SECMatrix
from mimir.brain.goal_generator import GoalGenerator
from mimir.types import Belief, BeliefSource, GoalStatus, SECEntry
from mimir.config import MimirConfig


def make_config() -> MimirConfig:
    return MimirConfig(
        goal_pe_threshold=0.3,
        goal_pe_persistence=3,
        goal_staleness_threshold=10,
        max_active_goals=5,
    )


def setup_components(config: MimirConfig | None = None):
    config = config or make_config()
    bg = BeliefGraph(config)
    sec = SECMatrix(config)
    gg = GoalGenerator(config, bg, sec)
    return bg, sec, gg


def test_generate_investigate_goal():
    bg, sec, gg = setup_components()

    b = Belief(
        id="b1", statement="GDPR fines increasing",
        confidence=0.7, source=BeliefSource.OBSERVATION,
        created_at=0, last_updated=5, last_verified=5,
        pe_history=[0.5, 0.4, 0.6, 0.5],
        tags=["gdpr"],
    )
    bg.add_belief(b)

    # Give SEC a positive C for gdpr
    sec.entries["gdpr"] = SECEntry(
        cluster="gdpr", d_obs=0.1, d_not=0.5, obs_count=5, not_count=5
    )

    goals = gg.generate_goals(current_cycle=6)
    assert len(goals) == 1
    assert goals[0].target_belief_id == "b1"
    assert "Investigate" in goals[0].description
    assert goals[0].status == GoalStatus.ACTIVE
    assert goals[0].priority > 0


def test_generate_refresh_goal():
    bg, sec, gg = setup_components()

    b = Belief(
        id="b1", statement="Cloud costs rising",
        confidence=0.9, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["cloud"],
    )
    bg.add_belief(b)

    goals = gg.generate_goals(current_cycle=15)
    assert len(goals) == 1
    assert goals[0].target_belief_id == "b1"
    assert "Refresh" in goals[0].description
    # priority = 0.9 * (15 / 100) = 0.135
    assert abs(goals[0].priority - 0.135) < 1e-9


def test_no_duplicate_goals():
    bg, sec, gg = setup_components()

    b = Belief(
        id="b1", statement="test",
        confidence=0.7, source=BeliefSource.OBSERVATION,
        created_at=0, last_updated=3, last_verified=3,
        pe_history=[0.5, 0.5, 0.5],
        tags=["test"],
    )
    bg.add_belief(b)

    goals1 = gg.generate_goals(current_cycle=4)
    assert len(goals1) == 1

    goals2 = gg.generate_goals(current_cycle=5)
    assert len(goals2) == 0  # already has active goal for b1


def test_max_active_goals():
    config = make_config()
    config.max_active_goals = 2
    bg, sec, gg = setup_components(config)

    for i in range(5):
        b = Belief(
            id=f"b{i}", statement=f"belief {i}",
            confidence=0.7, source=BeliefSource.OBSERVATION,
            created_at=0, last_updated=3, last_verified=3,
            pe_history=[0.5, 0.5, 0.5],
            tags=["test"],
        )
        bg.add_belief(b)

    goals = gg.generate_goals(current_cycle=4)
    assert len(goals) == 2


def test_complete_and_abandon():
    bg, sec, gg = setup_components()

    b = Belief(
        id="b1", statement="test",
        confidence=0.7, source=BeliefSource.OBSERVATION,
        created_at=0, last_updated=3, last_verified=3,
        pe_history=[0.5, 0.5, 0.5],
        tags=["test"],
    )
    bg.add_belief(b)

    goals = gg.generate_goals(current_cycle=4)
    gid = goals[0].id

    gg.complete_goal(gid)
    assert gg.goals[gid].status == GoalStatus.COMPLETED

    # Now a new goal can be generated for b1
    b.pe_history.append(0.6)
    goals2 = gg.generate_goals(current_cycle=5)
    assert len(goals2) == 1

    gid2 = goals2[0].id
    gg.abandon_goal(gid2, "no longer relevant")
    assert gg.goals[gid2].status == GoalStatus.ABANDONED


def test_both_trigger_types():
    bg, sec, gg = setup_components()

    # High PE belief
    b1 = Belief(
        id="b1", statement="volatile belief",
        confidence=0.6, source=BeliefSource.OBSERVATION,
        created_at=0, last_updated=5, last_verified=5,
        pe_history=[0.5, 0.4, 0.5],
        tags=["volatile"],
    )
    bg.add_belief(b1)

    # Stale belief
    b2 = Belief(
        id="b2", statement="stale belief",
        confidence=0.9, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["stale"],
    )
    bg.add_belief(b2)

    goals = gg.generate_goals(current_cycle=15)
    assert len(goals) == 2

    types = {g.description.split(":")[0] for g in goals}
    assert "Investigate" in types
    assert "Refresh" in types
