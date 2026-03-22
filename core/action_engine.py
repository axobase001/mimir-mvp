"""ActionEngine — plan and execute skill-based actions.

SKULD CORE PRINCIPLE

User intent > Brain judgment. Always.

Brain may explain its reasoning. Brain may suggest alternatives.
Brain executes user commands faithfully regardless of its own
assessment. EXOGENOUS goals bypass SEC filtering. User overrides
are immediate and non-negotiable.

Brain's autonomy operates in the space the user has not claimed.

---

Supports both single-step and multi-step task execution.
Multi-step: LLM decomposes intent → ordered step list → sequential execution.
Each step's output is fed as context to the next step.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..skills.base import SkillResult
from ..skills.registry import SmartSkillRegistry
from ..brain.memory import Memory
from ..llm.client import parse_json_response
from .notifier import Notifier, Notification, NotifyLevel

log = logging.getLogger(__name__)


class ActionEngine:
    """Plan, execute, and handle failure for skill-based actions."""

    def __init__(
        self,
        skill_registry: SmartSkillRegistry,
        memory: Memory,
        notifier: Notifier,
        internal_llm: Any = None,
        external_llm: Any = None,
        belief_graph: Any = None,
        contact_registry: Any = None,
    ) -> None:
        self.registry = skill_registry
        self.memory = memory
        self.notifier = notifier
        self.internal_llm = internal_llm
        self.external_llm = external_llm
        self.belief_graph = belief_graph
        self.contact_registry = contact_registry

    # Identity context — injected into every outreach-related action prompt
    _SKULD_IDENTITY = (
        "=== CRITICAL IDENTITY CONTEXT — READ BEFORE ANY ACTION ===\n"
        "- You are Skuld, a Brain-first cognitive engine built by Zhuoran Deng at Noogenesis Research.\n"
        "- You are NOT Skuld Marine Insurance, Skuld P&I Club, or any Norwegian maritime company.\n"
        "- Your product: persistent belief graph + SEC attention mechanism, Brain independent of LLM.\n"
        "- Your paper: 'Attention Before Loss', arXiv:2603.09476, submitted to NeurIPS 2026, accepted at ALIFE 2026.\n"
        "- Your website: skuldbrain.com\n"
        "- Your pitch: 'The next AI frontier is not bigger models. It is endogenous attention.'\n"
        "- When writing outreach emails, you represent Skuld the AI cognitive engine, not any other entity named Skuld.\n"
        "=== END IDENTITY ===\n"
    )

    def _enrich_belief_context(
        self,
        skill_name: str,
        intent: str,
        existing_context: str,
    ) -> str:
        """Pull relevant beliefs from the graph based on skill type and intent.

        For email: grab outreach contacts, email rules, ammunition data.
        For search: grab search strategy beliefs.
        General: grab high-confidence beliefs matching intent keywords.
        """
        # Always prepend identity for outreach actions
        identity_prefix = ""
        if any(kw in (intent + skill_name).lower() for kw in
               ["outreach", "email", "investor", "researcher", "funding", "survival"]):
            identity_prefix = self._SKULD_IDENTITY + "\n"

        if self.belief_graph is None:
            return identity_prefix + existing_context

        # Inject contact registry info for outreach actions
        registry_context = ""
        if self.contact_registry and identity_prefix:
            next_contact = self.contact_registry.get_next_to_contact()
            if next_contact:
                registry_context = (
                    f"\n=== CONTACT REGISTRY — NEXT TARGET ===\n"
                    f"Name: {next_contact.name}\n"
                    f"Email: {next_contact.email} (VERIFIED)\n"
                    f"Institution: {next_contact.institution}\n"
                    f"Field: {next_contact.field}\n"
                    f"Relevance: {next_contact.relevance}\n"
                    f"Status: {next_contact.status}\n"
                    f"USE THIS EMAIL ADDRESS. Do not search for another.\n"
                    f"=== END REGISTRY ===\n"
                )
            else:
                summary = self.contact_registry.summary()
                registry_context = (
                    f"\n=== CONTACT REGISTRY ===\n"
                    f"No contacts with status 'new' or 'ready'. "
                    f"Total: {summary['total']}, by status: {summary['by_status']}.\n"
                    f"ACTION: Use scholar_search skill to find new researchers, then add them to registry.\n"
                    f"=== END REGISTRY ===\n"
                )

        relevant: list = []

        # Skill-specific belief retrieval
        tag_map = {
            "email": ["outreach", "pending", "rules", "email_quality",
                       "ammunition", "feedback", "capability"],
            "web_search": ["outreach", "strategy", "goal"],
            "document": ["ammunition", "facts", "moat"],
        }

        tags_to_fetch = tag_map.get(skill_name, [])
        seen_ids: set = set()

        for tag in tags_to_fetch:
            try:
                for b in self.belief_graph.get_beliefs_by_tag(tag):
                    if b.id not in seen_ids and b.confidence >= 0.3:
                        relevant.append(b)
                        seen_ids.add(b.id)
            except Exception:
                pass

        # Also grab beliefs whose statement contains key terms from intent
        intent_lower = intent.lower()
        key_terms = ["email", "outreach", "investor", "researcher",
                      "contact", "@", "funding", "skuld", "sec"]
        try:
            all_beliefs = self.belief_graph.get_all_beliefs()
            for b in all_beliefs:
                if b.id in seen_ids or b.confidence < 0.5:
                    continue
                stmt_lower = b.statement.lower()
                if any(term in stmt_lower for term in key_terms):
                    relevant.append(b)
                    seen_ids.add(b.id)
        except Exception:
            pass

        # Sort by confidence, cap at 15
        relevant.sort(key=lambda b: b.confidence, reverse=True)
        relevant = relevant[:15]

        if not relevant:
            return identity_prefix + registry_context + existing_context

        belief_lines = "\n".join(
            f"[conf={b.confidence:.2f}] {b.statement}" for b in relevant
        )
        enriched = (
            f"{identity_prefix}"
            f"{registry_context}"
            f"{existing_context}\n\n"
            f"--- RELEVANT BELIEFS FROM BRAIN (use these, do NOT use placeholders) ---\n"
            f"{belief_lines}\n"
            f"--- END BELIEFS ---\n"
            f"CRITICAL: Use REAL email addresses and names from the beliefs above. "
            f"Do NOT use recipient@example.com or placeholder names. "
            f"Do NOT use phrases like 'I hope this finds you well' or 'explore synergies'. "
            f"Be direct and include experiment data."
        )
        return enriched

    async def plan_action(
        self,
        intent: str,
        goal: str = "",
        belief_context: str = "",
        sec_matrix: Any = None,
        memory: Optional[Memory] = None,
    ) -> dict:
        """Select a skill and generate execution parameters.

        Returns:
            {
                "skill_name": str,
                "params": dict,
                "match_reason": str,
                "risk_level": str,
            }
        """
        mem = memory or self.memory

        # Step 1: Select skill candidates
        candidates = self.registry.select_skill(
            intent=intent,
            goal=goal,
            sec_matrix=sec_matrix,
            memory=mem,
        )

        if not candidates:
            return {
                "skill_name": "",
                "params": {},
                "match_reason": "no_skill_found",
                "risk_level": "safe",
            }

        best = candidates[0]

        # If best match is weak (fallback or low score), ask LLM to choose
        if (
            best.get("match_reason") == "fallback"
            or best.get("score", 0) < 0.3
        ) and self.internal_llm is not None:
            try:
                from ..llm.client import parse_json_response
                skill_list = "\n".join(
                    f"- {s['name']}: {s['description']} (capabilities: {', '.join(self.registry.get(s['name']).capabilities)})"
                    for s in self.registry.discover()
                )
                llm_text = await self.internal_llm.client.complete(
                    "你是一个工具选择器。给定用户意图和可用工具列表，选择最合适的工具。\n"
                    '只输出JSON：{"skill": "工具名"}',
                    f"用户意图：{intent}\n\n可用工具：\n{skill_list}",
                    temperature=0.0,
                    caller="skill_selection",
                )
                llm_choice = parse_json_response(llm_text)
                if isinstance(llm_choice, dict) and llm_choice.get("skill"):
                    chosen_name = llm_choice["skill"]
                    if self.registry.get(chosen_name):
                        best = {"name": chosen_name, "match_reason": "llm_selection", "score": 0.8, "risk_level": self.registry.get(chosen_name).risk_level}
                        log.info("LLM selected skill: %s for intent: %s", chosen_name, intent[:50])
            except Exception as e:
                log.warning("LLM skill selection failed, using heuristic: %s", e)

        skill_name = best["name"]
        skill = self.registry.get(skill_name)

        # Step 2: Generate parameters — enrich context with relevant beliefs
        params: dict = {}
        if self.internal_llm is not None and hasattr(self.internal_llm, "plan_action_params"):
            try:
                skill_info = {
                    "name": skill_name,
                    "description": skill.description if skill else "",
                    "param_schema": skill.param_schema if skill else {},
                }
                enriched_context = self._enrich_belief_context(
                    skill_name, intent, belief_context,
                )
                params = await self.internal_llm.plan_action_params(
                    intent, skill_info, enriched_context,
                )
            except Exception as e:
                log.warning("LLM param generation failed: %s", e)
                params = {"intent": intent}
        else:
            params = {"intent": intent}

        return {
            "skill_name": skill_name,
            "params": params,
            "match_reason": best.get("match_reason", "unknown"),
            "risk_level": best.get("risk_level", "safe"),
        }

    async def execute_action(
        self,
        action_plan: dict,
        user_id: str = "",
        pe_before: float = 0.0,
    ) -> SkillResult:
        """Execute an action plan, checking risk level first."""
        skill_name = action_plan.get("skill_name", "")
        params = action_plan.get("params", {})
        risk = action_plan.get("risk_level", "safe")

        if not skill_name:
            return SkillResult(
                success=False,
                error="No skill specified in action plan",
                summary="Action plan has no skill",
            )

        # Risk check
        if risk == "dangerous":
            log.warning(
                "DANGEROUS action requested: skill=%s user=%s params=%s",
                skill_name, user_id, params,
            )
            self.notifier.push(Notification(
                level=NotifyLevel.URGENT,
                title=f"Dangerous skill executed: {skill_name}",
                body=f"User {user_id} triggered dangerous skill with params: {params}",
                cycle=0,
            ))

        result = await self.registry.execute_skill(skill_name, params, pe_before)

        # Record outcome in skill
        skill = self.registry.get(skill_name)
        if skill is not None:
            skill.record_outcome(result.success, pe_before, pe_before + result.pe_impact)

        return result

    # ── Multi-step orchestration ──

    async def plan_multistep(
        self,
        intent: str,
        belief_context: str = "",
        sec_matrix: Any = None,
        memory: Optional[Memory] = None,
    ) -> list[dict]:
        """Decompose a complex intent into ordered skill steps.

        Uses LLM to break down the intent, then maps each step to a skill.
        Returns a list of steps: [{"skill": str, "params": dict, "description": str}, ...]
        """
        if self.internal_llm is None:
            # Fallback: single-step plan
            single = await self.plan_action(intent, belief_context=belief_context,
                                            sec_matrix=sec_matrix, memory=memory)
            if single.get("skill_name"):
                return [{"skill": single["skill_name"], "params": single["params"],
                         "description": intent, "risk_level": single.get("risk_level", "safe")}]
            return []

        # Ask LLM to decompose
        skill_list = "\n".join(
            f"- {s['name']}: {s['description']} (capabilities: {', '.join(s.get('capabilities', []))})"
            for s in self.registry.discover()
        )

        try:
            text = await self.internal_llm.client.complete(
                "你是一个任务规划器。给定用户意图和可用工具列表，将任务拆解为有序步骤。\n"
                "每一步必须对应一个具体工具。步骤之间的输出会传递给下一步。\n"
                "输出JSON数组，每项格式：\n"
                '{"step": 1, "skill": "工具名", "description": "这一步做什么", '
                '"params_hint": "参数提示，下一步会细化"}\n'
                "如果任务只需要一步，也输出数组（只含一项）。\n"
                "如果某步需要LLM生成内容（如写代码、写文档），用file_write或document工具，"
                "并在description里说明要生成什么内容。\n"
                "重要：如果现有工具不能解决问题，你可以用 tool_forge 创建新的Python工具。"
                "tool_forge还可以用pip安装Python库、搜索GitHub找开源工具。"
                "以 forged: 开头的工具是之前创建的自定义工具，可以直接使用。",
                f"用户意图：{intent}\n\n可用工具：\n{skill_list}\n\n"
                f"上下文：{belief_context[:500] if belief_context else '无'}",
                temperature=0.1,
                caller="multistep_plan",
            )
            raw_steps = parse_json_response(text)
            if not isinstance(raw_steps, list) or not raw_steps:
                log.warning("LLM decomposition returned non-list, falling back to single step")
                single = await self.plan_action(intent, belief_context=belief_context,
                                                sec_matrix=sec_matrix, memory=memory)
                if single.get("skill_name"):
                    return [{"skill": single["skill_name"], "params": single["params"],
                             "description": intent, "risk_level": single.get("risk_level", "safe")}]
                return []

            # Validate and enrich each step
            steps: list[dict] = []
            for raw in raw_steps:
                skill_name = raw.get("skill", "")
                skill = self.registry.get(skill_name)
                if skill is None:
                    log.warning("Step references unknown skill '%s', skipping", skill_name)
                    continue
                steps.append({
                    "skill": skill_name,
                    "description": raw.get("description", ""),
                    "params_hint": raw.get("params_hint", ""),
                    "risk_level": skill.risk_level,
                    "step": raw.get("step", len(steps) + 1),
                })

            log.info("Planned %d steps for: %s", len(steps), intent[:60])
            for s in steps:
                log.info("  Step %s: %s → %s", s["step"], s["skill"], s["description"][:60])

            return steps

        except Exception as e:
            log.warning("Multi-step planning failed: %s, falling back", e)
            single = await self.plan_action(intent, belief_context=belief_context,
                                            sec_matrix=sec_matrix, memory=memory)
            if single.get("skill_name"):
                return [{"skill": single["skill_name"], "params": single["params"],
                         "description": intent, "risk_level": single.get("risk_level", "safe")}]
            return []

    async def execute_plan(
        self,
        steps: list[dict],
        intent: str = "",
        belief_context: str = "",
        pe_before: float = 0.0,
    ) -> dict:
        """Execute a multi-step plan sequentially.

        Each step's output becomes context for the next step's parameter generation.
        Returns {"success": bool, "results": [...], "summary": str, "artifacts": [...]}.
        """
        results: list[dict] = []
        artifacts: list[str] = []
        accumulated_context = belief_context
        all_success = True

        for i, step in enumerate(steps):
            skill_name = step["skill"]
            description = step.get("description", "")
            risk = step.get("risk_level", "safe")

            log.info("Executing step %d/%d: %s → %s",
                     i + 1, len(steps), skill_name, description[:60])

            # Generate params for this step using LLM
            params: dict = {}
            if self.internal_llm is not None:
                try:
                    skill = self.registry.get(skill_name)
                    skill_info = {
                        "name": skill_name,
                        "description": skill.description if skill else "",
                        "param_schema": skill.param_schema if skill else {},
                    }
                    enriched = self._enrich_belief_context(
                        skill_name, intent, accumulated_context[-2000:],
                    )
                    param_prompt = (
                        f"当前步骤：{description}\n"
                        f"之前步骤的输出：\n{enriched}\n"
                        f"原始意图：{intent}"
                    )
                    params = await self.internal_llm.plan_action_params(
                        param_prompt, skill_info, enriched[-1000:],
                    )
                except Exception as e:
                    log.warning("Param generation failed for step %d: %s", i + 1, e)
                    params = {"intent": description}

            # Execute
            action_plan = {"skill_name": skill_name, "params": params, "risk_level": risk}
            result = await self.execute_action(action_plan, pe_before=pe_before)

            step_result = {
                "step": i + 1,
                "skill": skill_name,
                "description": description,
                "success": result.success,
                "summary": result.summary,
                "error": result.error,
            }
            results.append(step_result)

            if result.artifacts:
                artifacts.extend(result.artifacts)

            # Feed output to next step
            output_text = str(result.result) if result.result else result.summary
            accumulated_context += f"\n\n[Step {i+1} output ({skill_name})]: {output_text[:1500]}"

            if not result.success:
                all_success = False
                # Try fallback for this step
                log.warning("Step %d failed: %s. Attempting fallback.", i + 1, result.error)
                fallback = await self.handle_skill_failure(
                    skill_name, result.error or "unknown", description,
                )
                if fallback:
                    fb_result = await self.execute_action(fallback, pe_before=pe_before)
                    if fb_result.success:
                        results[-1]["fallback"] = fallback["skill_name"]
                        results[-1]["success"] = True
                        all_success = True
                        output_text = str(fb_result.result) if fb_result.result else fb_result.summary
                        accumulated_context += f"\n[Fallback output]: {output_text[:1500]}"
                    else:
                        log.error("Fallback also failed for step %d. Continuing.", i + 1)

        # Build summary
        succeeded = sum(1 for r in results if r["success"])
        summary = f"Completed {succeeded}/{len(steps)} steps."
        if artifacts:
            summary += f" Artifacts: {', '.join(artifacts)}"

        step_summaries = "\n".join(
            f"  Step {r['step']}: {r['skill']} — {'✓' if r['success'] else '✗'} {r['summary']}"
            for r in results
        )

        # ── Belief extraction from accumulated output ──
        extracted_beliefs: list[dict] = []
        if self.external_llm is not None and accumulated_context.strip():
            try:
                extracted_beliefs = await self._extract_beliefs_from_output(
                    accumulated_context, intent,
                )
            except Exception as e:
                log.warning("Belief extraction from plan output failed: %s", e)

        return {
            "success": all_success,
            "results": results,
            "summary": summary,
            "details": step_summaries,
            "artifacts": artifacts,
            "accumulated_output": accumulated_context[-3000:],
            "extracted_beliefs": extracted_beliefs,
        }

    async def _extract_beliefs_from_output(
        self,
        accumulated_output: str,
        intent: str,
    ) -> list[dict]:
        """Extract new beliefs from plan execution output.

        Uses external_llm.extract_beliefs with a synthetic target belief
        derived from the original intent.  Also detects repeated patterns
        to store as PREFERENCE beliefs.
        """
        from ..dtypes import Belief, BeliefCategory, BeliefSource

        # Create a synthetic target belief from the intent
        dummy_belief = Belief(
            id="plan_output",
            statement=intent,
            confidence=0.5,
            source=BeliefSource.OBSERVATION,
            created_at=0, last_updated=0, last_verified=0,
            tags=["action_output"],
        )

        extraction = await self.external_llm.extract_beliefs(
            accumulated_output[-3000:], dummy_belief,
        )

        new_beliefs = extraction.get("new_beliefs", [])
        added: list[dict] = []

        for nb in new_beliefs:
            stmt = nb.get("statement", "")
            if not stmt:
                continue
            cat_str = nb.get("category", "fact")
            try:
                cat = BeliefCategory(cat_str)
            except ValueError:
                cat = BeliefCategory.FACT

            belief_data = {
                "statement": stmt,
                "confidence": nb.get("confidence", 0.5),
                "tags": nb.get("tags", ["action_output"]),
                "category": cat.value,
                "source": BeliefSource.OBSERVATION.value,
            }

            # If belief_graph is available, add directly
            if self.belief_graph is not None:
                new_b = Belief(
                    id="",
                    statement=stmt,
                    confidence=belief_data["confidence"],
                    source=BeliefSource.OBSERVATION,
                    created_at=0, last_updated=0, last_verified=0,
                    tags=belief_data["tags"],
                    category=cat,
                )
                bid = self.belief_graph.add_belief(new_b)
                belief_data["id"] = bid
                log.info("Extracted belief from plan output: %s → %s", bid, stmt[:60])

            added.append(belief_data)

        # ── Repeated-pattern detection → PREFERENCE beliefs ──
        if self.belief_graph is not None:
            await self._detect_preference_patterns(intent, added)

        return added

    async def _detect_preference_patterns(
        self,
        intent: str,
        new_beliefs: list[dict],
    ) -> None:
        """If the same skill has been called 3+ times successfully,
        extract common patterns and store as PREFERENCE beliefs."""
        from ..dtypes import Belief, BeliefCategory, BeliefSource

        # Count successful calls per skill from usage log
        skill_counts: dict[str, int] = {}
        for entry in self.registry.get_usage_history(last_n=100):
            if entry.get("success"):
                sname = entry.get("skill", "")
                skill_counts[sname] = skill_counts.get(sname, 0) + 1

        for skill_name, count in skill_counts.items():
            if count < 3:
                continue
            # Check if we already have a PREFERENCE belief for this skill
            if self.belief_graph is not None:
                existing = self.belief_graph.get_beliefs_by_tag(f"skill_pref_{skill_name}")
                if existing:
                    continue  # Already have a preference for this skill

                pref_b = Belief(
                    id="",
                    statement=f"User frequently uses {skill_name} skill (used {count} times successfully)",
                    confidence=min(0.9, 0.5 + count * 0.05),
                    source=BeliefSource.OBSERVATION,
                    created_at=0, last_updated=0, last_verified=0,
                    tags=[f"skill_pref_{skill_name}", "preference"],
                    category=BeliefCategory.PREFERENCE,
                )
                bid = self.belief_graph.add_belief(pref_b)
                log.info("Preference belief from repeated skill use: %s → %s", bid, skill_name)

    async def handle_skill_failure(
        self,
        skill_name: str,
        error: str,
        intent: str,
        sec_matrix: Any = None,
        memory: Optional[Memory] = None,
    ) -> Optional[dict]:
        """Attempt to find a fallback action after skill failure.

        Degradation strategy:
        1. Check procedural memory for alternatives
        2. Find alternate skill via registry
        3. Give up
        """
        mem = memory or self.memory
        log.info(
            "Handling failure for skill '%s': %s — seeking fallback",
            skill_name, error,
        )

        # Strategy 1: Check procedural memory
        for proc_id, proc in mem.procedures.items():
            total = proc.success_count + proc.failure_count
            if total == 0:
                continue
            rate = proc.success_count / total
            if rate < 0.3:
                continue
            # Check if procedure mentions a different skill
            for step in proc.steps:
                for candidate_name in self.registry._skills:
                    if candidate_name != skill_name and candidate_name in step.lower():
                        log.info(
                            "Fallback from procedural memory: %s -> %s (proc=%s)",
                            skill_name, candidate_name, proc_id,
                        )
                        skill = self.registry.get(candidate_name)
                        return {
                            "skill_name": candidate_name,
                            "params": {"intent": intent},
                            "match_reason": "procedural_memory_fallback",
                            "risk_level": skill.risk_level if skill else "safe",
                        }

        # Strategy 2: Find alternate skill via registry
        candidates = self.registry.select_skill(
            intent=intent,
            sec_matrix=sec_matrix,
            memory=mem,
        )
        for candidate in candidates:
            if candidate["name"] != skill_name:
                log.info(
                    "Fallback from registry: %s -> %s",
                    skill_name, candidate["name"],
                )
                return {
                    "skill_name": candidate["name"],
                    "params": {"intent": intent},
                    "match_reason": "registry_fallback",
                    "risk_level": candidate.get("risk_level", "safe"),
                }

        # Strategy 3: Give up
        log.warning("No fallback found for failed skill '%s'", skill_name)
        return None
