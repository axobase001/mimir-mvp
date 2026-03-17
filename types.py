from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class BeliefSource(Enum):
    SEED = "seed"
    OBSERVATION = "observation"
    INFERENCE = "inference"
    ABSTRACTION = "abstraction"


class BeliefCategory(Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    PROCEDURE = "procedure"
    HYPOTHESIS = "hypothesis"


class GoalStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class GoalOrigin(Enum):
    ENDOGENOUS = "endogenous"
    EXOGENOUS = "exogenous"


class PEType(Enum):
    OBSERVATION = "observation"
    ACTION = "action"
    INTERACTION = "interaction"


@dataclass
class TypedPE:
    pe_type: PEType
    value: float
    cycle: int = 0
    source_id: str = ""

    def __float__(self) -> float:
        return self.value


@dataclass
class Belief:
    id: str
    statement: str
    confidence: float
    source: BeliefSource
    created_at: int
    last_updated: int
    last_verified: int
    pe_history: list[float] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    parent_ids: list[str] = field(default_factory=list)
    category: BeliefCategory = BeliefCategory.FACT


@dataclass
class SECEntry:
    cluster: str
    d_obs: float = 0.0
    d_not: float = 0.0
    obs_count: int = 0
    not_count: int = 0

    @property
    def c_value(self) -> float:
        """Positive C = searching reduced PE (useful). Negative C = searching didn't help."""
        if self.obs_count < 2 or self.not_count < 2:
            return 0.0
        return self.d_not - self.d_obs


@dataclass
class Episode:
    cycle: int
    action: str
    outcome: str
    pe_before: float
    pe_after: float
    beliefs_affected: list[str] = field(default_factory=list)


@dataclass
class Procedure:
    id: str
    description: str
    steps: list[str]
    success_count: int = 0
    failure_count: int = 0
    last_failure_reason: str = ""
    avg_pe: float = 0.0


@dataclass
class Goal:
    id: str
    target_belief_id: str
    description: str
    reason: str
    status: GoalStatus = GoalStatus.ACTIVE
    created_at: int = 0
    priority: float = 0.0
    origin: GoalOrigin = GoalOrigin.ENDOGENOUS
