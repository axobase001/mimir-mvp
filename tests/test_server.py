import asyncio
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from mimir.server.app import app
from mimir.brain.belief_graph import BeliefGraph
from mimir.brain.sec_matrix import SECMatrix
from mimir.brain.prediction import PredictionEngine
from mimir.brain.goal_generator import GoalGenerator
from mimir.brain.memory import Memory
from mimir.llm.client import LLMClient
from mimir.llm.internal import InternalLLM
from mimir.llm.external import ExternalLLM
from mimir.core.cycle import MimirCycle
from mimir.core.notifier import Notifier
from mimir.core.dedup import BeliefDeduplicator
from mimir.core.scheduler import BrainScheduler
from mimir.storage.user_db import UserDB
from mimir.storage.brain_store import BrainStore
from mimir.types import Belief, BeliefSource
from mimir.config import MimirConfig
from mimir.server.auth.jwt import configure as configure_jwt, create_token

from unittest.mock import AsyncMock
from dataclasses import dataclass, field as dc_field


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def get_client():
    """Set up a test client with auth, scheduler, and a pre-initialized user brain."""
    config = MimirConfig(
        llm_api_key="test",
        brave_api_key="",
        default_llm_key="test",
        default_brave_key="",
        jwt_secret="test-server-secret-key-32chars!",
    )

    configure_jwt("test-server-secret-key-32chars!", expire_hours=72)

    tmpdir = tempfile.mkdtemp()
    user_db = UserDB(db_path=str(Path(tmpdir) / "users.db"), jwt_secret=config.jwt_secret)
    brain_store = BrainStore(base_dir=str(Path(tmpdir) / "brains"))

    scheduler = BrainScheduler(config, user_db, brain_store)

    # Create a test user
    user = user_db.create_user("test@test.com", "password123")
    user_id = user["id"]
    token = create_token(user_id)

    # Start a brain for the user with test beliefs
    seeds = [
        {"statement": "GDPR fines exceeded $1M", "confidence": 0.8, "tags": ["gdpr"]},
        {"statement": "AI regulation increasing", "confidence": 0.7, "tags": ["ai_reg"]},
    ]
    _run(scheduler.start_brain(user_id, seeds))

    # Mock the LLM client to avoid real API calls
    state = scheduler.get_brain_state(user_id)
    state["llm_client"].complete = AsyncMock(return_value='["gdpr"]')

    # Attach to app
    app.state.user_db = user_db
    app.state.brain_store = brain_store
    app.state.scheduler = scheduler
    app.state.config = config
    app.state.max_users = 1000

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    return client, headers, scheduler, user_id


def test_dashboard():
    client, headers, scheduler, uid = get_client()
    r = client.get("/api/dashboard", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["belief_count"] == 2
    assert len(data["belief_graph"]["nodes"]) == 2


def test_dashboard_requires_auth():
    client, headers, _, _ = get_client()
    r = client.get("/api/dashboard")
    assert r.status_code == 401


def test_belief_detail():
    client, headers, _, _ = get_client()
    r = client.get("/api/beliefs/seed_000", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["statement"] == "GDPR fines exceeded $1M"


def test_add_belief():
    client, headers, scheduler, uid = get_client()
    state = scheduler.get_brain_state(uid)
    state["llm_client"].complete = AsyncMock(return_value='{"duplicate": false}')

    r = client.post("/api/beliefs", json={
        "statement": "New test belief",
        "tags": ["test"],
        "confidence": 0.6,
    }, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "created"
    assert len(state["belief_graph"].get_all_beliefs()) == 3


def test_delete_belief():
    client, headers, scheduler, uid = get_client()
    state = scheduler.get_brain_state(uid)
    r = client.delete("/api/beliefs/seed_001", headers=headers)
    assert r.status_code == 200
    assert state["belief_graph"].get_belief("seed_001") is None


def test_goals_crud():
    client, headers, _, _ = get_client()

    # List (empty)
    r = client.get("/api/goals", headers=headers)
    assert r.json()["goals"] == []

    # Add
    r = client.post("/api/goals", json={
        "description": "Research GDPR trends",
        "priority": 0.8,
    }, headers=headers)
    data = r.json()
    assert data["action"] == "created"
    gid = data["goal_id"]

    # List (one goal)
    r = client.get("/api/goals", headers=headers)
    assert len(r.json()["goals"]) == 1

    # Complete
    r = client.put(f"/api/goals/{gid}/complete", headers=headers)
    assert r.json()["action"] == "completed"


def test_root_serves_html():
    client, _, _, _ = get_client()
    r = client.get("/")
    assert r.status_code == 200
    assert "Mimir" in r.text


def test_auth_register_login():
    client, _, _, _ = get_client()

    # Register
    r = client.post("/api/auth/register", json={
        "email": "new@test.com",
        "password": "newpass123",
        "display_name": "New User",
    })
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["user"]["email"] == "new@test.com"

    # Login
    r = client.post("/api/auth/login", json={
        "email": "new@test.com",
        "password": "newpass123",
    })
    assert r.status_code == 200
    assert "token" in r.json()

    # Login with wrong password
    r = client.post("/api/auth/login", json={
        "email": "new@test.com",
        "password": "wrongpass",
    })
    assert r.status_code == 401
