"""Smart Skill Registry — intelligent selection with SEC and memory integration."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


class SmartSkillRegistry:
    """Enhanced registry with intelligent skill selection."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._usage_log: list[dict] = []

    # ── Registration ──

    def register(self, skill: Skill) -> None:
        """Register a skill by its name."""
        self._skills[skill.name] = skill
        log.info("Registered skill: %s (risk=%s, caps=%s)",
                 skill.name, skill.risk_level, skill.capabilities)

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    # ── Discovery ──

    def discover(self) -> list[dict]:
        """Return summaries of all registered skills."""
        result: list[dict] = []
        for s in self._skills.values():
            result.append({
                "name": s.name,
                "description": s.description,
                "capabilities": s.capabilities,
                "risk_level": s.risk_level,
                "param_schema": s.param_schema,
                "success_rate": round(s.success_rate, 3),
                "avg_pe_improvement": round(s.avg_pe_improvement, 4),
            })
        return result

    # ── Intelligent selection ──

    def select_skill(
        self,
        intent: str,
        goal: str = "",
        sec_matrix: Any = None,
        memory: Any = None,
    ) -> list[dict]:
        """Select best-matching skills for an intent.

        Priority:
        1. Procedural memory match (memory.procedures has matching intent)
        2. Capability tag match
        3. SEC-weighted (skills whose tags have high C-values)
        4. Success rate fallback
        """
        if not self._skills:
            return []

        intent_lower = intent.lower()
        goal_lower = goal.lower() if goal else ""
        candidates: list[dict] = []

        # Expand intent with bilingual keyword mapping
        _keyword_map = {
            "写": "write_document generate_report",
            "报告": "generate_report write_document summarize",
            "分析": "analyze_data statistics trend_analysis",
            "搜索": "web_search information_retrieval",
            "搜": "web_search information_retrieval",
            "查": "web_search fetch_url",
            "代码": "run_code data_processing",
            "执行": "run_code automation",
            "跑": "run_code automation",
            "邮件": "send_email notify",
            "发送": "send_email communicate",
            "抓取": "fetch_url read_webpage",
            "网页": "fetch_url read_webpage extract_content",
            "文件": "write_file read_file create_file",
            "保存": "write_file create_file write_document",
            "读": "read_file inspect_content",
            "总结": "summarize generate_report",
            "摘要": "summarize generate_report",
            "数据": "analyze_data csv_processing statistics",
            "统计": "statistics analyze_data",
            "趋势": "trend_analysis analyze_data",
            "文档": "write_document format_text",
            "markdown": "write_document generate_report",
            "csv": "csv_processing analyze_data",
            "命令": "run_command system_admin",
            "shell": "run_command system_admin deployment",
            "部署": "deployment system_admin run_command",
            "截图": "capture_screenshot visual_inspect webpage_preview",
            "screenshot": "capture_screenshot visual_inspect",
            "日历": "read_calendar create_event list_events schedule",
            "日程": "read_calendar create_event schedule",
            "slack": "send_message notify_team slack_notification",
            "通知": "notify_team send_message slack_notification",
            "json": "query_json filter_data transform_json",
            "翻译": "translate_text language_conversion localize",
            "translate": "translate_text language_conversion",
        }
        expanded_intent = intent_lower
        for zh_key, en_caps in _keyword_map.items():
            if zh_key in intent_lower:
                expanded_intent += " " + en_caps

        # ── Priority 1: Procedural memory match ──
        memory_matched: set[str] = set()
        if memory is not None:
            for proc_id, proc in getattr(memory, "procedures", {}).items():
                total = proc.success_count + proc.failure_count
                if total == 0:
                    continue
                rate = proc.success_count / total
                # Check if procedure description matches intent
                if any(
                    word in proc.description.lower()
                    for word in intent_lower.split()
                    if len(word) > 2
                ):
                    # Find skills referenced in procedure steps
                    for step in proc.steps:
                        for skill_name in self._skills:
                            if skill_name in step.lower():
                                memory_matched.add(skill_name)

        for skill_name in memory_matched:
            s = self._skills[skill_name]
            candidates.append({
                "name": s.name,
                "description": s.description,
                "match_reason": "procedural_memory",
                "score": 1.0,
                "risk_level": s.risk_level,
            })

        # ── Priority 2: Capability tag match ──
        cap_scores: dict[str, float] = {}
        for s in self._skills.values():
            if s.name in memory_matched:
                continue
            score = 0.0
            for cap in s.capabilities:
                cap_words = cap.lower().replace("_", " ").split()
                for word in cap_words:
                    if word in expanded_intent or word in goal_lower:
                        score += 0.3
            if score > 0:
                cap_scores[s.name] = min(score, 0.9)

        # ── Priority 3: SEC-weighted boost ──
        if sec_matrix is not None:
            for skill_name, base_score in cap_scores.items():
                s = self._skills[skill_name]
                sec_boost = 0.0
                for cap in s.capabilities:
                    c_val = sec_matrix.get_c_value(cap)
                    if c_val > 0:
                        sec_boost = max(sec_boost, c_val * 0.2)
                cap_scores[skill_name] = base_score + sec_boost

        # ── Priority 4: Success rate boost ──
        for skill_name in cap_scores:
            s = self._skills[skill_name]
            cap_scores[skill_name] += s.success_rate * 0.1

        # Convert cap_scores to candidates
        for skill_name, score in sorted(
            cap_scores.items(), key=lambda x: x[1], reverse=True
        ):
            s = self._skills[skill_name]
            candidates.append({
                "name": s.name,
                "description": s.description,
                "match_reason": "capability_match",
                "score": round(score, 3),
                "risk_level": s.risk_level,
            })

        # If no candidates found, return all skills sorted by success rate
        if not candidates:
            for s in sorted(
                self._skills.values(),
                key=lambda x: x.success_rate,
                reverse=True,
            ):
                candidates.append({
                    "name": s.name,
                    "description": s.description,
                    "match_reason": "fallback",
                    "score": round(s.success_rate * 0.1, 3),
                    "risk_level": s.risk_level,
                })

        return candidates

    # ── Execution ──

    async def execute_skill(
        self, skill_name: str, params: dict, pe_before: float = 0.0
    ) -> SkillResult:
        """Execute a skill by name and wrap result."""
        skill = self._skills.get(skill_name)
        if skill is None:
            return SkillResult(
                success=False,
                error=f"Skill '{skill_name}' not found",
                summary=f"Skill not found: {skill_name}",
            )

        if skill.risk_level == "dangerous":
            log.warning(
                "Executing DANGEROUS skill '%s' with params: %s",
                skill_name, params,
            )

        start = time.time()
        try:
            raw = await skill.execute(params)
            elapsed = time.time() - start

            success = raw.get("success", False)
            result = SkillResult(
                success=success,
                result=raw.get("result", ""),
                error=raw.get("error"),
                artifacts=raw.get("artifacts", []),
                summary=f"{skill_name} executed in {elapsed:.2f}s",
                pe_impact=0.0,
            )

            # Record for usage log
            self._usage_log.append({
                "skill": skill_name,
                "success": success,
                "elapsed": elapsed,
                "timestamp": time.time(),
                "pe_before": pe_before,
            })
            if len(self._usage_log) > 500:
                self._usage_log = self._usage_log[-500:]

            return result

        except Exception as e:
            elapsed = time.time() - start
            log.error("Skill '%s' execution failed: %s", skill_name, e)

            self._usage_log.append({
                "skill": skill_name,
                "success": False,
                "elapsed": elapsed,
                "timestamp": time.time(),
                "pe_before": pe_before,
                "error": str(e),
            })

            return SkillResult(
                success=False,
                error=str(e),
                summary=f"{skill_name} failed after {elapsed:.2f}s: {e}",
            )

    # ── History ──

    def get_usage_history(self, last_n: int = 50) -> list[dict]:
        """Return recent skill execution history."""
        return self._usage_log[-last_n:]

    def list_skills(self) -> list[dict]:
        """Backward-compatible list."""
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills.values()
        ]
