from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SkillResult:
    """Standardized result from skill execution."""
    success: bool
    result: Any = ""
    error: Optional[str] = None
    artifacts: list[str] = field(default_factory=list)
    summary: str = ""
    pe_impact: float = 0.0


class Skill(ABC):
    """Base class for all Skuld skills."""

    def __init__(self) -> None:
        self._outcome_history: list[dict] = []

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    async def execute(self, params: dict) -> dict: ...

    # ── New abstract properties (with defaults for backward compat) ──

    @property
    def capabilities(self) -> list[str]:
        """List of capability tags this skill provides."""
        return []

    @property
    def param_schema(self) -> dict:
        """JSON-schema-like dict describing expected parameters."""
        return {}

    @property
    def risk_level(self) -> str:
        """One of 'safe', 'review', 'dangerous'."""
        return "safe"

    @property
    def usage_stats(self) -> dict:
        return {}

    # ── SEC tracking ──

    def record_outcome(
        self, success: bool, pe_before: float, pe_after: float
    ) -> None:
        """Record execution outcome for SEC tracking."""
        self._outcome_history.append({
            "success": success,
            "pe_before": pe_before,
            "pe_after": pe_after,
            "timestamp": time.time(),
        })
        # Keep last 200 entries
        if len(self._outcome_history) > 200:
            self._outcome_history = self._outcome_history[-200:]

    @property
    def success_rate(self) -> float:
        """Fraction of successful executions."""
        if not self._outcome_history:
            return 0.0
        successes = sum(1 for o in self._outcome_history if o["success"])
        return successes / len(self._outcome_history)

    @property
    def avg_pe_improvement(self) -> float:
        """Average PE reduction (positive means improvement)."""
        if not self._outcome_history:
            return 0.0
        improvements = [
            o["pe_before"] - o["pe_after"] for o in self._outcome_history
        ]
        return sum(improvements) / len(improvements)


class SkillRegistry:
    """Simple skill registry — kept for backward compatibility."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]
