"""Tests for onboarding: init, templates, reset."""

import asyncio
import pytest

from mimir.config import MimirConfig
from mimir.storage.user_db import UserDB
from mimir.storage.brain_store import BrainStore
from mimir.core.scheduler import BrainScheduler
from mimir.server.routes.onboarding import TEMPLATES
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


class TestTemplates:
    def test_all_templates_exist(self):
        assert "financial_analyst" in TEMPLATES
        assert "developer" in TEMPLATES
        assert "researcher" in TEMPLATES
        assert "entrepreneur" in TEMPLATES
        assert "custom" in TEMPLATES

    def test_template_structure(self):
        for key, t in TEMPLATES.items():
            assert "name" in t
            assert "description" in t
            assert "seed_beliefs" in t
            assert isinstance(t["seed_beliefs"], list)

    def test_non_custom_templates_have_beliefs(self):
        for key, t in TEMPLATES.items():
            if key != "custom":
                assert len(t["seed_beliefs"]) >= 3
                for b in t["seed_beliefs"]:
                    assert "statement" in b
                    assert "tags" in b
                    assert "confidence" in b

    def test_custom_template_empty(self):
        assert len(TEMPLATES["custom"]["seed_beliefs"]) == 0


class TestOnboardingInit:
    def test_init_with_template(self, config, user_db, brain_store):
        """Initialize brain with a template."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        user = user_db.create_user("alice@test.com", "password123")

        seeds = TEMPLATES["developer"]["seed_beliefs"]
        _run(scheduler.start_brain(user["id"], seeds))

        assert brain_store.brain_exists(user["id"])
        engine = scheduler.get_brain_engine(user["id"])
        assert engine is not None
        assert len(engine.bg.get_all_beliefs()) == len(seeds)

    def test_init_with_custom_beliefs(self, config, user_db, brain_store):
        """Initialize brain with custom seed beliefs."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        user = user_db.create_user("alice@test.com", "password123")

        custom = [
            {"statement": "My custom belief", "confidence": 0.8, "tags": ["custom"]},
            {"statement": "Another belief", "confidence": 0.6, "tags": ["test"]},
        ]
        _run(scheduler.start_brain(user["id"], custom))

        engine = scheduler.get_brain_engine(user["id"])
        beliefs = [b.statement for b in engine.bg.get_all_beliefs()]
        assert "My custom belief" in beliefs
        assert "Another belief" in beliefs

    def test_init_fails_without_beliefs(self, config, user_db, brain_store):
        """Init should fail if no seed beliefs and no saved state."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        user = user_db.create_user("alice@test.com", "password123")

        with pytest.raises(ValueError, match="No seed beliefs"):
            _run(scheduler.start_brain(user["id"], []))

    def test_init_fails_for_nonexistent_user(self, config, user_db, brain_store):
        """Init should fail for a user that doesn't exist."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        with pytest.raises(ValueError, match="not found"):
            _run(scheduler.start_brain("nonexistent-user-id", [{"statement": "test", "tags": []}]))


class TestOnboardingReset:
    def test_reset_brain(self, config, user_db, brain_store):
        """Reset removes brain state."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        user = user_db.create_user("alice@test.com", "password123")

        seeds = [{"statement": "Test belief", "confidence": 0.8, "tags": ["test"]}]
        _run(scheduler.start_brain(user["id"], seeds))
        assert brain_store.brain_exists(user["id"])

        _run(scheduler.stop_brain(user["id"]))
        brain_store.delete_brain(user["id"])
        assert not brain_store.brain_exists(user["id"])

    def test_reset_and_reinit(self, config, user_db, brain_store):
        """After reset, user can reinitialize with different beliefs."""
        scheduler = BrainScheduler(config, user_db, brain_store)
        user = user_db.create_user("alice@test.com", "password123")

        # First init
        seeds1 = [{"statement": "Old belief", "confidence": 0.8, "tags": ["old"]}]
        _run(scheduler.start_brain(user["id"], seeds1))

        # Reset
        _run(scheduler.stop_brain(user["id"]))
        brain_store.delete_brain(user["id"])

        # Reinit with different beliefs
        seeds2 = [{"statement": "New belief", "confidence": 0.9, "tags": ["new"]}]
        _run(scheduler.start_brain(user["id"], seeds2))

        engine = scheduler.get_brain_engine(user["id"])
        beliefs = [b.statement for b in engine.bg.get_all_beliefs()]
        assert "New belief" in beliefs
        assert "Old belief" not in beliefs
