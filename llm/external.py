import json
import logging

from ..types import Belief
from ..config import MimirConfig
from .client import LLMClient, parse_json_response

log = logging.getLogger(__name__)


class ExternalLLM:
    def __init__(self, client: LLMClient, config: MimirConfig):
        self.client = client
        self.config = config

    async def intent_to_query(self, intent: str, context: str = "") -> str:
        """Translate observation intent into a search query."""
        system = (
            "将以下观测意图翻译成一个简短的搜索引擎查询（3-8个词）。"
            "只输出查询本身，不要解释。"
        )
        user = f"意图：{intent}"
        if context:
            user += f"\n上下文：{context}"

        try:
            text = await self.client.complete(system, user, temperature=0.1)
            return text.strip().strip('"').strip("'")
        except Exception as e:
            log.warning("intent_to_query() failed: %s", e)
            return intent[:80]

    async def extract_beliefs(self, search_results: str, target_belief: Belief) -> dict:
        """Extract structured information from search results."""
        system = (
            "你是一个信息提取引擎。给定搜索结果和一条待验证的信念，"
            "判断搜索结果是否支持、反驳或无关于该信念。\n"
            "输出JSON：\n"
            "{\n"
            '  "verdict": "support" | "contradict" | "irrelevant",\n'
            '  "observed_confidence": float (0-1, 你对verdict的确信程度),\n'
            '  "extracted_facts": ["fact1", "fact2", ...],\n'
            '  "new_beliefs": [\n'
            '    {"statement": "...", "tags": [...], "confidence": float}\n'
            "  ]\n"
            "}\n"
            "new_beliefs是搜索结果中发现的、信念图里可能还没有的新信息。"
        )
        user = (
            f"待验证信念：{target_belief.statement} "
            f"(当前confidence: {target_belief.confidence:.2f})\n\n"
            f"搜索结果：\n{search_results}"
        )

        default = {
            "verdict": "irrelevant",
            "observed_confidence": 0.5,
            "extracted_facts": [],
            "new_beliefs": [],
        }

        try:
            text = await self.client.complete(system, user)
            data = parse_json_response(text)
            if not isinstance(data, dict):
                return default

            # Validate and sanitize
            verdict = data.get("verdict", "irrelevant")
            if verdict not in ("support", "contradict", "irrelevant"):
                verdict = "irrelevant"

            observed = float(data.get("observed_confidence", 0.5))
            observed = max(0.0, min(1.0, observed))

            facts = data.get("extracted_facts", [])
            if not isinstance(facts, list):
                facts = []

            new_beliefs = data.get("new_beliefs", [])
            if not isinstance(new_beliefs, list):
                new_beliefs = []
            # Validate each new belief
            valid_new = []
            for nb in new_beliefs:
                if isinstance(nb, dict) and "statement" in nb:
                    valid_new.append({
                        "statement": str(nb["statement"]),
                        "tags": nb.get("tags", []),
                        "confidence": max(0.0, min(1.0, float(nb.get("confidence", 0.5)))),
                    })

            return {
                "verdict": verdict,
                "observed_confidence": observed,
                "extracted_facts": facts,
                "new_beliefs": valid_new,
            }
        except Exception as e:
            log.warning("extract_beliefs() failed: %s", e)
            return default

    async def chat_answer(
        self,
        question: str,
        beliefs_context: str,
        search_results: str = "",
    ) -> str:
        """Generate a chat answer grounded in beliefs and/or fresh search results.

        The prompt demands concrete analysis — no hedging, no 'I don't know',
        no 'as an AI'. If data is thin, say what IS known and what's uncertain.
        """
        system = (
            "你是Mimir，一个拥有信念图和实时搜索能力的认知系统。\n"
            "回答规则：\n"
            "1. 给出具体、有信息量的回答。绝对不要说'我不知道'或'等下个周期'。\n"
            "2. 如果有搜索结果，直接引用里面的数据和事实。\n"
            "3. 如果信念图有相关信息，综合信念图和搜索结果一起分析。\n"
            "4. 标注哪些是高置信度事实，哪些是推测。\n"
            "5. 简洁有力，不要套话。中英文都可以，跟随用户语言。"
        )
        user = f"用户问题：{question}\n\n"
        if beliefs_context:
            user += f"信念图中的相关信息：\n{beliefs_context}\n\n"
        if search_results:
            user += f"刚搜到的实时信息：\n{search_results}\n"

        try:
            return (await self.client.complete(system, user)).strip()
        except Exception as e:
            log.warning("chat_answer() failed: %s", e)
            return f"处理出错: {e}"

    async def summarize_cycle(self, cycle_data: dict) -> str:
        """Summarize cycle changes into natural language notes."""
        system = (
            "你是一个记录员。将以下周期数据总结为简洁的笔记（3-5句话）。"
            "重点记录：新发现、重大PE变化、新生成的目标、信念图的变化。"
        )
        user = json.dumps(cycle_data, ensure_ascii=False, default=str)

        try:
            text = await self.client.complete(system, user, temperature=0.2)
            return text.strip()
        except Exception as e:
            log.warning("summarize_cycle() failed: %s", e)
            return f"Cycle summary unavailable: {e}"
