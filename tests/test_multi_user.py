"""Tests for multi-user Brain isolation."""

import asyncio
import pytest

from mimir.config import MimirConfig
from mimir.storage.user_db import UserDB
from mimir.storage.brain_store import BrainStore
from mimir.core.scheduler import BrainScheduler
from mimir.server.auth.jwt import configure


def _run(coro):
    """Helper to run async code in tests, compatible with Python 3.13."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def config():
    return MimirConfig(
        llm_api_key="test-key",
        brave_api_key="test-brave",
        default_llm_key="test-key",
        default_brave_key="test-brave",
        jwt_secret="test-secret-12345678",
        scheduler_interval=60.0,
        inter_user_delay=0.0,
    )


@pytest.fixture
def user_db(tmp_path):
    return UserDB(db_path=str(tmp_path / "users.db"), jwt_secret="test-secret-12345678")


@pytest.fixture
def brain_store(tmp_path):
    return BrainStore(base_dir=str(tmp_path / "brains"))


@pytest.fixture(autouse=True)
def setup_jwt():
    configure("test-secret-12345678", expire_hours=72)
    yield


class TestMultiUserIsolation:
    def test_two_users_separate_brain_storage(self, user_db, brain_store):
        """Two users each have their own Brain directory."""
        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")

        brain_store.save_brain(u1["id"], {"test": "alice_data", "cycle_count": 1,
                                           "belief_graph": {"nodes": {}, "edges": [], "counter": 0},
                                           "sec_matrix": {"entries": {}},
                                           "memory": {"episodes": [], "procedures": []},
                                           "goals": {}, "usage_stats": {}})
        brain_store.save_brain(u2["id"], {"test": "bob_data", "cycle_count": 5,
                                           "belief_graph": {"nodes": {}, "edges": [], "counter": 0},
                                           "sec_matrix": {"entries": {}},
                                           "memory": {"episodes": [], "procedures": []},
                                           "goals": {}, "usage_stats": {}})

        # Load separately
        d1 = brain_store.load_brain(u1["id"])
        d2 = brain_store.load_brain(u2["id"])
        assert d1["test"] == "alice_data"
        assert d2["test"] == "bob_data"
        assert d1["cycle_count"] == 1
        assert d2["cycle_count"] == 5

    def test_brain_exists_isolation(self, user_db, brain_store):
        """One user's brain existence doesn't affect another."""
        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")

        assert not brain_store.brain_exists(u1["id"])
        assert not brain_store.brain_exists(u2["id"])

        brain_store.save_brain(u1["id"], {"cycle_count": 0,
                                           "belief_graph": {"nodes": {}, "edges": [], "counter": 0},
                                           "sec_matrix": {"entries": {}},
                                           "memory": {"episodes": [], "procedures": []},
                                           "goals": {}, "usage_stats": {}})

        assert brain_store.brain_exists(u1["id"])
        assert not brain_store.brain_exists(u2["id"])

    def test_delete_brain_doesnt_affect_other(self, user_db, brain_store):
        """Deleting one user's brain doesn't touch the other's."""
        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")

        state = {"cycle_count": 0,
                 "belief_graph": {"nodes": {}, "edges": [], "counter": 0},
                 "sec_matrix": {"entries": {}},
                 "memory": {"episodes": [], "procedures": []},
                 "goals": {}, "usage_stats": {}}
        brain_store.save_brain(u1["id"], state)
        brain_store.save_brain(u2["id"], state)

        brain_store.delete_brain(u1["id"])
        assert not brain_store.brain_exists(u1["id"])
        assert brain_store.brain_exists(u2["id"])

    def test_list_active_brains(self, user_db, brain_store):
        """list_active_brains returns only users with state files."""
        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")
        u3 = user_db.create_user("carol@test.com", "password789")

        state = {"cycle_count": 0,
                 "belief_graph": {"nodes": {}, "edges": [], "counter": 0},
                 "sec_matrix": {"entries": {}},
                 "memory": {"episodes": [], "procedures": []},
                 "goals": {}, "usage_stats": {}}
        brain_store.save_brain(u1["id"], state)
        brain_store.save_brain(u2["id"], state)

        active = brain_store.list_active_brains()
        assert u1["id"] in active
        assert u2["id"] in active
        assert u3["id"] not in active

    def test_usage_limits_per_user(self, user_db):
        """Each user has independent cycle limits."""
        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")

        # Use up alice's cycles
        for _ in range(3):
            user_db.update_usage(u1["id"], cycles_delta=1)

        assert not user_db.check_limit(u1["id"], "cycles")
        assert user_db.check_limit(u2["id"], "cycles")

    def test_scheduler_start_brain(self, config, user_db, brain_store):
        """Scheduler can start brains for two users independently."""
        scheduler = BrainScheduler(config, user_db, brain_store)

        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")

        seeds1 = [{"statement": "Alice belief 1", "confidence": 0.8, "tags": ["alice"]}]
        seeds2 = [{"statement": "Bob belief 1", "confidence": 0.7, "tags": ["bob"]}]

        _run(scheduler.start_brain(u1["id"], seeds1))
        _run(scheduler.start_brain(u2["id"], seeds2))

        # Both brains running
        status = scheduler.get_all_status()
        assert status["active_brains"] == 2

        # Check isolation: alice's brain has alice's beliefs
        e1 = scheduler.get_brain_engine(u1["id"])
        e2 = scheduler.get_brain_engine(u2["id"])
        assert e1 is not None
        assert e2 is not None

        b1 = [b.statement for b in e1.bg.get_all_beliefs()]
        b2 = [b.statement for b in e2.bg.get_all_beliefs()]
        assert "Alice belief 1" in b1
        assert "Alice belief 1" not in b2
        assert "Bob belief 1" in b2
        assert "Bob belief 1" not in b1

    def test_scheduler_stop_brain(self, config, user_db, brain_store):
        """Stopping one user's brain doesn't affect the other."""
        scheduler = BrainScheduler(config, user_db, brain_store)

        u1 = user_db.create_user("alice@test.com", "password123")
        u2 = user_db.create_user("bob@test.com", "password456")

        seeds1 = [{"statement": "Alice belief", "confidence": 0.8, "tags": ["alice"]}]
        seeds2 = [{"statement": "Bob belief", "confidence": 0.7, "tags": ["bob"]}]

        _run(scheduler.start_brain(u1["id"], seeds1))
        _run(scheduler.start_brain(u2["id"], seeds2))
        _run(scheduler.stop_brain(u1["id"]))

        assert scheduler.get_brain_engine(u1["id"]) is None
        assert scheduler.get_brain_engine(u2["id"]) is not None

    def test_brain_state_persistence(self, config, user_db, brain_store):
        """Brain state is saved on stop and can be restored."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        u1 = user_db.create_user("alice@test.com", "password123")
        seeds = [{"statement": "Test belief", "confidence": 0.8, "tags": ["test"]}]

        _run(scheduler.start_brain(u1["id"], seeds))
        assert brain_store.brain_exists(u1["id"])

        _run(scheduler.stop_brain(u1["id"]))
        assert brain_store.brain_exists(u1["id"])

        # Restore
        _run(scheduler.start_brain(u1["id"]))
        engine = scheduler.get_brain_engine(u1["id"])
        assert engine is not None
        beliefs = [b.statement for b in engine.bg.get_all_beliefs()]
        assert "Test belief" in beliefs
