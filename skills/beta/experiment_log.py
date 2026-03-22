"""Experiment log skill — local lab notebook for tracking experiments."""

import json
import logging
import os
import time
import uuid
from typing import Any

from ..base import Skill

log = logging.getLogger(__name__)

_DEFAULT_LOG_PATH = os.path.join("data", "experiment_log.json")


class ExperimentLogSkill(Skill):
    """Track experiments with hypotheses, methods, results, and metrics."""

    def __init__(self, log_path: str = _DEFAULT_LOG_PATH):
        super().__init__()
        self._log_path = log_path
        self._experiments: list[dict[str, Any]] = []
        self._load()

    # ── Properties ──

    @property
    def name(self) -> str:
        return "experiment_log"

    @property
    def description(self) -> str:
        return "Log and search experiments with hypotheses, methods, and results"

    @property
    def capabilities(self) -> list[str]:
        return ["experiment_logging", "research_tracking", "lab_notebook"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: log, list, search, compare",
            },
            "hypothesis": {
                "type": "str",
                "required": False,
                "description": "Experiment hypothesis (for log)",
            },
            "method": {
                "type": "str",
                "required": False,
                "description": "Experiment method (for log)",
            },
            "results": {
                "type": "str",
                "required": False,
                "description": "Experiment results (for log)",
            },
            "metrics": {
                "type": "dict",
                "required": False,
                "description": "Key-value metrics (for log)",
            },
            "notes": {
                "type": "str",
                "required": False,
                "description": "Additional notes (for log)",
            },
            "keyword": {
                "type": "str",
                "required": False,
                "description": "Search keyword (for search)",
            },
            "ids": {
                "type": "list[str]",
                "required": False,
                "description": "Two experiment IDs to compare (for compare)",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "list")

        if action == "log":
            return self._log_experiment(params)
        elif action == "list":
            return self._list_experiments()
        elif action == "search":
            return self._search_experiments(params)
        elif action == "compare":
            return self._compare_experiments(params)
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    def _log_experiment(self, params: dict) -> dict:
        experiment = {
            "id": str(uuid.uuid4())[:8],
            "hypothesis": params.get("hypothesis", ""),
            "method": params.get("method", ""),
            "results": params.get("results", ""),
            "metrics": params.get("metrics", {}),
            "notes": params.get("notes", ""),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._experiments.append(experiment)
        self._save()

        return {
            "success": True,
            "result": f"Experiment logged with ID: {experiment['id']}\n"
                      f"{self._format_experiment(experiment)}",
            "error": None,
        }

    def _list_experiments(self) -> dict:
        if not self._experiments:
            return {
                "success": True,
                "result": "No experiments logged yet.",
                "error": None,
            }

        lines = []
        for exp in self._experiments:
            lines.append(
                f"[{exp['id']}] {exp['timestamp']} — "
                f"H: {exp['hypothesis'][:60]}..."
                if len(exp['hypothesis']) > 60
                else f"[{exp['id']}] {exp['timestamp']} — H: {exp['hypothesis']}"
            )
        return {"success": True, "result": "\n".join(lines), "error": None}

    def _search_experiments(self, params: dict) -> dict:
        keyword = params.get("keyword", "").strip().lower()
        if not keyword:
            return {"success": False, "result": "", "error": "'keyword' is required"}

        matches = []
        for exp in self._experiments:
            searchable = " ".join([
                exp.get("hypothesis", ""),
                exp.get("method", ""),
                exp.get("results", ""),
                exp.get("notes", ""),
                json.dumps(exp.get("metrics", {})),
            ]).lower()
            if keyword in searchable:
                matches.append(exp)

        if not matches:
            return {
                "success": True,
                "result": f"No experiments matching '{keyword}'.",
                "error": None,
            }

        text = "\n\n".join(self._format_experiment(e) for e in matches)
        return {
            "success": True,
            "result": f"Found {len(matches)} matching experiments:\n\n{text}",
            "error": None,
        }

    def _compare_experiments(self, params: dict) -> dict:
        ids = params.get("ids", [])
        if not isinstance(ids, list) or len(ids) != 2:
            return {
                "success": False,
                "result": "",
                "error": "'ids' must be a list of exactly 2 experiment IDs",
            }

        id_a, id_b = ids[0], ids[1]
        exp_a = self._find_by_id(id_a)
        exp_b = self._find_by_id(id_b)

        if not exp_a:
            return {
                "success": False,
                "result": "",
                "error": f"Experiment '{id_a}' not found",
            }
        if not exp_b:
            return {
                "success": False,
                "result": "",
                "error": f"Experiment '{id_b}' not found",
            }

        comparison = (
            f"=== Experiment {id_a} vs {id_b} ===\n\n"
            f"--- {id_a} ---\n{self._format_experiment(exp_a)}\n\n"
            f"--- {id_b} ---\n{self._format_experiment(exp_b)}\n\n"
        )

        # Compare metrics if both have them
        metrics_a = exp_a.get("metrics", {})
        metrics_b = exp_b.get("metrics", {})
        all_keys = sorted(set(list(metrics_a.keys()) + list(metrics_b.keys())))
        if all_keys:
            comparison += "--- Metrics Comparison ---\n"
            for key in all_keys:
                val_a = metrics_a.get(key, "N/A")
                val_b = metrics_b.get(key, "N/A")
                comparison += f"  {key}: {val_a} vs {val_b}\n"

        return {"success": True, "result": comparison, "error": None}

    # ── Helpers ──

    def _find_by_id(self, exp_id: str) -> dict | None:
        for exp in self._experiments:
            if exp["id"] == exp_id:
                return exp
        return None

    @staticmethod
    def _format_experiment(exp: dict) -> str:
        metrics_str = ""
        if exp.get("metrics"):
            metrics_str = "\n  Metrics: " + ", ".join(
                f"{k}={v}" for k, v in exp["metrics"].items()
            )
        return (
            f"ID: {exp['id']} | {exp['timestamp']}\n"
            f"  Hypothesis: {exp['hypothesis']}\n"
            f"  Method: {exp['method']}\n"
            f"  Results: {exp['results']}"
            f"{metrics_str}\n"
            f"  Notes: {exp['notes']}"
        )

    def _load(self) -> None:
        """Load experiments from JSON file if it exists."""
        if os.path.isfile(self._log_path):
            try:
                with open(self._log_path, "r", encoding="utf-8") as f:
                    self._experiments = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Could not load experiment log from %s: %s", self._log_path, e)
                self._experiments = []

    def _save(self) -> None:
        """Persist experiments to JSON file."""
        try:
            os.makedirs(os.path.dirname(self._log_path) or ".", exist_ok=True)
            with open(self._log_path, "w", encoding="utf-8") as f:
                json.dump(self._experiments, f, indent=2, ensure_ascii=False)
        except OSError as e:
            log.warning("Could not save experiment log to %s: %s", self._log_path, e)
