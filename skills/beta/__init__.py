"""Skuld Beta skills — persona-specific skill modules."""

# ── Crypto Trader Persona (5 skills) ──

try:
    from .crypto_price import CryptoPriceSkill
except ImportError:
    CryptoPriceSkill = None  # type: ignore[assignment, misc]

try:
    from .price_alert import PriceAlertSkill
except ImportError:
    PriceAlertSkill = None  # type: ignore[assignment, misc]

try:
    from .onchain_data import OnchainDataSkill
except ImportError:
    OnchainDataSkill = None  # type: ignore[assignment, misc]

try:
    from .sentiment_scan import SentimentScanSkill
except ImportError:
    SentimentScanSkill = None  # type: ignore[assignment, misc]

try:
    from .portfolio_track import PortfolioTrackSkill
except ImportError:
    PortfolioTrackSkill = None  # type: ignore[assignment, misc]

# ── AI Startup Founder Persona (4 skills) ──

try:
    from .daily_brief import DailyBriefSkill
except ImportError:
    DailyBriefSkill = None  # type: ignore[assignment, misc]

try:
    from .competitor_watch import CompetitorWatchSkill
except ImportError:
    CompetitorWatchSkill = None  # type: ignore[assignment, misc]

try:
    from .rss_monitor import RssMonitorSkill
except ImportError:
    RssMonitorSkill = None  # type: ignore[assignment, misc]

try:
    from .meeting_prep import MeetingPrepSkill
except ImportError:
    MeetingPrepSkill = None  # type: ignore[assignment, misc]

# ── AI PhD Student Persona (4 skills) ──

try:
    from .arxiv_tracker import ArxivTrackerSkill
except ImportError:
    ArxivTrackerSkill = None  # type: ignore[assignment, misc]

try:
    from .paper_reader import PaperReaderSkill
except ImportError:
    PaperReaderSkill = None  # type: ignore[assignment, misc]

try:
    from .citation_graph import CitationGraphSkill
except ImportError:
    CitationGraphSkill = None  # type: ignore[assignment, misc]

try:
    from .experiment_log import ExperimentLogSkill
except ImportError:
    ExperimentLogSkill = None  # type: ignore[assignment, misc]

__all__ = [
    # Crypto Trader
    "CryptoPriceSkill",
    "PriceAlertSkill",
    "OnchainDataSkill",
    "SentimentScanSkill",
    "PortfolioTrackSkill",
    # AI Startup Founder
    "DailyBriefSkill",
    "CompetitorWatchSkill",
    "RssMonitorSkill",
    "MeetingPrepSkill",
    # AI PhD Student
    "ArxivTrackerSkill",
    "PaperReaderSkill",
    "CitationGraphSkill",
    "ExperimentLogSkill",
]
