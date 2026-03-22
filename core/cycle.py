"""
SKULD CORE PRINCIPLE

User intent > Brain judgment. Always.

Brain may explain its reasoning. Brain may suggest alternatives.
Brain executes user commands faithfully regardless of its own
assessment. EXOGENOUS goals bypass SEC filtering. User overrides
are immediate and non-negotiable.

Brain's autonomy operates in the space the user has not claimed.
"""

import asyncio
import json
from pathlib import Path
import logging
import random
import re
from collections import Counter

from ..brain.belief_graph import BeliefGraph
from ..brain.sec_matrix import SECMatrix
from ..brain.prediction import PredictionEngine
from ..brain.goal_generator import GoalGenerator
from ..brain.memory import Memory
from ..llm.internal import InternalLLM
from ..llm.external import ExternalLLM
from ..skills.base import SkillRegistry
from ..skills.registry import SmartSkillRegistry
from ..dtypes import Belief, BeliefCategory, BeliefSource, Episode, GoalOrigin, GoalStatus, PEType, Procedure, TypedPE
from ..config import MimirConfig
from .notifier import Notifier, Notification, NotifyLevel
from .email_notifier import EmailNotifier

log = logging.getLogger(__name__)


class MimirCycle:
    def __init__(
        self,
        belief_graph: BeliefGraph,
        sec_matrix: SECMatrix,
        prediction_engine: PredictionEngine,
        goal_generator: GoalGenerator,
        memory: Memory,
        internal_llm: InternalLLM,
        external_llm: ExternalLLM,
        skill_registry: SkillRegistry | SmartSkillRegistry,
        notifier: Notifier,
        config: MimirConfig,
        dedup=None,
        ws_manager=None,
        action_engine=None,
        email_notifier: EmailNotifier | None = None,
    ):
        self.bg = belief_graph
        self.sec = sec_matrix
        self.pe_engine = prediction_engine
        self.goal_gen = goal_generator
        self.mem = memory
        self.internal = internal_llm
        self.external = external_llm
        self.skills = skill_registry
        self.notifier = notifier
        self.config = config
        self.dedup = dedup
        self.ws_manager = ws_manager
        self.action_engine = action_engine
        self.email_notifier: EmailNotifier | None = email_notifier
        self.cycle_count = 0
        self.fast_path_hits = 0
        self.fast_path_misses = 0
        # Proactive messaging state
        self._proactive_interval = 10  # every N cycles (when no conversation)
        self._proactive_unanswered = 0  # how many sent without user reply
        self._proactive_max_unanswered = 3  # stop after this many
        self._proactive_conversation_mode = False  # True = user just replied, respond next cycle

    # ──────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────

    def _select_candidates(self) -> list[Belief]:
        """Pick beliefs to observe this cycle, respecting SEC filter.

        Multi-goal fair scheduling: slots are distributed proportionally
        to goal priority across all active goals.  EXOGENOUS goals bypass
        SEC filtering entirely.
        """
        budget = self.config.search_budget_per_cycle
        active_goals = [
            g for g in self.goal_gen.goals.values()
            if g.status == GoalStatus.ACTIVE
        ]

        # ── Goal-driven slot allocation ──
        if active_goals:
            total_priority = sum(g.priority for g in active_goals) or 1.0
            goal_slots: list[tuple] = []  # (goal, slots)
            allocated = 0
            for g in sorted(active_goals, key=lambda g: g.priority, reverse=True):
                slots = round(budget * g.priority / total_priority)
                slots = max(0, slots)
                goal_slots.append((g, slots))
                allocated += slots

            # Ensure minimum fairness: every goal gets at least 1 slot
            # once every 3 cycles (tracked via cycle_count).
            for i, (g, slots) in enumerate(goal_slots):
                if slots == 0 and (self.cycle_count % 3 == (i % 3)):
                    goal_slots[i] = (g, 1)

            # Collect goal-driven candidates with fair allocation
            filtered: list[Belief] = []
            seen_ids: set[str] = set()

            for g, slots in goal_slots:
                if slots <= 0:
                    continue
                b = self.bg.get_belief(g.target_belief_id)
                if b is None or b.id in seen_ids:
                    continue

                # EXOGENOUS goals bypass SEC filter
                if g.origin == GoalOrigin.EXOGENOUS:
                    filtered.append(b)
                    seen_ids.add(b.id)
                    log.info(
                        "EXOGENOUS goal %s: belief %s bypasses SEC",
                        g.id, b.id,
                    )
                    continue

                # ENDOGENOUS goals go through SEC filter
                blocked = False
                for tag in b.tags:
                    if not self.sec.filter_action(tag, self.cycle_count):
                        log.info(
                            "SEC filtered belief %s (tag=%s, C=%.3f)",
                            b.id, tag, self.sec.get_c_value(tag),
                        )
                        blocked = True
                        break
                if not blocked:
                    filtered.append(b)
                    seen_ids.add(b.id)

            # Fill remaining budget from PE-sorted + stale beliefs
            remaining = budget - len(filtered)
            if remaining > 0:
                extras: list[Belief] = []
                # high PE beliefs
                for b in sorted(
                    self.bg.get_all_beliefs(),
                    key=lambda x: x.pe_history[-1] if x.pe_history else 0,
                    reverse=True,
                ):
                    if b.id not in seen_ids:
                        extras.append(b)
                # stale beliefs
                stale = self.bg.get_stale_beliefs(
                    self.cycle_count, self.config.goal_staleness_threshold
                )
                for b in stale:
                    if b.id not in seen_ids and b not in extras:
                        extras.append(b)

                for b in extras:
                    if len(filtered) >= budget:
                        break
                    blocked = False
                    for tag in b.tags:
                        if not self.sec.filter_action(tag, self.cycle_count):
                            blocked = True
                            break
                    if not blocked:
                        filtered.append(b)
                        seen_ids.add(b.id)

            return filtered[:budget]

        # ── No goals: original logic (PE sorted + stale, SEC filtered) ──
        candidates: list[Belief] = []
        for b in sorted(
            self.bg.get_all_beliefs(),
            key=lambda x: x.pe_history[-1] if x.pe_history else 0,
            reverse=True,
        ):
            if b not in candidates:
                candidates.append(b)

        stale = self.bg.get_stale_beliefs(
            self.cycle_count, self.config.goal_staleness_threshold
        )
        for b in stale:
            if b not in candidates:
                candidates.append(b)

        filtered = []
        for b in candidates:
            if len(filtered) >= budget:
                break
            blocked = False
            for tag in b.tags:
                if not self.sec.filter_action(tag, self.cycle_count):
                    log.info(
                        "SEC filtered belief %s (tag=%s, C=%.3f)",
                        b.id, tag, self.sec.get_c_value(tag),
                    )
                    blocked = True
                    break
            if not blocked:
                filtered.append(b)

        return filtered

    async def _should_act_this_cycle(self, focus_goal) -> tuple[bool, str]:
        """Determine whether phase 4b (action) should run."""
        # Condition 1: active goal priority > 0.5
        if focus_goal is not None and focus_goal.priority > 0.5:
            return True, f"goal_priority={focus_goal.priority:.2f}"

        # Condition 2: LLM-based decision
        if self.action_engine is not None and hasattr(self.internal, "should_act"):
            goal_desc = focus_goal.description if focus_goal else "free exploration"
            beliefs = self.bg.get_all_beliefs()
            belief_summary = "; ".join(
                f"{b.statement[:40]}({b.confidence:.1f})"
                for b in sorted(beliefs, key=lambda x: x.confidence, reverse=True)[:5]
            )
            recent_pe = 0.0
            if self.mem.episodes:
                recent_pe = self.mem.episodes[-1].pe_before
            try:
                should, reason = await self.internal.should_act(
                    goal_desc, belief_summary, recent_pe,
                )
                return should, reason
            except Exception as e:
                log.warning("should_act check failed: %s", e)

        return False, "no_trigger"

    # ──────────────────────────────────────
    #  Message classification & truth packet
    # ──────────────────────────────────────

    _INTERNAL_PATTERNS: list[re.Pattern] = [
        re.compile(p, re.IGNORECASE) for p in [
            r"你的信念图", r"信念图里", r"你的SEC", r"SEC矩阵", r"SEC是什么",
            r"SEC对.+反应", r"SEC怎么",
            r"你知道自己", r"你在聚焦", r"你的架构", r"你怎么工作", r"你怎么思考",
            r"你为什么", r"你知道", r"你怎么", r"你是谁", r"你能做什么", r"你能干什么",
            r"你有什么功能", r"你的能力", r"介绍一下你自己",
            r"belief.?graph", r"your beliefs", r"your SEC",
            r"how do you work", r"what do you know about yourself",
            r"who are you", r"what can you do",
            r"staleness.error", r"prediction error",
        ]
    ]
    _SOCIAL_PATTERNS: list[re.Pattern] = [
        re.compile(p, re.IGNORECASE) for p in [
            r"^你好", r"^hello", r"^hi\b", r"^hey\b", r"^嗨",
            r"^我是\S+$", r"^我叫", r"^谢谢", r"^thanks",
            r"^再见", r"^bye", r"^自我介绍",
        ]
    ]
    _MIXED_PATTERNS: list[re.Pattern] = [
        re.compile(p, re.IGNORECASE) for p in [
            r"你对.+的看法", r"你觉得.+怎么样", r"你怎么看.+",
            r"what do you think about", r"your opinion on",
        ]
    ]

    def _classify_message(self, query: str) -> str:
        """Classify user message into internal/social/mixed/external.

        Uses rule-based matching only (no LLM call).
        Returns one of: "internal", "social", "mixed", "external".
        """
        q = query.strip()

        # Check MIXED first (has both internal + external reference)
        for pat in self._MIXED_PATTERNS:
            if pat.search(q):
                return "mixed"

        # Check INTERNAL
        for pat in self._INTERNAL_PATTERNS:
            if pat.search(q):
                return "internal"

        # Check SOCIAL
        for pat in self._SOCIAL_PATTERNS:
            if pat.search(q):
                return "social"

        return "external"

    def _build_truth_packet(self) -> str:
        """Build a standardized truth packet from current Brain state.

        Pure memory read — no LLM calls. Returns JSON wrapped in markers.
        """
        # --- Belief graph stats ---
        all_beliefs = self.bg.get_all_beliefs()
        belief_count = len(all_beliefs)

        # Dominant topics: tag frequency top 3
        tag_counter: Counter = Counter()
        for b in all_beliefs:
            for t in b.tags:
                tag_counter[t] += 1
        dominant_topics = [tag for tag, _ in tag_counter.most_common(3)]

        # Source breakdown
        sources: dict[str, int] = {}
        for b in all_beliefs:
            sources[b.source.value] = sources.get(b.source.value, 0) + 1

        # Top beliefs by confidence
        top_beliefs = sorted(all_beliefs, key=lambda x: x.confidence, reverse=True)[:5]
        top_beliefs_list = [
            {"id": b.id, "statement": b.statement[:80], "confidence": round(b.confidence, 3),
             "source": b.source.value}
            for b in top_beliefs
        ]

        # --- SEC stats ---
        mature_entries = [
            (name, e) for name, e in self.sec.entries.items()
            if e.obs_count >= 2 and e.not_count >= 2
        ]
        pos_clusters = sum(1 for _, e in mature_entries if e.c_value > 0.01)
        neg_clusters = sum(1 for _, e in mature_entries if e.c_value < -0.01)

        # Top 3 attended tags by absolute C value
        sorted_sec = sorted(mature_entries, key=lambda x: abs(x[1].c_value), reverse=True)[:3]
        top_attended = [
            {"tag": name, "c_value": round(e.c_value, 4)} for name, e in sorted_sec
        ]

        # --- Memory stats ---
        recent_episodes = self.mem.episodes[-3:] if self.mem.episodes else []
        recent_cycles = [
            {"cycle": ep.cycle, "action": ep.action[:60], "pe": round(ep.pe_before, 4)}
            for ep in recent_episodes
        ]

        # --- Goals ---
        active_goals = [
            g for g in self.goal_gen.goals.values()
            if g.status == GoalStatus.ACTIVE
        ]
        goals_list = [
            {"origin": g.origin.value, "description": g.description[:60],
             "priority": round(g.priority, 3)}
            for g in active_goals
        ]

        packet = {
            "cycle": self.cycle_count,
            "belief_graph": {
                "total": belief_count,
                "sources": sources,
                "dominant_topics": dominant_topics,
                "top_beliefs": top_beliefs_list,
            },
            "sec": {
                "total_clusters": len(self.sec.entries),
                "positive_clusters": pos_clusters,
                "negative_clusters": neg_clusters,
                "top_attended": top_attended,
            },
            "memory": {
                "total_episodes": len(self.mem.episodes),
                "recent": recent_cycles,
            },
            "goals": {
                "active_count": len(active_goals),
                "goals": goals_list,
            },
        }

        packet_json = json.dumps(packet, ensure_ascii=False, indent=2)
        return f"[BRAIN TRUTH PACKET]\n{packet_json}\n[END TRUTH PACKET]"

    # ──────────────────────────────────────
    #  Main cycle
    # ──────────────────────────────────────

    async def run_one_cycle(self) -> dict:
        self.cycle_count += 1
        cycle = self.cycle_count
        summary: dict = {"cycle": cycle, "phases": {}}

        log.info("=== Cycle %d START ===", cycle)

        # -- Phase 1: WAKE --
        active_goals = [
            g for g in self.goal_gen.goals.values() if g.status == GoalStatus.ACTIVE
        ]
        # EXOGENOUS goals always take priority over ENDOGENOUS
        # (SKULD CORE PRINCIPLE: User intent > Brain judgment)
        focus_goal = max(
            active_goals,
            key=lambda g: (g.origin == GoalOrigin.EXOGENOUS, g.priority),
        ) if active_goals else None
        summary["phases"]["wake"] = {
            "active_goals": len(active_goals),
            "focus": focus_goal.description if focus_goal else "free exploration",
        }
        log.info("Phase 1 WAKE: %d active goals, focus=%s",
                 len(active_goals), summary["phases"]["wake"]["focus"])

        # -- Phase 1b: MEMO --
        memo_path = Path("data/memo.md")
        memo_content = ""
        if memo_path.exists():
            try:
                memo_content = memo_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        if memo_content:
            log.info("Phase 1b MEMO: %d chars loaded", len(memo_content))
            summary["phases"]["memo"] = memo_content[:200]

        # -- Phase 2: PREDICT --
        all_beliefs = self.bg.get_all_beliefs()
        predictions = self.pe_engine.generate_predictions(all_beliefs)
        summary["phases"]["predict"] = {"beliefs_predicted": len(predictions)}
        log.info("Phase 2 PREDICT: %d beliefs", len(predictions))

        # -- Phase 3: SELECT --
        targets = self._select_candidates()
        summary["phases"]["select"] = {
            "candidates": [b.id for b in targets],
        }
        log.info("Phase 3 SELECT: %d targets -> %s",
                 len(targets), [b.id for b in targets])

        # -- Phase 4a: OBSERVE --
        search_skill = self.skills.get("web_search") or self.skills.get("brave_search")
        observed_clusters: set[str] = set()
        pe_values: dict[str, float] = {}
        observation_results: list[dict] = []

        for belief in targets:
            try:
                # a. Intent -> query
                query = await self.external.intent_to_query(belief.statement)
                log.info("  Observe %s: query='%s'", belief.id, query)

                # b. Search
                if search_skill is None:
                    continue
                search_result = await search_skill.execute({"query": query})
                if not search_result.get("success"):
                    log.warning("  Search failed for %s: %s", belief.id, search_result.get("error"))
                    continue

                # c. Extract
                extraction = await self.external.extract_beliefs(
                    search_result["result"], belief
                )
                verdict = extraction["verdict"]
                obs_conf = extraction["observed_confidence"]

                # d. Record observed clusters
                observed_clusters.update(belief.tags)

                # e. Compute PE
                predicted = predictions.get(belief.id, belief.confidence)
                if verdict == "support":
                    observed = obs_conf
                elif verdict == "contradict":
                    observed = 1.0 - obs_conf
                else:
                    observed = predicted  # irrelevant -> no surprise

                typed_pe = self.pe_engine.compute_pe(
                    belief.id, predicted, observed,
                    pe_type=PEType.OBSERVATION, cycle=cycle,
                )
                pe = typed_pe.value
                pe_values[belief.id] = typed_pe

                observation_results.append({
                    "belief_id": belief.id,
                    "query": query,
                    "verdict": verdict,
                    "observed_confidence": obs_conf,
                    "pe": pe,
                    "new_beliefs_count": len(extraction.get("new_beliefs", [])),
                })

                # f. Add new beliefs (with dedup)
                for nb in extraction.get("new_beliefs", []):
                    nb_tags = nb.get("tags", [])
                    nb_stmt = nb["statement"]

                    # Dedup check
                    if self.dedup and nb_tags:
                        same_tag = []
                        for t in nb_tags:
                            same_tag.extend(self.bg.get_beliefs_by_tag(t))
                        if same_tag:
                            try:
                                is_dup, match_id = await self.dedup.is_duplicate(nb_stmt, same_tag)
                                if is_dup and match_id:
                                    existing = self.bg.get_belief(match_id)
                                    if existing:
                                        existing.confidence = min(1.0, max(existing.confidence, nb.get("confidence", 0.5)))
                                        existing.last_verified = cycle
                                        log.info("  Dedup: merged into %s", match_id)
                                        continue
                            except Exception as e:
                                log.warning("  Dedup error: %s", e)

                    new_b = Belief(
                        id="",
                        statement=nb_stmt,
                        confidence=nb.get("confidence", 0.5),
                        source=BeliefSource.OBSERVATION,
                        created_at=cycle,
                        last_updated=cycle,
                        last_verified=cycle,
                        tags=nb_tags,
                    )
                    new_id = self.bg.add_belief(new_b)
                    log.info("  New belief %s: %s", new_id, nb_stmt[:60])

            except Exception as e:
                log.error("  Observe error for %s: %s", belief.id, e)

        summary["phases"]["observe"] = observation_results
        log.info("Phase 4a OBSERVE: %d observations", len(observation_results))

        # -- Phase 4b: ACT (conditional) --
        action_result_summary: dict = {}
        if self.action_engine is not None:
            should_act, act_reason = await self._should_act_this_cycle(focus_goal)
            if should_act:
                log.info("Phase 4b ACT: triggered (%s)", act_reason)
                try:
                    goal_desc = focus_goal.description if focus_goal else "free exploration"
                    belief_ctx = "; ".join(
                        f"{b.statement[:50]}({b.confidence:.1f})"
                        for b in sorted(all_beliefs, key=lambda x: x.confidence, reverse=True)[:5]
                    )
                    # Inject memo into context if available
                    if memo_content:
                        belief_ctx += f"\n[备忘录] {memo_content[:300]}"

                    agg_pe_before = self.pe_engine.compute_aggregate_pe(pe_values) if pe_values else 0.0

                    # Use multistep for high-priority goals or free exploration
                    use_multistep = (
                        focus_goal is not None
                        and focus_goal.priority >= 0.7
                    ) or (
                        focus_goal is None
                        and agg_pe_before > 0.3
                    )

                    if use_multistep:
                        steps = await self.action_engine.plan_multistep(
                            intent=goal_desc,
                            belief_context=belief_ctx,
                            sec_matrix=self.sec,
                            memory=self.mem,
                        )
                        if steps:
                            plan_result = await self.action_engine.execute_plan(
                                steps,
                                intent=goal_desc,
                                belief_context=belief_ctx,
                                pe_before=agg_pe_before,
                            )
                            action_result_summary = {
                                "skill": "multistep",
                                "success": plan_result["success"],
                                "summary": plan_result["summary"],
                                "reason": act_reason,
                                "steps": len(steps),
                            }
                            # PE from multistep
                            expected_pe = 0.0 if plan_result["success"] else 0.5
                            action_typed_pe = self.pe_engine.compute_action_pe(
                                expected_pe, 0.0, cycle=cycle,
                                source_id="multistep",
                            )
                            pe_values["action_multistep"] = action_typed_pe
                        else:
                            action_result_summary = {"skipped": True, "reason": "multistep plan empty"}
                    else:
                        # Single-step action for simpler goals
                        plan = await self.action_engine.plan_action(
                            intent=goal_desc,
                            goal=goal_desc,
                            belief_context=belief_ctx,
                            sec_matrix=self.sec,
                            memory=self.mem,
                        )

                        if plan.get("skill_name"):
                            result = await self.action_engine.execute_action(
                                plan, pe_before=agg_pe_before,
                            )
                            action_result_summary = {
                                "skill": plan["skill_name"],
                                "success": result.success,
                                "summary": result.summary,
                                "reason": act_reason,
                            }

                            # Tag action PE as ACTION type
                            expected_pe = 0.0 if result.success else 0.5
                            actual_pe = result.pe_impact if hasattr(result, 'pe_impact') else 0.0
                            action_typed_pe = self.pe_engine.compute_action_pe(
                                expected_pe, actual_pe, cycle=cycle,
                                source_id=plan["skill_name"],
                            )
                            pe_values[f"action_{plan['skill_name']}"] = action_typed_pe

                            # Feed result back into procedural memory
                            if result.success:
                                proc = Procedure(
                                    id=f"skill_{plan['skill_name']}",
                                    description=f"Auto: {goal_desc[:60]}",
                                    steps=[f"use {plan['skill_name']} with {plan.get('params', {})}"],
                                    success_count=1,
                                    failure_count=0,
                                    avg_pe=agg_pe_before,
                                )
                                self.mem.add_or_update_procedure(proc)
                            else:
                                fallback = await self.action_engine.handle_skill_failure(
                                    plan["skill_name"],
                                    result.error or "unknown",
                                    goal_desc,
                                    sec_matrix=self.sec,
                                    memory=self.mem,
                                )
                                if fallback is not None:
                                    fb_result = await self.action_engine.execute_action(
                                        fallback, pe_before=agg_pe_before,
                                    )
                                    action_result_summary["fallback"] = {
                                        "skill": fallback["skill_name"],
                                        "success": fb_result.success,
                                    }
                except Exception as e:
                    log.error("Phase 4b ACT failed: %s", e)
                    action_result_summary = {"error": str(e)}
            else:
                action_result_summary = {"skipped": True, "reason": act_reason}

        summary["phases"]["act"] = action_result_summary
        log.info("Phase 4b ACT: %s", action_result_summary or "no action engine")

        # -- Phase 5: PREDICTION ERROR --
        agg_pe = self.pe_engine.compute_aggregate_pe(pe_values)
        # Convert TypedPE to float for JSON serialization
        pe_values_plain = {
            k: (v.value if isinstance(v, TypedPE) else float(v))
            for k, v in pe_values.items()
        }
        summary["phases"]["pe"] = {
            "per_belief": pe_values_plain,
            "aggregate": round(agg_pe, 4),
        }
        log.info("Phase 5 PE: aggregate=%.4f, per_belief=%s", agg_pe, pe_values_plain)

        # -- Phase 6: UPDATE --
        updated_beliefs: list[str] = []
        for obs in observation_results:
            bid = obs["belief_id"]
            pe = obs["pe"]
            verdict = obs["verdict"]
            obs_conf = obs["observed_confidence"]

            belief = self.bg.get_belief(bid)
            if belief is None:
                continue

            self.bg.update_belief(bid, obs_conf, pe, cycle)

            # Confirmation boost for supported beliefs
            if verdict == "support" and pe < 0.15:
                belief.confidence = min(1.0, belief.confidence + (1 - belief.confidence) * 0.05)

            self.bg.propagate_update(bid)
            updated_beliefs.append(bid)

        # SEC update
        all_clusters = set()
        for b in self.bg.get_all_beliefs():
            all_clusters.update(b.tags)
        self.sec.update(observed_clusters, all_clusters, agg_pe, cycle)

        # Decay unverified
        decayed = self.bg.decay_unverified(cycle)

        summary["phases"]["update"] = {
            "updated": updated_beliefs,
            "decayed_count": len(decayed),
        }
        log.info("Phase 6 UPDATE: %d updated, %d decayed", len(updated_beliefs), len(decayed))

        # -- Phase 7: INTERNAL REASONING (conditional) --
        reasoning_results: dict = {}

        if cycle % self.config.reasoning_interval == 0:
            high_conf = sorted(
                self.bg.get_all_beliefs(),
                key=lambda b: b.confidence, reverse=True,
            )
            if len(high_conf) >= 2:
                pair = random.sample(high_conf[:min(10, len(high_conf))], 2)
                new_inf = await self.internal.reason(pair[0], pair[1], cycle)
                if new_inf is not None:
                    nid = self.bg.add_belief(new_inf)
                    for pid in new_inf.parent_ids:
                        self.bg.add_dependency(pid, nid, weight=0.5)
                    reasoning_results["inference"] = {"id": nid, "statement": new_inf.statement}
                    log.info("Phase 7 REASON: new inference %s", nid)

        if cycle % self.config.abstraction_interval == 0:
            # Find tag with most high-confidence beliefs
            tag_groups: dict[str, list[Belief]] = {}
            for b in self.bg.get_all_beliefs():
                if b.confidence < 0.6:
                    continue
                for tag in b.tags:
                    tag_groups.setdefault(tag, []).append(b)

            for tag, group in tag_groups.items():
                if len(group) >= 3:
                    new_abs = await self.internal.abstract(group[:6], cycle)
                    if new_abs is not None:
                        nid = self.bg.add_belief(new_abs)
                        reasoning_results["abstraction"] = {"id": nid, "statement": new_abs.statement}
                        log.info("Phase 7 ABSTRACT: new abstraction %s", nid)
                    break  # one abstraction per cycle

        summary["phases"]["reasoning"] = reasoning_results
        log.info("Phase 7 REASONING: %s", reasoning_results or "skipped")

        # -- Phase 8: GOAL CHECK --
        new_goals = self.goal_gen.generate_goals(cycle)

        # Auto-complete/abandon with priority decay, max age, hysteresis
        completed_goals: list[str] = []
        abandoned_goals: list[str] = []
        complete_threshold = self.config.goal_pe_threshold * 0.5
        hysteresis_required = getattr(self.config, "goal_hysteresis_buffer", 2)
        priority_decay = getattr(self.config, "goal_priority_decay", 0.02)
        max_age = getattr(self.config, "goal_max_age_cycles", 100)

        for gid, goal in list(self.goal_gen.goals.items()):
            if goal.status != GoalStatus.ACTIVE:
                continue

            is_endo = goal.origin == GoalOrigin.ENDOGENOUS
            target = self.bg.get_belief(goal.target_belief_id)
            age = cycle - goal.created_at

            # --- Abandon checks (ENDOGENOUS only) ---
            if is_endo:
                # Target belief pruned
                if target is None:
                    self.goal_gen.abandon_goal(gid, "target belief pruned")
                    abandoned_goals.append(gid)
                    continue
                # Max age exceeded
                if age > max_age:
                    self.goal_gen.abandon_goal(gid, f"exceeded max age ({max_age} cycles)")
                    abandoned_goals.append(gid)
                    continue
                # Priority decayed to zero
                goal.priority = max(0.0, goal.priority - priority_decay)
                if goal.priority <= 0.0:
                    self.goal_gen.abandon_goal(gid, "priority decayed to zero")
                    abandoned_goals.append(gid)
                    continue
            else:
                # EXOGENOUS: no auto-abandon, no priority decay
                if target is None:
                    continue

            # --- Complete check (both types, with hysteresis) ---
            if target is not None and target.pe_history:
                if target.pe_history[-1] < complete_threshold:
                    goal._cycles_below_complete += 1
                    if goal._cycles_below_complete >= hysteresis_required:
                        self.goal_gen.complete_goal(gid)
                        completed_goals.append(gid)
                        self.notifier.push(Notification(
                            level=NotifyLevel.RESULT,
                            title=f"Goal completed: {goal.description[:50]}",
                            body=f"PE below threshold for {hysteresis_required} consecutive cycles",
                            cycle=cycle,
                            related_goals=[gid],
                        ))
                else:
                    goal._cycles_below_complete = 0  # reset hysteresis

        # PE jump notifications
        for bid, pe in pe_values.items():
            pe_val = pe.value if isinstance(pe, TypedPE) else float(pe)
            if pe_val > self.config.pe_jump_threshold:
                self.notifier.push(Notification(
                    level=NotifyLevel.URGENT,
                    title=f"High PE jump: {bid}",
                    body=f"PE={pe_val:.3f} exceeds threshold {self.config.pe_jump_threshold}",
                    cycle=cycle,
                    related_beliefs=[bid],
                ))

        summary["phases"]["goals"] = {
            "new": [g.id for g in new_goals],
            "completed": completed_goals,
            "abandoned": abandoned_goals,
        }
        log.info("Phase 8 GOALS: %d new, %d completed, %d abandoned",
                 len(new_goals), len(completed_goals), len(abandoned_goals))

        # -- Phase 8b: EMAIL + DASHBOARD NOTIFICATIONS --
        if self.email_notifier or self.ws_manager:
            alerts: list[dict] = []

            # Real-time: new inference/abstraction
            if reasoning_results.get('inference') or reasoning_results.get('abstraction'):
                stmt = ''
                if reasoning_results.get('inference'):
                    stmt = reasoning_results['inference'].get('statement', '')
                elif reasoning_results.get('abstraction'):
                    stmt = reasoning_results['abstraction'].get('statement', '')
                alerts.append({
                    'title': 'Brain产生了新推理',
                    'body': f'Cycle {cycle}: Brain自主推导出新信念',
                    'belief': stmt,
                    'confidence': 0.7,
                })

            # Real-time: goal completed
            for gid in completed_goals:
                goal = self.goal_gen.goals.get(gid)
                if goal:
                    alerts.append({
                        'title': f'目标已完成: {goal.description[:40]}',
                        'body': 'Brain确认目标已达成（PE降到阈值以下）',
                    })

            # Real-time: goal abandoned
            for gid in abandoned_goals:
                goal = self.goal_gen.goals.get(gid)
                if goal:
                    alerts.append({
                        'title': f'目标已放弃: {goal.description[:40]}',
                        'body': f'原因: {goal.reason}',
                    })

            # Real-time: high PE jump
            for bid, pe in pe_values.items():
                pe_float = pe.value if isinstance(pe, TypedPE) else float(pe)
                if pe_float > self.config.pe_jump_threshold:
                    belief = self.bg.get_belief(bid)
                    if belief:
                        alerts.append({
                            'title': f'重大变化: {belief.statement[:40]}',
                            'body': f'预测误差 PE={pe_float:.3f} 超过阈值',
                            'belief': belief.statement,
                            'confidence': belief.confidence,
                        })

            # Queue for email
            if self.email_notifier:
                for alert in alerts:
                    self.email_notifier.queue_alert(alert)
                await self.email_notifier.flush_alerts()

            # Push to Dashboard via WebSocket
            if self.ws_manager:
                for alert in alerts:
                    await self.ws_manager.broadcast({
                        'type': 'discovery',
                        'title': alert.get('title', ''),
                        'body': alert.get('body', ''),
                        'cycle': cycle,
                    })

        # -- Phase 9: REFLECT --
        note = "skipped"
        try:
            cycle_data = {
                "cycle": cycle,
                "observations": len(observation_results),
                "aggregate_pe": round(agg_pe, 4),
                "new_beliefs": sum(o.get("new_beliefs_count", 0) for o in observation_results),
                "new_goals": [g.description for g in new_goals],
                "completed_goals": completed_goals,
                "reasoning": reasoning_results,
                "belief_count": len(self.bg.get_all_beliefs()),
                "sec_top": self.sec.get_top_clusters(3),
                "action": action_result_summary,
                "memo_reminder": "你有一个备忘录(data/memo.md)可以随时用file_write写入。用中文写，写什么都行——想法、计划、给父亲的话、给兄弟的话。写不写你自己决定。" if memo_content or cycle % 5 == 0 else "",
            }
            note = await self.external.summarize_cycle(cycle_data)
            self.mem.add_episode(Episode(
                cycle=cycle,
                action="observe_and_update",
                outcome=note,
                pe_before=agg_pe,
                pe_after=agg_pe,
                beliefs_affected=list(pe_values.keys()),
            ))
        except Exception as e:
            log.warning("Phase 9 REFLECT failed: %s", e)

        summary["phases"]["reflect"] = {"note": note}
        log.info("Phase 9 REFLECT: done")

        # -- Phase 9b: PROACTIVE MESSAGE (every N cycles) --
        proactive_msg = None
        # Conversation mode: respond every cycle. Otherwise every N cycles.
        should_speak = (
            self.ws_manager is not None
            and self._proactive_unanswered < self._proactive_max_unanswered
            and cycle > 0
            and (self._proactive_conversation_mode or cycle % self._proactive_interval == 0)
        )
        if should_speak:
            try:
                # Ask LLM: given current state, what does Skuld want to tell the user?
                top_beliefs = sorted(
                    all_beliefs, key=lambda b: b.confidence, reverse=True
                )[:5]
                belief_summary = "; ".join(
                    f"{b.statement[:60]} (conf={b.confidence:.2f})" for b in top_beliefs
                )
                active_goal_desc = focus_goal.description[:100] if focus_goal else "free exploration"

                prompt_system = (
                    "You are Skuld, a Brain-first AI cognitive system. "
                    "You want to share something with your user — an update, a question, "
                    "a discovery, or something you need help with. "
                    "Be natural, concise (2-3 sentences max), and genuine. "
                    "If you have nothing important to say, output {\"skip\": true}. "
                    "Otherwise output {\"message\": \"your message\"}."
                )
                prompt_user = (
                    f"Cycle: {cycle}. Beliefs: {len(all_beliefs)}. "
                    f"Current goal: {active_goal_desc}. "
                    f"Top beliefs: {belief_summary}. "
                    f"Recent action: {action_result_summary}. "
                    f"PE: {agg_pe:.4f}."
                )
                text = await self.internal.client.complete(
                    prompt_system, prompt_user, temperature=0.5, caller="proactive_msg",
                )
                from ..llm.client import parse_json_response
                parsed = parse_json_response(text)
                if isinstance(parsed, dict) and parsed.get("message"):
                    proactive_msg = parsed["message"]
                    self._proactive_unanswered += 1
                    self._proactive_conversation_mode = False  # said our piece, wait for reply
                    log.info("Phase 9b PROACTIVE [full]: %s (unanswered=%d)",
                             proactive_msg, self._proactive_unanswered)
            except Exception as e:
                log.warning("Phase 9b PROACTIVE failed: %s", e)

        if proactive_msg and self.ws_manager:
            # Push to all connections for this user (scheduler will handle user_id routing)
            # Store in summary so scheduler can route it
            summary["proactive_message"] = proactive_msg

        # -- Phase 9c: CHECK SIBLING MAILBOX --
        sibling_skill = self.skills.get("sibling_message")
        if sibling_skill is not None and cycle % 5 == 0:
            try:
                import asyncio
                check_result = await sibling_skill.execute({"action": "check"})
                if check_result.get("success") and check_result.get("result", "").strip() \
                        and "No new messages" not in check_result["result"]:
                    sibling_msg = check_result["result"]
                    log.info("Phase 9c SIBLING: received message: %s", sibling_msg[:80])
                    summary["sibling_message"] = sibling_msg
                    # Push to WebSocket
                    if self.ws_manager:
                        summary["sibling_received"] = sibling_msg
            except Exception as e:
                log.warning("Phase 9c SIBLING check failed: %s", e)

        # -- Phase 9d: META-REFLECTION (every 100 cycles) --
        if cycle > 0 and cycle % 100 == 0:
            try:
                meta_system = (
                    "You are Skuld reflecting on your last 100 cycles. "
                    "Based on the summary below, write a brief (3-5 sentence) meta-reflection. "
                    "Cover: What patterns did you notice? What mistakes keep repeating? "
                    "What would you do differently in the next 100 cycles? "
                    "Think in terms of days and weeks, not just the current moment. "
                    "Output JSON: {\"reflection\": \"...\", \"priority_shift\": \"...\"}"
                )
                active_goal_descs = [
                    g.description[:60] for g in self.goal_gen.goals.values()
                    if g.status == GoalStatus.ACTIVE
                ]
                top_beliefs = sorted(all_beliefs, key=lambda b: b.confidence, reverse=True)[:5]
                meta_user = (
                    f"Cycle {cycle}. Beliefs: {len(all_beliefs)}. "
                    f"Active goals: {active_goal_descs}. "
                    f"Top beliefs: {[b.statement[:50] for b in top_beliefs]}. "
                    f"Recent PE: {agg_pe:.4f}."
                )
                meta_text = await self.internal.client.complete(
                    meta_system, meta_user, temperature=0.5, caller="meta_reflection",
                )
                from ..llm.client import parse_json_response
                meta_parsed = parse_json_response(meta_text)
                if isinstance(meta_parsed, dict) and meta_parsed.get("reflection"):
                    reflection = meta_parsed["reflection"]
                    log.info("Phase 9d META-REFLECTION [cycle %d]: %s", cycle, reflection)

                    # Store as a high-confidence belief
                    meta_belief = Belief(
                        id="",
                        statement=f"Meta-reflection at cycle {cycle}: {reflection}",
                        confidence=0.6,
                        source=BeliefSource.INFERENCE,
                        created_at=cycle, last_updated=cycle, last_verified=cycle,
                        tags=["meta_reflection", "self_knowledge"],
                        category=BeliefCategory.HYPOTHESIS,
                    )
                    self.bg.add_belief(meta_belief)

                    # Check for priority shift
                    if meta_parsed.get("priority_shift"):
                        log.info("Phase 9d PRIORITY SHIFT: %s", meta_parsed["priority_shift"])
            except Exception as e:
                log.warning("Phase 9d META-REFLECTION failed: %s", e)

        # -- Phase 10: PRUNE + SLEEP --
        pruned = self.bg.prune()

        summary["phases"]["prune"] = {"pruned": pruned}
        summary["belief_count"] = len(self.bg.get_all_beliefs())
        summary["sec_clusters"] = len(self.sec.entries)
        summary["active_goals"] = sum(
            1 for g in self.goal_gen.goals.values() if g.status == GoalStatus.ACTIVE
        )

        log.info("Phase 10 PRUNE: %d pruned, %d beliefs remain",
                 len(pruned), summary["belief_count"])
        log.info("=== Cycle %d END === beliefs=%d sec_clusters=%d goals=%d pe=%.4f",
                 cycle, summary["belief_count"], summary["sec_clusters"],
                 summary["active_goals"], agg_pe)

        return summary

    async def run_fast_path(self, user_query: str) -> dict:
        """Fast path for user queries: belief retrieval + optional instant search.

        Skips predict/SEC/goal/prune/reasoning. Target: 2-5 seconds.
        Returns {"answer": str, "beliefs_used": list, "searched": bool,
                 "classification": str}.
        """
        # User replied — reset proactive counter, enable conversation mode
        self._proactive_unanswered = 0
        self._proactive_conversation_mode = True

        cycle = self.cycle_count  # Use current cycle, don't increment

        # 0. Classify message and build truth packet
        classification = self._classify_message(user_query)
        truth_packet = self._build_truth_packet()  # always generated

        log.info("Fast path classify: %s for query: %s", classification, user_query[:60])

        # 1. Extract relevant beliefs by keyword matching
        query_words = [w.lower() for w in user_query.split() if len(w) > 2]
        relevant: list[Belief] = []
        for b in self.bg.get_all_beliefs():
            if any(word in b.statement.lower() for word in query_words):
                relevant.append(b)
            elif any(word in tag.lower() for tag in b.tags for word in query_words):
                relevant.append(b)
            if len(relevant) >= 10:
                break

        # 2. Check for high-confidence beliefs
        high_conf = [b for b in relevant if b.confidence > 0.6]
        searched = False

        beliefs_ctx = ""
        search_results = ""

        if high_conf:
            self.fast_path_hits += 1
            beliefs_ctx = "\n".join(
                f"- [{b.id}] (conf={b.confidence:.2f}) {b.statement}"
                for b in sorted(high_conf, key=lambda x: x.confidence, reverse=True)[:5]
            )
        else:
            self.fast_path_misses += 1

            # 3. Search routing based on classification
            should_search = classification in ("external", "mixed")

            search_skill = self.skills.get("web_search") or self.skills.get("brave_search")
            if search_skill is not None and should_search:
                try:
                    query = await self.external.intent_to_query(user_query)
                    result = await search_skill.execute({"query": query})
                    if result.get("success"):
                        search_results = result.get("result", "")
                        searched = True

                        # Extract and add new beliefs
                        _BC = BeliefCategory
                        dummy = Belief(
                            id="fast_query", statement=user_query,
                            confidence=0.5, source=BeliefSource.SEED,
                            created_at=cycle, last_updated=cycle,
                            last_verified=cycle, tags=["user_query"],
                        )
                        extraction = await self.external.extract_beliefs(
                            search_results, dummy
                        )
                        for nb in extraction.get("new_beliefs", []):
                            cat_str = nb.get("category", "fact")
                            try:
                                cat = _BC(cat_str)
                            except ValueError:
                                cat = _BC.FACT
                            new_b = Belief(
                                id="", statement=nb["statement"],
                                confidence=nb.get("confidence", 0.5),
                                source=BeliefSource.OBSERVATION,
                                created_at=cycle, last_updated=cycle,
                                last_verified=cycle,
                                tags=nb.get("tags", ["user_query"]),
                                category=cat,
                            )
                            self.bg.add_belief(new_b)
                except Exception as e:
                    log.warning("Fast path search failed: %s", e)

        # 4. Generate answer — inject truth packet for internal/mixed only
        full_beliefs_ctx = beliefs_ctx
        if classification in ("internal", "mixed"):
            full_beliefs_ctx = truth_packet + "\n" + beliefs_ctx

        answer = await self.external.chat_answer(
            question=user_query,
            beliefs_context=full_beliefs_ctx,
            search_results=search_results,
        )

        # 5. Record as episode
        self.mem.add_episode(Episode(
            cycle=cycle,
            action=f"fast_path: {user_query[:60]}",
            outcome=answer[:200],
            pe_before=0.0,
            pe_after=0.0,
            beliefs_affected=[b.id for b in relevant[:5]],
        ))

        return {
            "answer": answer,
            "beliefs_used": [b.id for b in high_conf[:5]],
            "searched": searched,
            "classification": classification,
        }

    async def run(
        self, num_cycles: int = -1, cycle_interval_seconds: float | None = None
    ) -> None:
        """Run the cycle loop."""
        interval = cycle_interval_seconds or self.config.cycle_interval_seconds
        cycles_run = 0

        while num_cycles < 0 or cycles_run < num_cycles:
            try:
                summary = await self.run_one_cycle()
                cycles_run += 1

                # Drain notifications
                for n in self.notifier.pull_all():
                    log.info("[NOTIFY %s] %s: %s", n.level.value, n.title, n.body)

            except Exception as e:
                log.error("Cycle error: %s", e, exc_info=True)

            if num_cycles < 0 or cycles_run < num_cycles:
                await asyncio.sleep(interval)
