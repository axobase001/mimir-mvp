"""Tests for SmartSkillRegistry."""

import asyncio

from mimir.skills.base import Skill, SkillResult
from mimir.skills.registry import SmartSkillRegistry
from mimir.skills.code_exec import CodeExecSkill
from mimir.skills.document import DocumentSkill
from mimir.skills.web_fetch import WebFetchSkill
from mimir.skills.data_analysis import DataAnalysisSkill
from mimir.skills.file_io import FileReadSkill, FileWriteSkill
from mimir.brain.memory import Memory
from mimir.brain.sec_matrix import SECMatrix
from mimir.config import MimirConfig
from mimir.types import Procedure


class DummySkill(Skill):
    def __init__(self, n: str, desc: str, caps: list[str], risk: str = "safe"):
        super().__init__()
        self._name = n
        self._desc = desc
        self._caps = caps
        self._risk = risk

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def capabilities(self) -> list[str]:
        return self._caps

    @property
    def risk_level(self) -> str:
        return self._risk

    async def execute(self, params: dict) -> dict:
        return {"success": True, "result": f"Dummy {self._name}", "error": None}


def make_registry() -> SmartSkillRegistry:
    reg = SmartSkillRegistry()
    reg.register(DummySkill("search", "Search web", ["web_search", "information_retrieval"]))
    reg.register(DummySkill("code", "Run code", ["run_code", "calculation", "automation"], "dangerous"))
    reg.register(DummySkill("doc", "Write docs", ["write_document", "generate_report"], "review"))
    reg.register(DummySkill("data", "Analyze data", ["analyze_data", "statistics", "csv_processing"]))
    return reg


# ── Registration and discovery ──

def test_register_and_discover():
    reg = make_registry()
    skills = reg.discover()
    assert len(skills) == 4
    names = {s["name"] for s in skills}
    assert names == {"search", "code", "doc", "data"}


def test_get_skill():
    reg = make_registry()
    assert reg.get("search") is not None
    assert reg.get("nonexistent") is None


# ── select_skill: capability match ──

def test_select_by_capability_search():
    reg = make_registry()
    results = reg.select_skill("I need to search the web for information")
    assert len(results) > 0
    assert results[0]["name"] == "search"
    assert results[0]["match_reason"] == "capability_match"


def test_select_by_capability_code():
    reg = make_registry()
    results = reg.select_skill("run some code to calculate the result")
    # "run" matches run_code, "code" also matches, "calculation" matches
    assert any(r["name"] == "code" for r in results)


def test_select_by_capability_doc():
    reg = make_registry()
    results = reg.select_skill("write a document report")
    assert any(r["name"] == "doc" for r in results)


def test_select_by_capability_data():
    reg = make_registry()
    results = reg.select_skill("analyze this data and get statistics")
    assert any(r["name"] == "data" for r in results)


# ── select_skill: procedural memory match (priority 1) ──

def test_select_priority_memory():
    """Procedural memory match should override capability match."""
    reg = make_registry()
    config = MimirConfig()
    mem = Memory(config)

    # Add a procedure referencing the "doc" skill for "search" intent
    mem.add_or_update_procedure(Procedure(
        id="proc_1",
        description="search for info and write document",
        steps=["use doc skill to write report"],
        success_count=5,
        failure_count=0,
        avg_pe=0.1,
    ))

    results = reg.select_skill("search for info", memory=mem)
    # doc should appear first because procedural memory has it
    assert results[0]["name"] == "doc"
    assert results[0]["match_reason"] == "procedural_memory"


# ── select_skill: SEC-weighted ──

def test_select_sec_weighted():
    reg = make_registry()
    config = MimirConfig()
    sec = SECMatrix(config)

    # Give "web_search" capability a positive C-value
    sec.entries["web_search"] = __import__("mimir.types", fromlist=["SECEntry"]).SECEntry(
        cluster="web_search", d_obs=0.1, d_not=0.5, obs_count=5, not_count=5,
    )

    results = reg.select_skill("find some information", sec_matrix=sec)
    # search should get a boost from SEC
    search_result = next((r for r in results if r["name"] == "search"), None)
    assert search_result is not None


# ── select_skill: success rate fallback ──

def test_select_fallback():
    reg = make_registry()
    # Intent that doesn't match any capability
    results = reg.select_skill("xyz completely unrelated")
    assert len(results) > 0
    assert results[0]["match_reason"] == "fallback"


# ── execute_skill ──

def test_execute_skill_success():
    reg = make_registry()
    result = asyncio.run(reg.execute_skill("search", {"query": "test"}))
    assert result.success is True
    assert "Dummy search" in result.result


def test_execute_skill_not_found():
    reg = make_registry()
    result = asyncio.run(reg.execute_skill("nonexistent", {}))
    assert result.success is False
    assert "not found" in result.error


def test_execute_skill_dangerous_warning():
    """Dangerous skills should still execute but log WARNING."""
    reg = make_registry()
    result = asyncio.run(reg.execute_skill("code", {"code": "print(1)"}))
    assert result.success is True


# ── Usage history ──

def test_usage_history():
    reg = make_registry()
    asyncio.run(reg.execute_skill("search", {}))
    asyncio.run(reg.execute_skill("code", {}))
    asyncio.run(reg.execute_skill("search", {}))

    history = reg.get_usage_history(last_n=10)
    assert len(history) == 3
    assert history[0]["skill"] == "search"
    assert history[1]["skill"] == "code"


# ── Backward-compatible list_skills ──

def test_list_skills_compat():
    reg = make_registry()
    skills = reg.list_skills()
    assert len(skills) == 4
    assert all("name" in s and "description" in s for s in skills)


# ── Real skill integration ──

def test_real_skills_in_registry():
    """Register actual skill implementations and verify discover()."""
    reg = SmartSkillRegistry()
    reg.register(CodeExecSkill())
    reg.register(DocumentSkill())
    reg.register(WebFetchSkill())
    reg.register(DataAnalysisSkill())
    reg.register(FileReadSkill())
    reg.register(FileWriteSkill())

    info = reg.discover()
    assert len(info) == 6

    # Each should have capabilities and risk_level
    for s in info:
        assert "capabilities" in s
        assert "risk_level" in s
        assert s["risk_level"] in ("safe", "review", "dangerous")


def test_select_real_skills():
    """Verify select_skill works with real skill objects."""
    reg = SmartSkillRegistry()
    reg.register(CodeExecSkill())
    reg.register(WebFetchSkill())
    reg.register(DataAnalysisSkill())

    results = reg.select_skill("fetch a webpage and extract content")
    assert any(r["name"] == "web_fetch" for r in results)

    results = reg.select_skill("run code to process data")
    assert any(r["name"] == "code_exec" for r in results)
