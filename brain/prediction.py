from ..types import Belief
from ..config import MimirConfig


class PredictionEngine:
    def __init__(self, config: MimirConfig):
        self.config = config

    def generate_predictions(self, beliefs: list[Belief]) -> dict[str, float]:
        """Predict = current confidence (status quo hypothesis).

        Brain predicts that each belief's confidence will remain unchanged
        at next verification. Any deviation is prediction error.
        """
        return {b.id: b.confidence for b in beliefs}

    def compute_pe(
        self, belief_id: str, predicted: float, observed: float
    ) -> float:
        """PE = |predicted_confidence - observed_confidence|"""
        return abs(predicted - observed)

    def compute_aggregate_pe(self, pe_dict: dict[str, float]) -> float:
        """Mean PE across all beliefs this cycle. Fed to SEC."""
        if not pe_dict:
            return 0.0
        return sum(pe_dict.values()) / len(pe_dict)
