from typing import Optional

from ..types import Episode, Procedure
from ..config import MimirConfig


class Memory:
    def __init__(self, config: MimirConfig):
        self.config = config
        self.episodes: list[Episode] = []
        self.procedures: dict[str, Procedure] = {}

    def add_episode(self, episode: Episode) -> None:
        """Record an episode. FIFO eviction when full."""
        self.episodes.append(episode)
        if len(self.episodes) > self.config.max_episodes:
            self.episodes = self.episodes[-self.config.max_episodes :]

    def add_or_update_procedure(self, procedure: Procedure) -> None:
        """Add new or update existing procedure by id.

        On update: merge counts, refresh steps/description, update avg_pe.
        On add when full: evict worst success-rate procedure.
        """
        if procedure.id in self.procedures:
            existing = self.procedures[procedure.id]
            existing.success_count += procedure.success_count
            existing.failure_count += procedure.failure_count
            if procedure.last_failure_reason:
                existing.last_failure_reason = procedure.last_failure_reason
            total = existing.success_count + existing.failure_count
            if total > 0:
                existing.avg_pe = (
                    existing.avg_pe * (total - 1) + procedure.avg_pe
                ) / total
            existing.steps = procedure.steps
            existing.description = procedure.description
        else:
            if len(self.procedures) >= self.config.max_procedures:
                worst_id = min(
                    self.procedures,
                    key=lambda pid: self.procedures[pid].success_count
                    / max(
                        1,
                        self.procedures[pid].success_count
                        + self.procedures[pid].failure_count,
                    ),
                )
                del self.procedures[worst_id]
            self.procedures[procedure.id] = procedure

    def get_relevant_episodes(
        self, tags: list[str], n: int = 5
    ) -> list[Episode]:
        """Find episodes related to given tags (belief ids or tag strings).

        Match: any overlap between tags and episode.beliefs_affected.
        Sort by PE improvement (pe_before - pe_after), return top n.
        """
        tag_set = set(tags)
        relevant = [
            ep
            for ep in self.episodes
            if tag_set.intersection(ep.beliefs_affected)
        ]
        relevant.sort(key=lambda ep: ep.pe_before - ep.pe_after, reverse=True)
        return relevant[:n]

    def get_procedure(self, proc_id: str) -> Optional[Procedure]:
        return self.procedures.get(proc_id)

    def get_best_procedures(self, n: int = 5) -> list[Procedure]:
        """Return n procedures with highest success rate."""
        sorted_procs = sorted(
            self.procedures.values(),
            key=lambda p: p.success_count
            / max(1, p.success_count + p.failure_count),
            reverse=True,
        )
        return sorted_procs[:n]

    def to_dict(self) -> dict:
        return {
            "episodes": [
                {
                    "cycle": ep.cycle,
                    "action": ep.action,
                    "outcome": ep.outcome,
                    "pe_before": ep.pe_before,
                    "pe_after": ep.pe_after,
                    "beliefs_affected": ep.beliefs_affected,
                }
                for ep in self.episodes
            ],
            "procedures": {
                pid: {
                    "id": p.id,
                    "description": p.description,
                    "steps": p.steps,
                    "success_count": p.success_count,
                    "failure_count": p.failure_count,
                    "last_failure_reason": p.last_failure_reason,
                    "avg_pe": p.avg_pe,
                }
                for pid, p in self.procedures.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict, config: MimirConfig) -> "Memory":
        mem = cls(config)
        for edata in data.get("episodes", []):
            mem.episodes.append(
                Episode(
                    cycle=edata["cycle"],
                    action=edata["action"],
                    outcome=edata["outcome"],
                    pe_before=edata["pe_before"],
                    pe_after=edata["pe_after"],
                    beliefs_affected=edata.get("beliefs_affected", []),
                )
            )
        for pid, pdata in data.get("procedures", {}).items():
            mem.procedures[pid] = Procedure(
                id=pdata["id"],
                description=pdata["description"],
                steps=pdata["steps"],
                success_count=pdata.get("success_count", 0),
                failure_count=pdata.get("failure_count", 0),
                last_failure_reason=pdata.get("last_failure_reason", ""),
                avg_pe=pdata.get("avg_pe", 0.0),
            )
        return mem
