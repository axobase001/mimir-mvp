from dataclasses import dataclass, field


@dataclass
class MimirConfig:
    # SEC
    sec_alpha: float = 0.1
    sec_warmup_cycles: int = 5
    sec_probe_reject_rate: float = 0.4
    sec_coverage_threshold: float = 0.8

    # Belief graph
    confidence_decay_rate: float = 0.02
    confidence_adjustment_rate: float = 0.3
    inference_confidence_discount: float = 0.7
    min_confidence_to_keep: float = 0.05
    max_pe_history: int = 20
    propagation_rate: float = 0.1

    # Goal generation
    goal_pe_threshold: float = 0.3
    goal_pe_persistence: int = 3
    goal_staleness_threshold: int = 20
    max_active_goals: int = 5

    # Memory
    max_episodes: int = 200
    max_procedures: int = 50

    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.3

    # Search
    brave_api_key: str = ""
    search_budget_per_cycle: int = 3

    # Cycle
    cycle_interval_seconds: float = 60.0
    reasoning_interval: int = 5
    abstraction_interval: int = 10

    # Notifications
    pe_jump_threshold: float = 0.5
    periodic_report_interval: int = 10

    # Multi-user / Auth
    jwt_secret: str = ""
    jwt_expire_hours: int = 72
    max_users: int = 1000
    default_llm_key: str = ""
    default_brave_key: str = ""
    free_cycles_per_day: int = 3
    free_beliefs_limit: int = 500
    pro_cycles_per_day: int = 20
    pro_beliefs_limit: int = -1

    # Scheduler
    scheduler_interval: float = 60.0
    inter_user_delay: float = 2.0

    # Belief category parameters
    belief_decay_rates: dict = field(default_factory=lambda: {
        "fact": 0.03,
        "preference": 0.005,
        "procedure": 0.01,
        "hypothesis": 0.05,
    })
    belief_pe_sensitivity: dict = field(default_factory=lambda: {
        "fact": 1.0,
        "preference": 0.3,
        "procedure": 0.5,
        "hypothesis": 1.5,
    })
    belief_min_confidence_to_keep: dict = field(default_factory=lambda: {
        "fact": 0.05,
        "preference": 0.2,
        "procedure": 0.1,
        "hypothesis": 0.03,
    })

    # PE type weights for SEC
    sec_pe_weights: dict = field(default_factory=lambda: {
        "observation": 1.0,
        "action": 0.5,
        "interaction": 0.3,
    })
