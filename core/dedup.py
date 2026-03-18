import logging

from ..types import Belief
from ..brain.belief_graph import BeliefGraph
from ..llm.client import LLMClient, parse_json_response
from ..config import MimirConfig

log = logging.getLogger(__name__)


class BeliefDeduplicator:
    def __init__(self, client: LLMClient, config: MimirConfig):
        self.client = client
        self.config = config

    async def is_duplicate(
        self,
        new_statement: str,
        existing_beliefs: list[Belief],
        threshold: float = 0.85,
    ) -> tuple[bool, str | None]:
        """Check if new_statement is semantically duplicate of any existing belief."""
        if not existing_beliefs:
            return False, None

        # Limit comparison set
        candidates = existing_beliefs[:10]

        system = (
            "判断新信念是否与已有信念列表中的某一条语义重复（表达同一个事实）。\n"
            '如果重复，输出JSON: {"duplicate": true, "match_id": "belief_xxx"}\n'
            '如果不重复，输出JSON: {"duplicate": false}'
        )
        user = (
            f"新信念: {new_statement}\n\n已有信念:\n"
            + "\n".join(f"- [{b.id}] {b.statement}" for b in candidates)
        )

        try:
            text = await self.client.complete(system, user, temperature=0.0, caller="dedup")
            data = parse_json_response(text)
            if data and data.get("duplicate"):
                match_id = data.get("match_id")
                if match_id and any(b.id == match_id for b in candidates):
                    return True, match_id
            return False, None
        except Exception as e:
            log.warning("dedup check failed: %s", e)
            return False, None

    async def merge_beliefs(
        self, belief_ids: list[str], belief_graph: BeliefGraph
    ) -> str:
        """Merge duplicate beliefs. Keep highest-confidence as primary."""
        beliefs = [belief_graph.get_belief(bid) for bid in belief_ids]
        beliefs = [b for b in beliefs if b is not None]
        if not beliefs:
            return ""

        primary = max(beliefs, key=lambda b: b.confidence)
        others = [b for b in beliefs if b.id != primary.id]

        for other in others:
            # Merge PE history
            primary.pe_history.extend(other.pe_history)
            primary.pe_history = primary.pe_history[-self.config.max_pe_history :]

            # Redirect edges pointing to/from other → primary
            graph = belief_graph.graph
            for pred in list(graph.predecessors(other.id)):
                if pred != primary.id:
                    w = graph[pred][other.id].get("weight", 1.0)
                    if not graph.has_edge(pred, primary.id):
                        graph.add_edge(pred, primary.id, weight=w)
            for succ in list(graph.successors(other.id)):
                if succ != primary.id:
                    w = graph[other.id][succ].get("weight", 1.0)
                    if not graph.has_edge(primary.id, succ):
                        graph.add_edge(primary.id, succ, weight=w)

            graph.remove_node(other.id)

        return primary.id
