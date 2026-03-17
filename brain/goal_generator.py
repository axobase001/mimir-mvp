from ..types import Goal, GoalStatus
from ..config import MimirConfig
from .belief_graph import BeliefGraph
from .sec_matrix import SECMatrix


class GoalGenerator:
    def __init__(
        self, config: MimirConfig, belief_graph: BeliefGraph, sec_matrix: SECMatrix
    ):
        self.config = config
        self.belief_graph = belief_graph
        self.sec_matrix = sec_matrix
        self.goals: dict[str, Goal] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"goal_{self._counter:03d}"

    def _has_active_goal_for(self, belief_id: str) -> bool:
        return any(
            g.target_belief_id == belief_id and g.status == GoalStatus.ACTIVE
            for g in self.goals.values()
        )

    def generate_goals(self, current_cycle: int) -> list[Goal]:
        """Scan belief graph, discover beliefs that need action, generate goals.

        Two triggers:
        1. High PE persistent -> "investigate" goal.
           priority = avg_pe * max(SEC C value for belief tags, 0.1)
        2. High confidence stale -> "refresh" goal.
           priority = confidence * staleness / 100
        """
        new_goals: list[Goal] = []

        # Type 1: High PE persistent
        high_pe = self.belief_graph.get_high_pe_beliefs(
            self.config.goal_pe_threshold,
            self.config.goal_pe_persistence,
        )
        for belief in high_pe:
            if self._has_active_goal_for(belief.id):
                continue

            recent_pe = belief.pe_history[-self.config.goal_pe_persistence :]
            avg_pe = sum(recent_pe) / len(recent_pe)

            c_values = [self.sec_matrix.get_c_value(tag) for tag in belief.tags]
            sec_factor = max(c_values) if c_values else 0.0
            priority = avg_pe * max(sec_factor, 0.1)

            new_goals.append(
                Goal(
                    id=self._next_id(),
                    target_belief_id=belief.id,
                    description=f"Investigate: {belief.statement}",
                    reason=f"PE > {self.config.goal_pe_threshold} for "
                    f"{self.config.goal_pe_persistence} cycles",
                    status=GoalStatus.ACTIVE,
                    created_at=current_cycle,
                    priority=priority,
                )
            )

        # Type 2: Stale high confidence
        stale = self.belief_graph.get_stale_beliefs(
            current_cycle,
            self.config.goal_staleness_threshold,
        )
        for belief in stale:
            if self._has_active_goal_for(belief.id):
                continue

            staleness = current_cycle - belief.last_verified
            priority = belief.confidence * (staleness / 100.0)

            new_goals.append(
                Goal(
                    id=self._next_id(),
                    target_belief_id=belief.id,
                    description=f"Refresh: {belief.statement}",
                    reason=f"Confidence {belief.confidence:.2f} but "
                    f"unverified for {staleness} cycles",
                    status=GoalStatus.ACTIVE,
                    created_at=current_cycle,
                    priority=priority,
                )
            )

        # Cap at available slots
        active_count = sum(
            1 for g in self.goals.values() if g.status == GoalStatus.ACTIVE
        )
        available = self.config.max_active_goals - active_count

        new_goals.sort(key=lambda g: g.priority, reverse=True)
        new_goals = new_goals[: max(0, available)]

        for goal in new_goals:
            self.goals[goal.id] = goal

        return new_goals

    def complete_goal(self, goal_id: str) -> None:
        if goal_id in self.goals:
            self.goals[goal_id].status = GoalStatus.COMPLETED

    def abandon_goal(self, goal_id: str, reason: str) -> None:
        if goal_id in self.goals:
            self.goals[goal_id].status = GoalStatus.ABANDONED
            self.goals[goal_id].reason = reason
