"""BrainScheduler — multi-Brain cycle scheduler for Mimir."""

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
from ..skills.registry import SmartSkillRegistry
from ..skills.search import BraveSearchSkill
from ..skills.file_io import FileReadSkill, FileWriteSkill
from ..skills.code_exec import CodeExecSkill
from ..skills.document import DocumentSkill
from ..skills.web_fetch import WebFetchSkill
from ..skills.data_analysis import DataAnalysisSkill
from ..types import Belief, BeliefSource
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
        brave_key = keys["brave_api_key"] or self.config.default_brave_key

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

        registry = SmartSkillRegistry()
        if brave_key:
            registry.register(BraveSearchSkill(brave_key))
        registry.register(FileReadSkill())
        registry.register(FileWriteSkill())
        registry.register(CodeExecSkill())
        registry.register(DocumentSkill())
        registry.register(WebFetchSkill())
        registry.register(DataAnalysisSkill())

        action_engine = ActionEngine(
            skill_registry=registry,
            memory=mem,
            notifier=notifier,
            internal_llm=internal,
            external_llm=external,
        )

        scheduled_tasks = ScheduledTaskManager()

        engine = MimirCycle(
            belief_graph=bg, sec_matrix=sec, prediction_engine=pe_engine,
            goal_generator=goal_gen, memory=mem,
            internal_llm=internal, external_llm=external,
            skill_registry=registry, notifier=notifier, config=self.config,
            dedup=dedup, ws_manager=self.ws_manager,
            action_engine=action_engine,
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
