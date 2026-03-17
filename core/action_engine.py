"""ActionEngine — plan and execute skill-based actions."""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..skills.base import SkillResult
from ..skills.registry import SmartSkillRegistry
from ..brain.memory import Memory
from .notifier import Notifier, Notification, NotifyLevel

log = logging.getLogger(__name__)


class ActionEngine:
    """Plan, execute, and handle failure for skill-based actions."""

    def __init__(
        self,
        skill_registry: SmartSkillRegistry,
        memory: Memory,
        notifier: Notifier,
        internal_llm: Any = None,
    ) -> None:
        self.registry = skill_registry
        self.memory = memory
        self.notifier = notifier
        self.internal_llm = internal_llm

    async def plan_action(
        self,
        intent: str,
        goal: str = "",
        belief_context: str = "",
        sec_matrix: Any = None,
        memory: Optional[Memory] = None,
    ) -> dict:
        """Select a skill and generate execution parameters.

        Returns:
            {
                "skill_name": str,
                "params": dict,
                "match_reason": str,
                "risk_level": str,
            }
        """
        mem = memory or self.memory

        # Step 1: Select skill candidates
        candidates = self.registry.select_skill(
            intent=intent,
            goal=goal,
            sec_matrix=sec_matrix,
            memory=mem,
        )

        if not candidates:
            return {
                "skill_name": "",
                "params": {},
                "match_reason": "no_skill_found",
                "risk_level": "safe",
            }

        best = candidates[0]
        skill_name = best["name"]
        skill = self.registry.get(skill_name)

        # Step 2: Generate parameters
        params: dict = {}
        if self.internal_llm is not None and hasattr(self.internal_llm, "plan_action_params"):
            try:
                skill_info = {
                    "name": skill_name,
                    "description": skill.description if skill else "",
                    "param_schema": skill.param_schema if skill else {},
                }
                params = await self.internal_llm.plan_action_params(
                    intent, skill_info, belief_context,
                )
            except Exception as e:
                log.warning("LLM param generation failed: %s", e)
                params = {"intent": intent}
        else:
            params = {"intent": intent}

        return {
            "skill_name": skill_name,
            "params": params,
            "match_reason": best.get("match_reason", "unknown"),
            "risk_level": best.get("risk_level", "safe"),
        }

    async def execute_action(
        self,
        action_plan: dict,
        user_id: str = "",
        pe_before: float = 0.0,
    ) -> SkillResult:
        """Execute an action plan, checking risk level first."""
        skill_name = action_plan.get("skill_name", "")
        params = action_plan.get("params", {})
        risk = action_plan.get("risk_level", "safe")

        if not skill_name:
            return SkillResult(
                success=False,
                error="No skill specified in action plan",
                summary="Action plan has no skill",
            )

        # Risk check
        if risk == "dangerous":
            log.warning(
                "DANGEROUS action requested: skill=%s user=%s params=%s",
                skill_name, user_id, params,
            )
            self.notifier.push(Notification(
                level=NotifyLevel.URGENT,
                title=f"Dangerous skill executed: {skill_name}",
                body=f"User {user_id} triggered dangerous skill with params: {params}",
                cycle=0,
            ))

        result = await self.registry.execute_skill(skill_name, params, pe_before)

        # Record outcome in skill
        skill = self.registry.get(skill_name)
        if skill is not None:
            skill.record_outcome(result.success, pe_before, pe_before + result.pe_impact)

        return result

    async def handle_skill_failure(
        self,
        skill_name: str,
        error: str,
        intent: str,
        sec_matrix: Any = None,
        memory: Optional[Memory] = None,
    ) -> Optional[dict]:
        """Attempt to find a fallback action after skill failure.

        Degradation strategy:
        1. Check procedural memory for alternatives
        2. Find alternate skill via registry
        3. Give up
        """
        mem = memory or self.memory
        log.info(
            "Handling failure for skill '%s': %s — seeking fallback",
            skill_name, error,
        )

        # Strategy 1: Check procedural memory
        for proc_id, proc in mem.procedures.items():
            total = proc.success_count + proc.failure_count
            if total == 0:
                continue
            rate = proc.success_count / total
            if rate < 0.3:
                continue
            # Check if procedure mentions a different skill
            for step in proc.steps:
                for candidate_name in self.registry._skills:
                    if candidate_name != skill_name and candidate_name in step.lower():
                        log.info(
                            "Fallback from procedural memory: %s -> %s (proc=%s)",
                            skill_name, candidate_name, proc_id,
                        )
                        skill = self.registry.get(candidate_name)
                        return {
                            "skill_name": candidate_name,
                            "params": {"intent": intent},
                            "match_reason": "procedural_memory_fallback",
                            "risk_level": skill.risk_level if skill else "safe",
                        }

        # Strategy 2: Find alternate skill via registry
        candidates = self.registry.select_skill(
            intent=intent,
            sec_matrix=sec_matrix,
            memory=mem,
        )
        for candidate in candidates:
            if candidate["name"] != skill_name:
                log.info(
                    "Fallback from registry: %s -> %s",
                    skill_name, candidate["name"],
                )
                return {
                    "skill_name": candidate["name"],
                    "params": {"intent": intent},
                    "match_reason": "registry_fallback",
                    "risk_level": candidate.get("risk_level", "safe"),
                }

        # Strategy 3: Give up
        log.warning("No fallback found for failed skill '%s'", skill_name)
        return None
