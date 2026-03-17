import asyncio
import logging
import random

from ..brain.belief_graph import BeliefGraph
from ..brain.sec_matrix import SECMatrix
from ..brain.prediction import PredictionEngine
from ..brain.goal_generator import GoalGenerator
from ..brain.memory import Memory
from ..llm.internal import InternalLLM
from ..llm.external import ExternalLLM
from ..skills.base import SkillRegistry
from ..skills.registry import SmartSkillRegistry
from ..types import Belief, BeliefCategory, BeliefSource, Episode, GoalOrigin, GoalStatus, PEType, Procedure, TypedPE
from ..config import MimirConfig
from .notifier import Notifier, Notification, NotifyLevel

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
        self.cycle_count = 0

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
        focus_goal = max(active_goals, key=lambda g: g.priority) if active_goals else None
        summary["phases"]["wake"] = {
            "active_goals": len(active_goals),
            "focus": focus_goal.description if focus_goal else "free exploration",
        }
        log.info("Phase 1 WAKE: %d active goals, focus=%s",
                 len(active_goals), summary["phases"]["wake"]["focus"])

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
        search_skill = self.skills.get("brave_search")
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
                    goal_desc = focus_goal.description if focus_goal else "reduce prediction error"
                    belief_ctx = "; ".join(
                        f"{b.statement[:50]}({b.confidence:.1f})"
                        for b in sorted(all_beliefs, key=lambda x: x.confidence, reverse=True)[:5]
                    )

                    plan = await self.action_engine.plan_action(
                        intent=goal_desc,
                        goal=goal_desc,
                        belief_context=belief_ctx,
                        sec_matrix=self.sec,
                        memory=self.mem,
                    )

                    if plan.get("skill_name"):
                        agg_pe_before = self.pe_engine.compute_aggregate_pe(pe_values) if pe_values else 0.0
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

                        # Feed result back into belief graph and procedural memory
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
                            # Try fallback
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
        Returns {"answer": str, "beliefs_used": list, "searched": bool}.
        """
        cycle = self.cycle_count  # Use current cycle, don't increment

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
            beliefs_ctx = "\n".join(
                f"- [{b.id}] (conf={b.confidence:.2f}) {b.statement}"
                for b in sorted(high_conf, key=lambda x: x.confidence, reverse=True)[:5]
            )
        else:
            # 3. No high-confidence match: instant search
            search_skill = self.skills.get("brave_search")
            if search_skill is not None:
                try:
                    query = await self.external.intent_to_query(user_query)
                    result = await search_skill.execute({"query": query})
                    if result.get("success"):
                        search_results = result.get("result", "")
                        searched = True

                        # Extract and add new beliefs
                        from ..types import BeliefCategory as _BC
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

        # 4. Generate answer
        answer = await self.external.chat_answer(
            question=user_query,
            beliefs_context=beliefs_ctx,
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
