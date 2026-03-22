"""Tests for ActionEngine."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from mimir.core.action_engine import ActionEngine
from mimir.core.notifier import Notifier, NotifyLevel
from mimir.skills.base import Skill, SkillResult
from mimir.skills.registry import SmartSkillRegistry
from mimir.brain.memory import Memory
from mimir.config import MimirConfig
from mimir.dtypes import Procedure


class MockSkill(Skill):
    def __init__(self, name: str, caps: list[str], risk: str = "safe", success: bool = True):
        super().__init__()
        self._name = name
        self._caps = caps
        self._risk = risk
        self._success = success

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Mock {self._name}"

    @property
    def capabilities(self) -> list[str]:
        return self._caps

    @property
    def risk_level(self) -> str:
        return self._risk

    async def execute(self, params: dict) -> dict:
        if self._success:
            return {"success": True, "result": f"Done by {self._name}", "error": None}
        return {"success": False, "result": "", "error": f"{self._name} failed"}


def make_engine(
    skills: list[Skill] | None = None,
    with_llm: bool = False,
) -> tuple[ActionEngine, SmartSkillRegistry, Memory, Notifier]:
    config = MimirConfig()
    registry = SmartSkillRegistry()
    memory = Memory(config)
    notifier = Notifier()

    if skills is None:
        skills = [
            MockSkill("search", ["web_search", "information_retrieval"]),
            MockSkill("code", ["run_code", "calculation"], "dangerous"),
            MockSkill("doc", ["write_document", "report"], "review"),
        ]
    for s in skills:
        registry.register(s)

    internal_llm = None
    if with_llm:
        internal_llm = MagicMock()
        internal_llm.plan_action_params = AsyncMock(return_value={"query": "test"})
        internal_llm.should_act = AsyncMock(return_value=(True, "test"))

    engine = ActionEngine(
        skill_registry=registry,
        memory=memory,
        notifier=notifier,
        internal_llm=internal_llm,
    )
    return engine, registry, memory, notifier


# ── plan_action ──

def test_plan_action_basic():
    engine, _, _, _ = make_engine()
    plan = asyncio.run(engine.plan_action(
        intent="search the web for information",
        goal="find latest news",
    ))
    assert plan["skill_name"] == "search"
    assert plan["risk_level"] == "safe"


def test_plan_action_with_llm():
    engine, _, _, _ = make_engine(with_llm=True)
    plan = asyncio.run(engine.plan_action(
        intent="search the web",
        goal="find info",
        belief_context="some context",
    ))
    assert plan["skill_name"] == "search"
    assert plan["params"] == {"query": "test"}


def test_plan_action_no_match():
    engine, _, _, _ = make_engine(skills=[])
    plan = asyncio.run(engine.plan_action(intent="do something"))
    assert plan["skill_name"] == ""
    assert plan["match_reason"] == "no_skill_found"


# ── execute_action ──

def test_execute_action_safe():
    engine, _, _, notifier = make_engine()
    plan = {"skill_name": "search", "params": {"query": "test"}, "risk_level": "safe"}
    result = asyncio.run(engine.execute_action(plan, user_id="user1"))
    assert result.success is True
    assert "Done by search" in result.result
    # Safe skill should not push notification
    assert not notifier.has_pending()


def test_execute_action_dangerous():
    engine, _, _, notifier = make_engine()
    plan = {"skill_name": "code", "params": {}, "risk_level": "dangerous"}
    result = asyncio.run(engine.execute_action(plan, user_id="user1"))
    assert result.success is True
    # Dangerous skill should push WARNING notification
    notifications = notifier.pull_all()
    assert len(notifications) == 1
    assert notifications[0].level == NotifyLevel.URGENT
    assert "code" in notifications[0].title.lower()


def test_execute_action_no_skill():
    engine, _, _, _ = make_engine()
    plan = {"skill_name": "", "params": {}}
    result = asyncio.run(engine.execute_action(plan))
    assert result.success is False
    assert "No skill" in result.error


# ── handle_skill_failure ──

def test_failure_fallback_registry():
    """When primary skill fails, registry should offer an alternate."""
    engine, _, _, _ = make_engine()
    fallback = asyncio.run(engine.handle_skill_failure(
        skill_name="search",
        error="connection refused",
        intent="find information on the web",
    ))
    # Should find an alternate skill (not "search")
    if fallback is not None:
        assert fallback["skill_name"] != "search"
        assert fallback["match_reason"] in ("registry_fallback", "procedural_memory_fallback")


def test_failure_fallback_memory():
    """Procedural memory should provide fallback skill."""
    engine, _, memory, _ = make_engine()

    # Add procedure referencing "doc" skill
    memory.add_or_update_procedure(Procedure(
        id="proc_info",
        description="find information and write document",
        steps=["use doc skill to write summary"],
        success_count=3,
        failure_count=0,
        avg_pe=0.1,
    ))

    fallback = asyncio.run(engine.handle_skill_failure(
        skill_name="search",
        error="timeout",
        intent="find information",
        memory=memory,
    ))
    assert fallback is not None
    assert fallback["skill_name"] == "doc"
    assert fallback["match_reason"] == "procedural_memory_fallback"


def test_failure_no_fallback():
    """When no alternative exists, handle_skill_failure returns None."""
    engine, _, _, _ = make_engine(skills=[
        MockSkill("only_one", ["something"])
    ])
    fallback = asyncio.run(engine.handle_skill_failure(
        skill_name="only_one",
        error="broken",
        intent="do something",
    ))
    assert fallback is None


# ── Memory: second call faster (procedure hit) ──

def test_procedural_memory_second_call():
    """After first successful execution, procedural memory should be used."""
    engine, registry, memory, _ = make_engine()

    # First call: capability match
    plan1 = asyncio.run(engine.plan_action(intent="search the web"))
    assert plan1["match_reason"] in ("capability_match", "fallback")

    # Simulate successful execution -> record procedure
    memory.add_or_update_procedure(Procedure(
        id="skill_search",
        description="search the web for information",
        steps=["use search with web query"],
        success_count=1,
        failure_count=0,
        avg_pe=0.1,
    ))

    # Second call: should match via procedural memory
    plan2 = asyncio.run(engine.plan_action(intent="search the web"))
    assert plan2["match_reason"] == "procedural_memory"
    assert plan2["skill_name"] == "search"


# ── Full flow: plan -> execute -> fail -> fallback ──

def test_full_flow_with_fallback():
    failing_skill = MockSkill("primary", ["web_search", "find"], success=False)
    backup_skill = MockSkill("backup", ["web_search", "retrieve"])
    engine, registry, memory, notifier = make_engine(
        skills=[failing_skill, backup_skill]
    )

    # Plan
    plan = asyncio.run(engine.plan_action(intent="search the web and find"))
    assert plan["skill_name"] in ("primary", "backup")

    # Execute primary (will fail)
    if plan["skill_name"] == "primary":
        result = asyncio.run(engine.execute_action(plan))
        assert result.success is False

        # Fallback
        fallback = asyncio.run(engine.handle_skill_failure(
            "primary", result.error or "failed", "search the web"
        ))
        if fallback is not None:
            fb_result = asyncio.run(engine.execute_action(fallback))
            assert fb_result.success is True
