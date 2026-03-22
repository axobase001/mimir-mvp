from ..dtypes import Belief, PEType, TypedPE
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
        self,
        belief_id: str,
        predicted: float,
        observed: float,
        pe_type: PEType = PEType.OBSERVATION,
        cycle: int = 0,
    ) -> TypedPE:
        """PE = |predicted_confidence - observed_confidence|

        Returns a TypedPE that also behaves as float via float().
        """
        value = abs(predicted - observed)
        return TypedPE(
            pe_type=pe_type,
            value=value,
            cycle=cycle,
            source_id=belief_id,
        )

    def compute_action_pe(
        self, expected: float, actual: float, cycle: int = 0, source_id: str = ""
    ) -> TypedPE:
        """Compute PE for an action outcome."""
        return TypedPE(
            pe_type=PEType.ACTION,
            value=abs(expected - actual),
            cycle=cycle,
            source_id=source_id,
        )

    def compute_interaction_pe(
        self, expected: float, actual: float, cycle: int = 0, source_id: str = ""
    ) -> TypedPE:
        """Compute PE for user interaction feedback."""
        return TypedPE(
            pe_type=PEType.INTERACTION,
            value=abs(expected - actual),
            cycle=cycle,
            source_id=source_id,
        )

    def compute_aggregate_pe(
        self, pe_dict: dict[str, float | TypedPE]
    ) -> float:
        """Weighted mean PE across all beliefs this cycle. Fed to SEC.

        If values are TypedPE, applies sec_pe_weights by type.
        If values are plain floats, falls back to simple mean (backward compatible).
        """
        if not pe_dict:
            return 0.0

        weights = self.config.sec_pe_weights
        total_weighted = 0.0
        total_weight = 0.0

        for key, pe in pe_dict.items():
            if isinstance(pe, TypedPE):
                w = weights.get(pe.pe_type.value, 1.0)
                total_weighted += pe.value * w
                total_weight += w
            else:
                # Backward compatible: plain float, weight=1.0
                total_weighted += float(pe)
                total_weight += 1.0

        if total_weight == 0.0:
            return 0.0
        return total_weighted / total_weight
