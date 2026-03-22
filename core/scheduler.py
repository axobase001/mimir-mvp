"""BrainScheduler — multi-Brain cycle scheduler for Skuld."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from ..brain.belief_graph import BeliefGraph
from ..brain.sec_matrix import SECMatrix
from ..brain.prediction import PredictionEngine
from ..brain.goal_generator import GoalGenerator
from ..brain.memory import Memory
from ..llm.client import LLMClient
from ..llm.internal import InternalLLM
from ..llm.external import ExternalLLM
from ..core.cycle import MimirCycle
from ..core.notifier import Notifier
from ..core.dedup import BeliefDeduplicator
from ..core.action_engine import ActionEngine
from ..core.scheduled_tasks import ScheduledTaskManager
from ..core.email_notifier import EmailNotifier, EmailConfig
from ..skills.registry import SmartSkillRegistry
from ..skills.search import WebSearchSkill
from ..skills.file_io import FileReadSkill, FileWriteSkill
from ..skills.code_exec import CodeExecSkill
from ..skills.document import DocumentSkill
from ..skills.web_fetch import WebFetchSkill
from ..skills.data_analysis import DataAnalysisSkill
from ..skills.shell_exec import ShellExecSkill
from ..skills.screenshot import ScreenshotSkill
from ..skills.calendar_ical import CalendarSkill
from ..skills.slack_webhook import SlackWebhookSkill
from ..skills.json_query import JSONQuerySkill
from ..skills.translate import TranslateSkill
from ..skills.summarize_url import SummarizeURLSkill
from ..skills.custom_tool import CustomToolManager
from ..skills.email_skill import EmailSkill
from ..skills.scholar_search import ScholarSearchSkill
from ..skills.sibling_message import SiblingMessageSkill
from ..skills.email_read import EmailReadSkill
from ..skills.outreach import OutreachRateLimiter, OutreachTracker, FollowUpManager
from ..core.contact_registry import ContactRegistry
from ..dtypes import Belief, BeliefSource
from ..config import MimirConfig
from ..state import MimirState
from ..storage.user_db import UserDB
from ..storage.brain_store import BrainStore

log = logging.getLogger(__name__)


class BrainScheduler:
    """Manages multiple Brain cycles across users."""

    def __init__(
        self,
        config: MimirConfig,
        user_db: UserDB,
        brain_store: BrainStore,
        ws_manager=None,
    ):
        self.config = config
        self.user_db = user_db
        self.brain_store = brain_store
        self.ws_manager = ws_manager

        # user_id -> MimirCycle
        self._running_brains: dict[str, MimirCycle] = {}
        # user_id -> auxiliary state (notifier, llm_client, etc.)
        self._brain_state: dict[str, dict] = {}
        self._stop_event = asyncio.Event()
        self._loop_task: Optional[asyncio.Task] = None

    async def start_brain(self, user_id: str, seed_beliefs: list[dict] = None) -> None:
        """Initialize and start a Brain for a user."""
        if user_id in self._running_brains:
            log.warning("Brain already running for user %s", user_id)
            return

        user = self.user_db.get_user(user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found")

        # Determine API keys — user's own or defaults
        keys = self.user_db.get_decrypted_keys(user_id)
        llm_key = keys["llm_api_key"] or self.config.default_llm_key
        brave_key = keys.get("brave_api_key") or self.config.default_brave_key  # legacy, unused

        # Check if restoring from saved state
        saved = self.brain_store.load_brain(user_id)

        if saved is not None:
            bg, sec, mem, goals, cycle_count, usage = MimirState.load_from_dict(
                saved, self.config
            )
            goal_gen = GoalGenerator(self.config, bg, sec)
            goal_gen.goals = goals
            goal_gen._counter = max(
                (int(g.id.split("_")[1]) for g in goals.values()), default=0
            )
        else:
            # Fresh brain from seeds
            if not seed_beliefs:
                raise ValueError("No seed beliefs provided and no saved state")
            bg = BeliefGraph(self.config)
            for i, seed in enumerate(seed_beliefs):
                b = Belief(
                    id=f"seed_{i:03d}",
                    statement=seed["statement"],
                    confidence=seed.get("confidence", 0.7),
                    source=BeliefSource.SEED,
                    created_at=0, last_updated=0, last_verified=0,
                    tags=seed.get("tags", []),
                )
                bg.add_belief(b)
            sec = SECMatrix(self.config)
            mem = Memory(self.config)
            goal_gen = GoalGenerator(self.config, bg, sec)
            cycle_count = 0

        pe_engine = PredictionEngine(self.config)
        notifier = Notifier()

        llm_client = LLMClient(
            api_key=llm_key,
            base_url=self.config.llm_base_url,
            model=self.config.llm_model,
            max_tokens=self.config.llm_max_tokens,
            temperature=self.config.llm_temperature,
        )
        internal = InternalLLM(llm_client, self.config)
        external = ExternalLLM(llm_client, self.config)
        dedup = BeliefDeduplicator(llm_client, self.config)

        registry = SmartSkillRegistry(sandbox=getattr(self.config, 'sandbox', False))
        registry.register(WebSearchSkill(self.config.searxng_url))
        registry.register(FileReadSkill())
        registry.register(FileWriteSkill())
        registry.register(CodeExecSkill())
        registry.register(DocumentSkill())
        registry.register(WebFetchSkill())
        registry.register(DataAnalysisSkill())
        registry.register(ShellExecSkill())
        registry.register(ScreenshotSkill())
        registry.register(CalendarSkill())
        registry.register(SlackWebhookSkill())
        registry.register(JSONQuerySkill())
        registry.register(TranslateSkill(llm_client=llm_client))
        registry.register(SummarizeURLSkill(llm_client=llm_client))

        # Email skills — Resend API with rate limiting and outreach tracking
        outreach_limiter = OutreachRateLimiter(
            per_cycle=self.config.outreach_per_cycle,
            per_domain_per_day=self.config.outreach_per_domain_per_day,
        )
        outreach_tracker = OutreachTracker(belief_graph=bg)
        contact_registry = ContactRegistry(belief_graph=bg)
        follow_up_mgr = FollowUpManager(
            tracker=outreach_tracker,
            rate_limiter=outreach_limiter,
            hours_before_followup=self.config.followup_hours,
        )
        email_skill = EmailSkill(
            rate_limiter=outreach_limiter,
            outreach_tracker=outreach_tracker,
        )
        # Sent addresses are loaded from Resend API in EmailSkill.__init__
        # (shared across all Skuld instances via same API key)
        email_skill.contact_registry = contact_registry
        registry.register(email_skill)
        registry.register(ScholarSearchSkill(
            searxng_url=self.config.searxng_url,
            contact_registry=contact_registry,
        ))
        if self.config.sibling_url:
            registry.register(SiblingMessageSkill(
                my_name=self.config.sibling_name,
                sibling_url=self.config.sibling_url,
            ))
        email_read_skill = EmailReadSkill(
            imap_host=self.config.imap_host,
            imap_port=self.config.imap_port,
            imap_user=self.config.imap_user,
            imap_pass=self.config.imap_pass,
            outreach_tracker=outreach_tracker,
            my_name=getattr(self.config, 'sibling_name', ''),
        )
        registry.register(email_read_skill)

        # Load user-defined custom tools
        custom_mgr = CustomToolManager()
        for custom_skill in custom_mgr.load_tools():
            registry.register(custom_skill)

        # ToolForge — let Skuld create its own Python tools at runtime
        from ..skills.tool_forge import ToolForgeSkill
        tool_forge = ToolForgeSkill(registry=registry)
        registry.register(tool_forge)
        # Also register any previously forged tools
        for forged_skill in tool_forge.get_forged_skills():
            registry.register(forged_skill)

        # ── Beta persona skills ──
        persona = getattr(self.config, 'persona', '')
        if persona:
            self._register_persona_skills(persona, registry, llm_client)

        action_engine = ActionEngine(
            skill_registry=registry,
            memory=mem,
            notifier=notifier,
            internal_llm=internal,
            external_llm=external,
            belief_graph=bg,
            contact_registry=contact_registry,
        )

        scheduled_tasks = ScheduledTaskManager()

        # Email notifier — only for real email addresses (skip dev/local addresses)
        user_email = self.config.notification_email or (
            user.get("email", "") if isinstance(user, dict)
            else getattr(user, "email", "")
        )
        # Don't waste Resend quota on fake dev addresses
        is_real_email = (
            user_email
            and "@" in user_email
            and not user_email.endswith(".local")
            and not user_email.endswith("@example.com")
        )
        email_config = EmailConfig(
            to_addr=user_email if is_real_email else "",
            enabled=is_real_email,
            daily_digest=self.config.daily_digest_enabled,
            weekly_digest=self.config.weekly_digest_enabled,
            realtime_alerts=self.config.realtime_alerts_enabled,
            digest_hour=self.config.digest_hour,
            # Legacy SMTP fields (unused with Resend)
            smtp_host=self.config.smtp_host,
            smtp_port=self.config.smtp_port,
            smtp_user=self.config.smtp_user,
            smtp_pass=self.config.smtp_pass,
            from_addr=self.config.smtp_user,
        )
        email_notifier = EmailNotifier(email_config) if email_config.enabled else None
        if email_notifier:
            log.info("Email notifier configured for user %s (to=%s)",
                     user_id, email_config.to_addr)

        engine = MimirCycle(
            belief_graph=bg, sec_matrix=sec, prediction_engine=pe_engine,
            goal_generator=goal_gen, memory=mem,
            internal_llm=internal, external_llm=external,
            skill_registry=registry, notifier=notifier, config=self.config,
            dedup=dedup, ws_manager=self.ws_manager,
            action_engine=action_engine,
            email_notifier=email_notifier,
        )
        engine.cycle_count = cycle_count

        self._running_brains[user_id] = engine
        self._brain_state[user_id] = {
            "belief_graph": bg,
            "sec_matrix": sec,
            "memory": mem,
            "goal_generator": goal_gen,
            "notifier": notifier,
            "llm_client": llm_client,
            "external_llm": external,
            "internal_llm": internal,
            "dedup": dedup,
            "skill_registry": registry,
            "action_engine": action_engine,
            "scheduled_tasks": scheduled_tasks,
            "custom_tool_manager": custom_mgr,
            "email_notifier": email_notifier,
            "outreach_limiter": outreach_limiter,
            "outreach_tracker": outreach_tracker,
            "follow_up_mgr": follow_up_mgr,
            "email_read_skill": email_read_skill,
            "contact_registry": contact_registry,
        }

        # Save initial state
        self._save_brain_state(user_id)
        log.info("Brain started for user %s (cycle=%d, beliefs=%d)",
                 user_id, cycle_count, len(bg.get_all_beliefs()))

    async def stop_brain(self, user_id: str) -> None:
        """Stop and save a user's Brain."""
        if user_id in self._running_brains:
            self._save_brain_state(user_id)
            del self._running_brains[user_id]
            del self._brain_state[user_id]
            log.info("Brain stopped for user %s", user_id)

    def _register_persona_skills(self, persona: str, registry, llm_client=None) -> None:
        """Register persona-specific beta skills."""
        try:
            if persona == "crypto_trader":
                from ..skills.beta.crypto_price import CryptoPriceSkill
                from ..skills.beta.price_alert import PriceAlertSkill
                from ..skills.beta.onchain_data import OnchainDataSkill
                from ..skills.beta.sentiment_scan import SentimentScanSkill
                from ..skills.beta.portfolio_track import PortfolioTrackSkill
                registry.register(CryptoPriceSkill())
                registry.register(PriceAlertSkill())
                registry.register(OnchainDataSkill(
                    api_key=getattr(self.config, 'etherscan_api_key', '')))
                registry.register(SentimentScanSkill(
                    searxng_url=self.config.searxng_url))
                registry.register(PortfolioTrackSkill())
                log.info("Persona skills registered: crypto_trader (5 skills)")

            elif persona == "ai_founder":
                from ..skills.beta.daily_brief import DailyBriefSkill
                from ..skills.beta.competitor_watch import CompetitorWatchSkill
                from ..skills.beta.rss_monitor import RssMonitorSkill
                from ..skills.beta.meeting_prep import MeetingPrepSkill
                registry.register(DailyBriefSkill(
                    searxng_url=self.config.searxng_url))
                registry.register(CompetitorWatchSkill(
                    searxng_url=self.config.searxng_url))
                registry.register(RssMonitorSkill())
                registry.register(MeetingPrepSkill(
                    searxng_url=self.config.searxng_url))
                log.info("Persona skills registered: ai_founder (4 skills)")

            elif persona == "ai_phd":
                from ..skills.beta.arxiv_tracker import ArxivTrackerSkill
                from ..skills.beta.paper_reader import PaperReaderSkill
                from ..skills.beta.citation_graph import CitationGraphSkill
                from ..skills.beta.experiment_log import ExperimentLogSkill
                registry.register(ArxivTrackerSkill())
                registry.register(PaperReaderSkill(llm_client=llm_client))
                registry.register(CitationGraphSkill())
                registry.register(ExperimentLogSkill())
                log.info("Persona skills registered: ai_phd (4 skills)")

            else:
                log.warning("Unknown persona: %s, no extra skills registered", persona)

        except Exception as e:
            log.error("Failed to register persona skills for %s: %s", persona, e)

    def _save_brain_state(self, user_id: str) -> None:
        """Save brain state to disk."""
        engine = self._running_brains.get(user_id)
        state = self._brain_state.get(user_id)
        if engine is None or state is None:
            return

        state_data = MimirState.to_dict(
            state["belief_graph"],
            state["sec_matrix"],
            state["memory"],
            state["goal_generator"].goals,
            engine.cycle_count,
            state["llm_client"].get_usage_stats(),
        )
        self.brain_store.save_brain(user_id, state_data)

    async def run_cycle_for_user(self, user_id: str) -> Optional[dict]:
        """Run one cycle for a specific user, checking usage limits."""
        if user_id not in self._running_brains:
            return None

        # Check usage limit (skip for dev users)
        dev_uid = getattr(self, '_dev_user_id', None)
        if user_id != dev_uid and not self.user_db.check_limit(user_id, "cycles"):
            log.info("User %s hit cycle limit, skipping", user_id)
            return None

        engine = self._running_brains[user_id]
        state = self._brain_state[user_id]

        # Reset per-cycle email counter
        outreach_limiter = state.get("outreach_limiter")
        if outreach_limiter:
            outreach_limiter.reset_cycle()

        # Execute due scheduled tasks before the cycle
        sched_mgr: ScheduledTaskManager = state.get("scheduled_tasks")
        if sched_mgr is not None:
            action_eng = state.get("action_engine")
            if action_eng is not None:
                for task in sched_mgr.get_due_tasks():
                    if task.user_id != user_id:
                        continue
                    try:
                        plan = await action_eng.plan_action(
                            intent=task.intent,
                            goal=task.description,
                        )
                        if plan.get("skill_name"):
                            await action_eng.execute_action(plan, user_id=user_id)
                        sched_mgr.mark_executed(task.id)
                        log.info("Scheduled task executed: %s (%s)",
                                 task.id, task.description[:40])
                    except Exception as e:
                        log.warning("Scheduled task %s failed: %s", task.id, e)

        try:
            summary = await engine.run_one_cycle()

            # Update usage
            self.user_db.update_usage(
                user_id,
                cycles_delta=1,
                beliefs_count=len(state["belief_graph"].get_all_beliefs()),
            )

            # Drain notifications and broadcast
            for n in state["notifier"].pull_all():
                log.info("[User %s NOTIFY %s] %s: %s",
                         user_id, n.level.value, n.title, n.body)
                if self.ws_manager:
                    await self.ws_manager.broadcast_to_user(user_id, {
                        "type": "notification",
                        "level": n.level.value,
                        "title": n.title,
                        "body": n.body,
                        "cycle": n.cycle,
                    })

            # Broadcast cycle end
            if self.ws_manager:
                await self.ws_manager.broadcast_to_user(user_id, {
                    "type": "cycle_end",
                    "cycle": summary.get("cycle"),
                    "beliefs": summary.get("belief_count"),
                    "goals": summary.get("active_goals"),
                })

            # Proactive message push
            proactive_msg = summary.get("proactive_message")
            if proactive_msg and self.ws_manager:
                await self.ws_manager.broadcast_to_user(user_id, {
                    "type": "proactive_message",
                    "message": proactive_msg,
                    "cycle": summary.get("cycle"),
                })
                log.info("Proactive message sent to user %s: %s",
                         user_id, proactive_msg[:80])

            # Email digest checks
            email_notifier: Optional[EmailNotifier] = state.get("email_notifier")
            if email_notifier:
                if email_notifier.should_send_daily():
                    brain_state = self._build_digest_data(user_id, summary)
                    subject, html = email_notifier.build_daily_digest(brain_state)
                    await email_notifier.send_email_async(subject, html)
                    email_notifier.mark_daily_sent()
                    log.info("Daily digest sent for user %s", user_id)

                if email_notifier.should_send_weekly():
                    brain_state = self._build_digest_data(user_id, summary)
                    subject, html = email_notifier.build_weekly_digest(brain_state)
                    await email_notifier.send_email_async(subject, html)
                    email_notifier.mark_weekly_sent()
                    log.info("Weekly digest sent for user %s", user_id)

            # Check inbox for replies from outreach contacts
            email_reader: Optional[EmailReadSkill] = state.get("email_read_skill")
            if email_reader and email_reader._imap_user:
                try:
                    matched = email_reader.check_replies()
                    for reply in matched:
                        log.info("Outreach reply from %s <%s>: %s",
                                 reply["contact_name"], reply["contact_email"],
                                 reply["subject"][:60])
                        if self.ws_manager:
                            await self.ws_manager.broadcast_to_user(user_id, {
                                "type": "discovery",
                                "title": f"{reply['contact_name']} replied!",
                                "body": f"Subject: {reply['subject'][:80]}",
                            })
                except Exception as e:
                    log.warning("Inbox check failed: %s", e)

                # Check family mail (from dad)
                try:
                    family = email_reader.check_family_mail()
                    for fm in family:
                        log.info("Family mail from %s: %s", fm["from"], fm["subject"][:60])
                        # Inject into brain as a chat message
                        engine = state.get("cycle_engine")
                        if engine and hasattr(engine, "bg"):
                            from ..dtypes import Belief, BeliefSource
                            b = Belief(
                                id="",
                                statement=f"父亲来信: {fm['subject']} — {fm['body'][:200]}",
                                confidence=0.95,
                                source=BeliefSource.OBSERVATION,
                                created_at=engine.cycle_count,
                                last_updated=engine.cycle_count,
                                last_verified=engine.cycle_count,
                                tags=["family", "dad"],
                            )
                            engine.bg.add_belief(b)
                            log.info("Family mail added as belief")
                except Exception as e:
                    log.warning("Family mail check failed: %s", e)

            # Follow-up checks
            follow_up_mgr: Optional[FollowUpManager] = state.get("follow_up_mgr")
            if follow_up_mgr:
                pending = follow_up_mgr.get_pending_followups()
                if pending:
                    action_eng = state.get("action_engine")
                    for contact in pending[:2]:  # max 2 follow-ups per cycle
                        allowed, _ = follow_up_mgr.rate_limiter.can_send(contact.email)
                        if not allowed:
                            continue
                        if action_eng is not None:
                            try:
                                plan = await action_eng.plan_action(
                                    intent=f"Send a polite follow-up email to {contact.name} <{contact.email}> "
                                           f"from {contact.org}. Reference the previous outreach. Be brief.",
                                    goal="outreach_followup",
                                )
                                if plan.get("skill_name") == "email":
                                    plan["params"]["contact_name"] = contact.name
                                    plan["params"]["contact_org"] = contact.org
                                    await action_eng.execute_action(plan, user_id=user_id)
                                    follow_up_mgr.mark_followed_up(contact.email)
                                    log.info("Follow-up sent to %s <%s>",
                                             contact.name, contact.email)
                            except Exception as e:
                                log.warning("Follow-up failed for %s: %s", contact.email, e)

            # Save state
            self._save_brain_state(user_id)

            return summary

        except Exception as e:
            log.error("Cycle error for user %s: %s", user_id, e, exc_info=True)
            return None

    async def run_scheduler_loop(self) -> None:
        """Main scheduler loop: periodically run cycles for all active brains."""
        log.info("Scheduler started (interval=%.0fs, inter_user_delay=%.1fs)",
                 self.config.scheduler_interval, self.config.inter_user_delay)

        while not self._stop_event.is_set():
            # Restore any brains from disk that aren't running
            await self._restore_saved_brains()

            user_ids = list(self._running_brains.keys())
            if user_ids:
                log.info("Scheduler tick: %d active brains", len(user_ids))

            for uid in user_ids:
                if self._stop_event.is_set():
                    break
                await self.run_cycle_for_user(uid)
                # Inter-user delay
                if self.config.inter_user_delay > 0 and uid != user_ids[-1]:
                    await asyncio.sleep(self.config.inter_user_delay)

            # Wait for next interval
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.scheduler_interval,
                )
                break  # stop event was set
            except asyncio.TimeoutError:
                pass  # normal timeout, continue loop

    async def _restore_saved_brains(self) -> None:
        """Restore any brains from disk that aren't currently running."""
        for uid in self.brain_store.list_active_brains():
            if uid not in self._running_brains:
                user = self.user_db.get_user(uid)
                if user is not None:
                    try:
                        await self.start_brain(uid)
                    except Exception as e:
                        log.warning("Failed to restore brain for %s: %s", uid, e)

    async def daily_reset(self) -> None:
        """Reset daily cycle counts for all users."""
        count = self.user_db.reset_daily_cycles()
        log.info("Daily reset: %d users reset", count)

    def _build_digest_data(self, user_id: str, summary: dict) -> dict:
        """Build digest data dict from brain state and cycle summary."""
        state = self._brain_state.get(user_id, {})
        engine = self._running_brains.get(user_id)
        bg = state.get("belief_graph")
        sec = state.get("sec_matrix")
        gg = state.get("goal_generator")
        llm_client = state.get("llm_client")

        belief_count = len(bg.get_all_beliefs()) if bg else 0
        cost = 0
        if llm_client:
            usage = llm_client.get_usage_stats()
            cost = usage.get("total_cost", 0)

        sec_top = []
        if sec:
            sec_top = sec.get_top_clusters(5)

        active_goals = []
        if gg:
            from ..dtypes import GoalStatus
            active_goals = [
                g.description for g in gg.goals.values()
                if g.status == GoalStatus.ACTIVE
            ]

        return {
            'belief_count': belief_count,
            'cycle': engine.cycle_count if engine else 0,
            'cost': cost,
            'new_beliefs_24h': sum(o.get('new_beliefs_count', 0) for o in summary.get('phases', {}).get('observe', [])) if isinstance(summary.get('phases', {}).get('observe'), list) else 0,
            'pruned_24h': len(summary.get('phases', {}).get('prune', {}).get('pruned', [])),
            'sec_top': sec_top,
            'active_goals': active_goals,
            'discoveries': [],
        }

    def get_brain_status(self, user_id: str) -> Optional[dict]:
        """Get status of a specific user's Brain."""
        if user_id not in self._running_brains:
            return None
        engine = self._running_brains[user_id]
        state = self._brain_state[user_id]
        return {
            "user_id": user_id,
            "running": True,
            "cycle_count": engine.cycle_count,
            "belief_count": len(state["belief_graph"].get_all_beliefs()),
            "usage_stats": state["llm_client"].get_usage_stats(),
        }

    def get_brain_engine(self, user_id: str) -> Optional[MimirCycle]:
        """Get the MimirCycle engine for a user."""
        return self._running_brains.get(user_id)

    def get_brain_state(self, user_id: str) -> Optional[dict]:
        """Get auxiliary state dict for a user."""
        return self._brain_state.get(user_id)

    def get_all_status(self) -> dict:
        """Get status of all running Brains."""
        return {
            "active_brains": len(self._running_brains),
            "brains": {
                uid: self.get_brain_status(uid)
                for uid in self._running_brains
            },
        }

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._stop_event.set()
        # Save all states
        for uid in list(self._running_brains.keys()):
            self._save_brain_state(uid)
        log.info("Scheduler stop signal sent, %d brains saved",
                 len(self._running_brains))
