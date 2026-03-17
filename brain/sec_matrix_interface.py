"""SEC Matrix interface stub.

The actual implementation (sec_matrix.py) is proprietary.
Implement this interface to provide your own SEC matrix.
"""

from abc import ABC, abstractmethod
from ..config import MimirConfig


class SECMatrixInterface(ABC):
    """Staleness-Error Correlation matrix.

    Tracks which observation directions reduce prediction error (PE).
    C = E[PE | cluster not observed] - E[PE | cluster observed]
    Positive C = observing this cluster helps. Negative C = doesn't help.
    """

    @abstractmethod
    def __init__(self, config: MimirConfig): ...

    @abstractmethod
    def update(
        self,
        observed_clusters: set[str],
        all_clusters: set[str],
        pe: float,
        cycle: int,
    ) -> None:
        """Update EMA statistics after each cycle."""

    @abstractmethod
    def get_c_value(self, cluster: str) -> float:
        """Return C value for a cluster."""

    @abstractmethod
    def filter_action(self, cluster: str, cycle: int) -> bool:
        """SEC filter. True=allow observation, False=reject."""

    @abstractmethod
    def get_top_clusters(self, n: int) -> list[tuple[str, float]]:
        """Top n clusters by C value."""

    @abstractmethod
    def get_negative_clusters(self) -> list[tuple[str, float]]:
        """All clusters with C < 0."""

    @abstractmethod
    def to_dict(self) -> dict: ...

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict, config: MimirConfig) -> "SECMatrixInterface": ...
