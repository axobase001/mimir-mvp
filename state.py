import json
from pathlib import Path

from .brain.belief_graph import BeliefGraph
from .brain.sec_matrix import SECMatrix
from .brain.memory import Memory
from .types import Goal, GoalOrigin, GoalStatus
from .config import MimirConfig


class MimirState:
    """Brain state persistence."""

    @staticmethod
    def to_dict(
        belief_graph: BeliefGraph,
        sec_matrix: SECMatrix,
        memory: Memory,
        goals: dict[str, Goal],
        cycle_count: int,
        usage_stats: dict,
    ) -> dict:
        """Serialize state to a dict (for BrainStore or file persistence)."""
        return {
            "cycle_count": cycle_count,
            "belief_graph": belief_graph.to_dict(),
            "sec_matrix": sec_matrix.to_dict(),
            "memory": memory.to_dict(),
            "goals": {
                gid: {
                    "id": g.id,
                    "target_belief_id": g.target_belief_id,
                    "description": g.description,
                    "reason": g.reason,
                    "status": g.status.value,
                    "created_at": g.created_at,
                    "priority": g.priority,
                    "origin": g.origin.value,
                    "_cycles_below_complete": g._cycles_below_complete,
                }
                for gid, g in goals.items()
            },
            "usage_stats": usage_stats,
        }

    @staticmethod
    def save(
        path: str,
        belief_graph: BeliefGraph,
        sec_matrix: SECMatrix,
        memory: Memory,
        goals: dict[str, Goal],
        cycle_count: int,
        usage_stats: dict,
    ) -> None:
        data = MimirState.to_dict(
            belief_graph, sec_matrix, memory, goals, cycle_count, usage_stats
        )
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _parse_state_dict(
        data: dict, config: MimirConfig
    ) -> tuple[BeliefGraph, SECMatrix, Memory, dict[str, Goal], int, dict]:
        """Parse a state dict into component objects."""
        belief_graph = BeliefGraph.from_dict(data["belief_graph"], config)
        sec_matrix = SECMatrix.from_dict(data["sec_matrix"], config)
        memory = Memory.from_dict(data["memory"], config)

        goals: dict[str, Goal] = {}
        for gid, gdata in data.get("goals", {}).items():
            # Backward compatible: default origin to ENDOGENOUS
            origin_value = gdata.get("origin", "endogenous")
            try:
                origin = GoalOrigin(origin_value)
            except ValueError:
                origin = GoalOrigin.ENDOGENOUS

            goals[gid] = Goal(
                id=gdata["id"],
                target_belief_id=gdata["target_belief_id"],
                description=gdata["description"],
                reason=gdata["reason"],
                status=GoalStatus(gdata["status"]),
                created_at=gdata["created_at"],
                priority=gdata["priority"],
                origin=origin,
                _cycles_below_complete=gdata.get("_cycles_below_complete", 0),
            )

        cycle_count = data.get("cycle_count", 0)
        usage_stats = data.get("usage_stats", {})

        return belief_graph, sec_matrix, memory, goals, cycle_count, usage_stats

    @staticmethod
    def load(
        path: str, config: MimirConfig
    ) -> tuple[BeliefGraph, SECMatrix, Memory, dict[str, Goal], int, dict]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return MimirState._parse_state_dict(data, config)

    @staticmethod
    def load_from_dict(
        data: dict, config: MimirConfig
    ) -> tuple[BeliefGraph, SECMatrix, Memory, dict[str, Goal], int, dict]:
        """Load state from an already-parsed dict (used by BrainStore)."""
        return MimirState._parse_state_dict(data, config)
