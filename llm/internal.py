import logging

from ..types import Belief, BeliefCategory, BeliefSource, Goal
from ..config import MimirConfig
from .client import LLMClient, parse_json_response

log = logging.getLogger(__name__)


class InternalLLM:
    def __init__(self, client: LLMClient, config: MimirConfig):
        self.client = client
        self.config = config

    async def reason(self, belief_a: Belief, belief_b: Belief, cycle: int) -> Belief | None:
        """Infer a new belief from two existing beliefs."""
        system = (
            "你是一个推理引擎。给定两条信念及其置信度，"
            "判断能否推导出一条新的信念。如果能，输出JSON格式：\n"
            '{"statement": "...", "tags": [...], "reasoning": "..."}\n'
            '如果不能，输出 {"result": "none"}'
        )
        user = (
            f"信念A ({belief_a.confidence:.2f}): {belief_a.statement}\n"
            f"信念B ({belief_b.confidence:.2f}): {belief_b.statement}"
        )

        try:
            text = await self.client.complete(system, user)
            data = parse_json_response(text)
            if data is None or data.get("result") == "none" or "statement" not in data:
                return None

            confidence = (
                belief_a.confidence
                * belief_b.confidence
                * self.config.inference_confidence_discount
            )
            return Belief(
                id="",  # assigned by BeliefGraph
                statement=data["statement"],
                confidence=confidence,
                source=BeliefSource.INFERENCE,
                created_at=cycle,
                last_updated=cycle,
                last_verified=cycle,
                tags=data.get("tags", []),
                parent_ids=[belief_a.id, belief_b.id],
                category=BeliefCategory.HYPOTHESIS,
            )
        except Exception as e:
            log.warning("reason() failed: %s", e)
            return None

    async def simulate(
        self, belief_graph_summary: str, candidate_actions: list[str]
    ) -> list[dict]:
        """Predict outcomes of candidate actions."""
        system = (
            "你是一个预测引擎。给定当前信念状态和候选行动列表，"
            "预测每个行动对信念图的可能影响。\n"
            "对每个行动输出JSON数组中的一项：\n"
            '{"action": "...", "expected_pe_change": float, '
            '"affected_beliefs": [...], "reasoning": "..."}'
        )
        user = (
            f"当前信念概要：\n{belief_graph_summary}\n\n"
            f"候选行动：\n" + "\n".join(f"- {a}" for a in candidate_actions)
        )

        try:
            text = await self.client.complete(system, user)
            data = parse_json_response(text)
            if not isinstance(data, list):
                return []
            data.sort(key=lambda x: x.get("expected_pe_change", 0))
            return data
        except Exception as e:
            log.warning("simulate() failed: %s", e)
            return []

    async def plan(
        self, goal: Goal, belief_graph_summary: str, available_skills: list[str]
    ) -> list[str]:
        """Generate execution steps for a goal."""
        system = (
            "你是一个规划引擎。给定一个目标、当前信念状态和可用技能列表，"
            "生成一个3-5步的执行计划。每步是一个具体行动。\n"
            '输出JSON数组：["step1", "step2", ...]'
        )
        user = (
            f"目标：{goal.description}\n原因：{goal.reason}\n"
            f"当前信念：\n{belief_graph_summary}\n"
            f"可用技能：{available_skills}"
        )

        try:
            text = await self.client.complete(system, user)
            data = parse_json_response(text)
            if isinstance(data, list):
                return [str(s) for s in data]
            return []
        except Exception as e:
            log.warning("plan() failed: %s", e)
            return []

    async def abstract(self, beliefs: list[Belief], cycle: int) -> Belief | None:
        """Extract a higher-level belief from multiple related beliefs."""
        if len(beliefs) < 3:
            return None
        avg_conf = sum(b.confidence for b in beliefs) / len(beliefs)
        if avg_conf <= 0.6:
            return None

        system = (
            "你是一个抽象引擎。给定多条相关信念，提取一条更高层的概括性信念。\n"
            '输出JSON：{"statement": "...", "tags": [...]}\n'
            '如果这些信念没有共同的高层模式，输出 {"result": "none"}'
        )
        user = "信念列表：\n" + "\n".join(
            f"- ({b.confidence:.2f}) {b.statement}" for b in beliefs
        )

        try:
            text = await self.client.complete(system, user)
            data = parse_json_response(text)
            if data is None or data.get("result") == "none" or "statement" not in data:
                return None

            return Belief(
                id="",
                statement=data["statement"],
                confidence=avg_conf * 0.9,
                source=BeliefSource.ABSTRACTION,
                created_at=cycle,
                last_updated=cycle,
                last_verified=cycle,
                tags=data.get("tags", []),
                parent_ids=[b.id for b in beliefs],
                category=BeliefCategory.HYPOTHESIS,
            )
        except Exception as e:
            log.warning("abstract() failed: %s", e)
            return None

    # ── Step 5 additions ──

    async def plan_action_params(
        self,
        intent: str,
        skill_info: dict,
        belief_context: str = "",
    ) -> dict:
        """Generate execution parameters for a selected skill.

        Args:
            intent: What the user or system wants to accomplish
            skill_info: {"name": ..., "description": ..., "param_schema": {...}}
            belief_context: Summary of relevant beliefs

        Returns:
            dict of parameters matching the skill's param_schema
        """
        system = (
            "你是一个参数生成引擎。给定用户意图、技能描述和参数schema，"
            "生成调用该技能所需的参数字典。\n"
            "输出一个JSON对象，key对应param_schema中的参数名。\n"
            "只输出JSON，不要解释。"
        )
        schema_str = ""
        for k, v in skill_info.get("param_schema", {}).items():
            schema_str += f"  {k}: {v}\n"

        user = (
            f"意图：{intent}\n"
            f"技能：{skill_info.get('name', '')} — {skill_info.get('description', '')}\n"
            f"参数schema：\n{schema_str}\n"
        )
        if belief_context:
            user += f"信念上下文：\n{belief_context}\n"

        try:
            text = await self.client.complete(system, user, temperature=0.1)
            data = parse_json_response(text)
            if isinstance(data, dict):
                return data
            return {"intent": intent}
        except Exception as e:
            log.warning("plan_action_params() failed: %s", e)
            return {"intent": intent}

    async def should_act(
        self,
        goal: str,
        belief_summary: str,
        recent_pe: float,
    ) -> tuple[bool, str]:
        """Decide whether the current cycle should take action.

        Returns:
            (should_act: bool, reason: str)
        """
        system = (
            "你是一个决策引擎。给定当前目标、信念摘要和最近的预测误差(PE)，"
            "判断本周期是否应该采取主动行动。\n"
            '输出JSON：{"act": true/false, "reason": "..."}'
        )
        user = (
            f"目标：{goal}\n"
            f"信念摘要：{belief_summary}\n"
            f"最近PE：{recent_pe:.4f}\n"
        )

        try:
            text = await self.client.complete(system, user, temperature=0.1)
            data = parse_json_response(text)
            if isinstance(data, dict):
                return (bool(data.get("act", False)), str(data.get("reason", "")))
            return (False, "parse_error")
        except Exception as e:
            log.warning("should_act() failed: %s", e)
            return (False, str(e))
