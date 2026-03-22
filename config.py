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
    searxng_url: str = "http://localhost:8080/search"
    brave_api_key: str = ""  # legacy, unused
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

    # Safety
    sandbox: bool = False  # block shell_exec/code_exec on local machines

    # Beta persona
    persona: str = ""  # "crypto_trader", "ai_founder", "ai_phd", or "" for default
    etherscan_api_key: str = ""

    # Scheduler
    scheduler_interval: float = 60.0
    inter_user_delay: float = 2.0

    # Safety caps
    max_beliefs_per_brain: int = 2000          # soft cap — force prune lowest-confidence when exceeded
    belief_cap_prune_batch: int = 50           # how many to prune when cap hit

    # Goal health
    goal_priority_decay: float = 0.02          # priority drops per cycle for ENDOGENOUS goals
    goal_max_age_cycles: int = 100             # ENDOGENOUS goals auto-abandon after this many cycles
    goal_hysteresis_buffer: int = 2            # PE must stay below complete threshold for N consecutive cycles

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

    # Email notifications
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    notification_email: str = ""
    daily_digest_enabled: bool = False
    weekly_digest_enabled: bool = False
    realtime_alerts_enabled: bool = True
    digest_hour: int = 8

    # IMAP (inbox reading — Gmail)
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_pass: str = ""

    # Outreach rate limits
    outreach_per_cycle: int = 1            # max 1 email per cycle (~5-10min)
    outreach_per_domain_per_day: int = 2   # same domain max 2/day
    followup_hours: float = 72.0

    # Sibling communication
    sibling_name: str = "local"            # this instance's name
    sibling_url: str = ""                  # URL of sibling's mailbox API
