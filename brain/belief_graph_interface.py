"""Belief Graph interface stub.

The actual implementation (belief_graph.py) is proprietary.
Implement this interface to provide your own belief graph.
"""

from abc import ABC, abstractmethod
from typing import Optional
from ..dtypes import Belief
from ..config import MimirConfig


class BeliefGraphInterface(ABC):
    """Directed graph of beliefs with confidence propagation.

    Nodes are Belief objects. Edges represent dependency relationships.
    When a parent belief's confidence changes, children are affected.
    """

    @abstractmethod
    def __init__(self, config: MimirConfig): ...

    @abstractmethod
    def add_belief(self, belief: Belief) -> str:
        """Add a belief node. Returns belief id."""

    @abstractmethod
    def get_belief(self, belief_id: str) -> Optional[Belief]: ...

    @abstractmethod
    def update_belief(
        self, belief_id: str, new_confidence: float, pe: float, cycle: int
    ) -> None:
        """Update belief confidence using PE-based Bayesian adjustment."""

    @abstractmethod
    def add_dependency(
        self, from_id: str, to_id: str, weight: float = 1.0
    ) -> None:
        """Add dependency edge. from_id's changes affect to_id."""

    @abstractmethod
    def propagate_update(self, updated_id: str) -> list[str]:
        """Propagate confidence decay along dependency edges (one layer)."""

    @abstractmethod
    def decay_unverified(self, current_cycle: int) -> list[str]:
        """Decay confidence for unverified beliefs."""

    @abstractmethod
    def prune(self) -> list[str]:
        """Remove low-confidence leaf beliefs."""

    @abstractmethod
    def get_all_beliefs(self) -> list[Belief]: ...

    @abstractmethod
    def get_beliefs_by_tag(self, tag: str) -> list[Belief]: ...

    @abstractmethod
    def get_high_pe_beliefs(
        self, threshold: float, min_persistence: int
    ) -> list[Belief]:
        """Beliefs with PE above threshold for min_persistence cycles."""

    @abstractmethod
    def get_stale_beliefs(
        self, current_cycle: int, staleness_threshold: int
    ) -> list[Belief]:
        """High-confidence beliefs not verified recently."""

    @abstractmethod
    def to_dict(self) -> dict: ...

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict, config: MimirConfig) -> "BeliefGraphInterface": ...
