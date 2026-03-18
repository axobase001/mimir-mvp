"""Learning curve benchmark: measure Skuld's improvement over repeated tasks.

Uses mock LLM and mock search (no real API calls).
Outputs CSV and optionally matplotlib charts.
"""

import asyncio
import csv
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

from mimir.config import MimirConfig
from mimir.types import Belief, BeliefCategory, BeliefSource
from mimir.brain.belief_graph import BeliefGraph
from mimir.brain.sec_matrix import SECMatrix
from mimir.brain.prediction import PredictionEngine
from mimir.brain.goal_generator import GoalGenerator
from mimir.brain.memory import Memory
from mimir.llm.client import LLMClient
from mimir.llm.internal import InternalLLM
from mimir.llm.external import ExternalLLM
from mimir.skills.base import SkillRegistry, Skill
from mimir.core.cycle import MimirCycle
from mimir.core.notifier import Notifier


# ── Mock components ──

class MockSearchSkill(Skill):
    call_count = 0

    @property
    def name(self) -> str:
        return "brave_search"

    @property
    def description(self) -> str:
        return "mock search"

    async def execute(self, params: dict) -> dict:
        MockSearchSkill.call_count += 1
        return {
            "success": True,
            "result": f"Search result #{self.call_count}: relevant data found.",
            "error": None,
        }


TASK_DEFINITIONS = [
    {
        "name": "search_summarize",
        "seeds": [
            ("s0", "AI chip market growing rapidly", 0.6, ["ai_chips", "market"]),
            ("s1", "NVIDIA dominates GPU market", 0.8, ["ai_chips", "nvidia"]),
            ("s2", "AMD entering AI accelerator space", 0.5, ["ai_chips", "amd"]),
        ],
    },
    {
        "name": "data_analysis",
        "seeds": [
            ("s0", "Global GDP growth projected at 3.1%", 0.7, ["economics", "gdp"]),
            ("s1", "Inflation declining in major economies", 0.6, ["economics", "inflation"]),
            ("s2", "Interest rates expected to decrease", 0.5, ["economics", "rates"]),
        ],
    },
    {
        "name": "document_generation",
        "seeds": [
            ("s0", "Python 3.12 released with performance improvements", 0.8, ["python", "release"]),
            ("s1", "Type hints adoption increasing in Python ecosystem", 0.7, ["python", "types"]),
            ("s2", "AsyncIO usage growing in web frameworks", 0.6, ["python", "async"]),
        ],
    },
]


def _make_engine(task: dict) -> MimirCycle:
    config = MimirConfig(
        sec_warmup_cycles=2,
        search_budget_per_cycle=2,
        reasoning_interval=3,
        abstraction_interval=100,
        goal_pe_threshold=0.3,
        goal_pe_persistence=2,
        cycle_interval_seconds=0,
    )

    bg = BeliefGraph(config)
    for bid, stmt, conf, tags in task["seeds"]:
        bg.add_belief(Belief(
            id=bid, statement=stmt, confidence=conf,
            source=BeliefSource.SEED, created_at=0,
            last_updated=0, last_verified=0, tags=tags,
        ))

    sec = SECMatrix(config)
    pe = PredictionEngine(config)
    mem = Memory(config)
    gg = GoalGenerator(config, bg, sec)
    notifier = Notifier()

    client = LLMClient(api_key="test", base_url="http://test", model="test")

    external = ExternalLLM(client, config)
    external.intent_to_query = AsyncMock(side_effect=lambda intent, **kw: intent[:50])

    _call_n = {"n": 0}

    async def mock_extract(results, belief):
        _call_n["n"] += 1
        return {
            "verdict": "support" if _call_n["n"] % 3 != 0 else "contradict",
            "observed_confidence": 0.75,
            "extracted_facts": ["mock fact"],
            "new_beliefs": [
                {
                    "statement": f"Discovered fact #{_call_n['n']}",
                    "tags": belief.tags[:1] if belief.tags else ["misc"],
                    "confidence": 0.6,
                    "category": "fact",
                }
            ],
        }

    external.extract_beliefs = mock_extract
    external.summarize_cycle = AsyncMock(return_value="Cycle done.")
    external.chat_answer = AsyncMock(return_value="Mock answer.")

    internal = InternalLLM(client, config)
    internal.reason = AsyncMock(return_value=None)
    internal.abstract = AsyncMock(return_value=None)

    registry = SkillRegistry()
    registry.register(MockSearchSkill())

    return MimirCycle(
        belief_graph=bg, sec_matrix=sec, prediction_engine=pe,
        goal_generator=gg, memory=mem, internal_llm=internal,
        external_llm=external, skill_registry=registry,
        notifier=notifier, config=config,
    )


def run_benchmark(output_dir: str = "benchmarks/output") -> dict:
    """Run the learning curve benchmark across all tasks.

    Returns a summary dict with results.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    results = {}

    for task in TASK_DEFINITIONS:
        task_name = task["name"]
        rows = []
        engine = _make_engine(task)
        MockSearchSkill.call_count = 0

        for iteration in range(1, 11):
            t0 = time.time()
            summary = asyncio.run(engine.run_one_cycle())
            elapsed = time.time() - t0

            agg_pe = summary["phases"]["pe"]["aggregate"]
            belief_count = summary.get("belief_count", len(engine.bg.get_all_beliefs()))
            search_calls = MockSearchSkill.call_count

            rows.append({
                "iteration": iteration,
                "elapsed_s": round(elapsed, 3),
                "aggregate_pe": round(agg_pe, 4),
                "belief_count": belief_count,
                "search_calls": search_calls,
                "goals": summary.get("active_goals", 0),
            })

        # Write CSV
        csv_path = os.path.join(output_dir, f"{task_name}.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        results[task_name] = {
            "csv": csv_path,
            "iterations": len(rows),
            "final_pe": rows[-1]["aggregate_pe"],
            "final_beliefs": rows[-1]["belief_count"],
        }

    # Optional: generate matplotlib chart
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, len(TASK_DEFINITIONS), figsize=(15, 5))
        if len(TASK_DEFINITIONS) == 1:
            axes = [axes]

        for ax, task in zip(axes, TASK_DEFINITIONS):
            csv_path = os.path.join(output_dir, f"{task['name']}.csv")
            iters, pes = [], []
            with open(csv_path) as f:
                for row in csv.DictReader(f):
                    iters.append(int(row["iteration"]))
                    pes.append(float(row["aggregate_pe"]))
            ax.plot(iters, pes, "o-", color="green")
            ax.set_title(task["name"])
            ax.set_xlabel("Iteration")
            ax.set_ylabel("Aggregate PE")
            ax.set_ylim(bottom=0)

        plt.tight_layout()
        chart_path = os.path.join(output_dir, "learning_curves.png")
        plt.savefig(chart_path, dpi=100)
        plt.close()
        results["chart"] = chart_path
    except ImportError:
        results["chart"] = None

    return results


if __name__ == "__main__":
    out = run_benchmark()
    print("Benchmark complete:")
    for k, v in out.items():
        print(f"  {k}: {v}")
