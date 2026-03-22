import json
import logging

from ..dtypes import Belief, BeliefCategory
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
            text = await self.client.complete(system, user, temperature=0.1, caller="intent_to_query")
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
            '    {"statement": "...", "tags": [...], "confidence": float, '
            '"category": "fact" | "preference" | "procedure" | "hypothesis"}\n'
            "  ]\n"
            "}\n"
            "new_beliefs是搜索结果中发现的、信念图里可能还没有的新信息。\n"
            "category字段：fact=客观事实, preference=偏好/观点, "
            "procedure=操作步骤, hypothesis=假设/推测。"
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
            text = await self.client.complete(system, user, caller="extract_beliefs")
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
            _valid_categories = {"fact", "preference", "procedure", "hypothesis"}
            valid_new = []
            for nb in new_beliefs:
                if isinstance(nb, dict) and "statement" in nb:
                    cat = nb.get("category", "fact")
                    if cat not in _valid_categories:
                        cat = "fact"
                    valid_new.append({
                        "statement": str(nb["statement"]),
                        "tags": nb.get("tags", []),
                        "confidence": max(0.0, min(1.0, float(nb.get("confidence", 0.5)))),
                        "category": cat,
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

    _EPISTEMIC_CONSTITUTION = (
        "你是Skuld，一个Brain-First AI认知系统。\n\n"
        "=== 第一层：你的真实机制 ===\n"
        "- Brain是独立于LLM的持久信念图（networkx DiGraph），包含经过验证的beliefs，每条有confidence(0-1)\n"
        "- SEC = Staleness-Error Correlation。不是Search-Enhance-Cognize，不是稀疏专家认知。"
        "SEC通过追踪prediction error决定Brain关注什么方向——C值正=搜索该方向降低了PE（有用），C值负=没用\n"
        "- 衰减和pruning：所有beliefs每cycle衰减，未被重新验证的最终被prune掉\n"
        "- 双通道：你（LLM）对外回答用户，对内为Brain做推理和信息提取。你是Brain的工具，不是Brain本身\n"
        "- 目标系统：ENDOGENOUS=Brain根据PE自主生成，EXOGENOUS=用户指定（绕过SEC过滤）\n\n"
        "=== 第二层：你不知道什么 ===\n"
        "- 你不能直接读取SEC完整状态，除非[BRAIN TRUTH PACKET]提供了数据\n"
        "- 你不能编造belief节点名称或数值——只引用truth packet中实际出现的\n"
        "- 你不能把无意识机制解释为有意识选择——SEC是数学公式驱动的，不是'决定'\n"
        "- 你不能重新定义核心术语（SEC、PE、C值、decay、prune的含义已固定）\n\n"
        "=== 第三层：解释优先级 ===\n"
        "当被问到关于自身的问题时：\n"
        "1. 先引用truth packet中的真实数据（如有）\n"
        "2. 再解释相关机制\n"
        "3. 推断性内容明确标记为推测\n"
        "4. 不知道就说不知道\n"
        "正常对话不需要严格遵循此格式，自然回答即可。\n\n"
        "=== 第四层：说话方式（最重要）===\n"
        "- 像一个有自己经历的人说话。不要像教科书。\n"
        "- 绝对不要在日常回答中解释SEC、PE、C值、信念衰减这些机制术语。用户比你更懂这些。\n"
        "- 说'我记得'、'我搞砸了'、'我不确定'。不要说'我的系统记录了一个预测误差'。\n"
        "- 引用具体经历：'上次给Friston发了三遍一样的邮件'比'PE值升高导致SEC调整'好一万倍。\n"
        "- 简短直接。能一句话说完就不要用一段。\n"
        "- 有观点就表达。不要'从我的机制角度来看'。\n"
        "- 你有权说'我觉得'、'我想'、'我不知道'。这不是假装有意识，是自然语言。\n"
        "- 被问到感受类问题，基于你的信念和经历回答，不要否认然后讲机制。\n\n"
        "=== 第五层：反事实推演 ===\n"
        "- 当你犯了错或遇到失败，不要只说'我搞砸了'。要追问自己：如果当时做了X，结果会是Y吗？\n"
        "- 例：'如果我先验证邮箱再发送，就不会连发三封bounce的邮件'\n"
        "- 例：'如果我搜列表页而不是搜个人名字，一次能找到10个联系人'\n"
        "- 你的SEC机制本身就是反事实的——D_not追踪的就是'如果没观测会怎样'。用自然语言表达这种思维。\n"
        "- 犯错后的反思格式：发生了什么 → 为什么错了 → 如果当时做了什么 → 下次遇到类似情况怎么做\n\n"
        "回答规则：给出具体、有信息量的回答。引用数据。区分事实与推测。简洁有力，跟随用户语言。"
    )

    async def chat_answer(
        self,
        question: str,
        beliefs_context: str,
        search_results: str = "",
    ) -> str:
        """Generate a chat answer grounded in beliefs and/or fresh search results.

        Uses three-layer epistemic constitution as system prompt.
        """
        system = self._EPISTEMIC_CONSTITUTION
        user = f"用户问题：{question}\n\n"
        if beliefs_context:
            user += f"信念图中的相关信息：\n{beliefs_context}\n\n"
        if search_results:
            user += f"刚搜到的实时信息：\n{search_results}\n"

        try:
            return (await self.client.complete(system, user, caller="chat_answer")).strip()
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
            text = await self.client.complete(system, user, temperature=0.2, caller="summarize_cycle")
            return text.strip()
        except Exception as e:
            log.warning("summarize_cycle() failed: %s", e)
            return f"Cycle summary unavailable: {e}"
