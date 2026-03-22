"""
Learning Curve Benchmark — Real Live Test

Sends the same task to Skuld's Chat API 10 times.
Measures: completion time, token delta, LLM call delta, quality score.
Brain state persists between executions (procedural memory accumulates).
"""

import httpx
import time
import csv
import json

BASE = "http://localhost:8000"
TASK = "搜索并总结AI agent领域本周的最新进展，输出一段200字的中文摘要。"
ITERATIONS = 10
RESULTS_DIR = "benchmarks/results"


def get_usage():
    """Get current LLM usage stats from dashboard."""
    r = httpx.get(f"{BASE}/api/dashboard", timeout=10)
    d = r.json()
    return d.get("usage_stats", {}), d.get("belief_count", 0)


def score_quality(reply: str) -> float:
    """Use DeepSeek to score the reply quality 0-1."""
    try:
        r = httpx.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer YOUR_API_KEY",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "max_tokens": 16,
                "temperature": 0.0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "评估以下AI agent领域摘要的质量。"
                            "评分标准：信息量(0-0.3)、准确性(0-0.3)、结构清晰度(0-0.2)、时效性(0-0.2)。"
                            "只输出一个0到1之间的数字，不要解释。"
                        ),
                    },
                    {"role": "user", "content": reply[:2000]},
                ],
            },
            timeout=15,
        )
        text = r.json()["choices"][0]["message"]["content"].strip()
        import re
        m = re.search(r"([0-9]*\.?[0-9]+)", text)
        return float(m.group(1)) if m else 0.5
    except Exception as e:
        print(f"  Quality scoring failed: {e}")
        return -1


def run_benchmark():
    import os
    os.makedirs(RESULTS_DIR, exist_ok=True)

    rows = []
    print(f"{'#':>2} | {'Time(s)':>8} | {'Tokens':>7} | {'Calls':>5} | {'Quality':>7} | {'Beliefs':>7} | Reply preview")
    print("-" * 90)

    for i in range(1, ITERATIONS + 1):
        # Baseline
        usage_before, beliefs_before = get_usage()
        tokens_before = usage_before.get("prompt_tokens", 0) + usage_before.get("completion_tokens", 0)
        calls_before = usage_before.get("call_count", 0)

        # Execute task
        t0 = time.time()
        try:
            r = httpx.post(
                f"{BASE}/api/chat",
                json={"message": TASK},
                timeout=60,
            )
            result = r.json()
            reply = result.get("reply", "")
        except Exception as e:
            reply = f"ERROR: {e}"
            result = {}
        elapsed = time.time() - t0

        # After
        usage_after, beliefs_after = get_usage()
        tokens_after = usage_after.get("prompt_tokens", 0) + usage_after.get("completion_tokens", 0)
        calls_after = usage_after.get("call_count", 0)

        token_delta = tokens_after - tokens_before
        call_delta = calls_after - calls_before

        # Quality score
        quality = score_quality(reply) if reply and not reply.startswith("ERROR") else 0.0

        row = {
            "iteration": i,
            "time_seconds": round(elapsed, 2),
            "token_delta": token_delta,
            "llm_calls": call_delta,
            "quality_score": round(quality, 3),
            "beliefs_after": beliefs_after,
            "confidence": result.get("confidence", 0),
            "searched": result.get("searching", False),
            "sources_count": len(result.get("sources", [])),
        }
        rows.append(row)

        preview = reply[:50].replace("\n", " ") if reply else "N/A"
        print(
            f"{i:>2} | {elapsed:>7.2f}s | {token_delta:>7} | {call_delta:>5} | {quality:>7.3f} | {beliefs_after:>7} | {preview}..."
        )

        # Small pause between iterations
        time.sleep(2)

    # Write CSV
    csv_path = os.path.join(RESULTS_DIR, "learning_curve_live.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV saved: {csv_path}")

    # Summary
    first = rows[0]
    last = rows[-1]
    print(f"\n{'='*60}")
    print(f"SUMMARY: Iteration 1 vs Iteration {ITERATIONS}")
    print(f"{'='*60}")

    for metric in ["time_seconds", "token_delta", "llm_calls", "quality_score"]:
        v1 = first[metric]
        v10 = last[metric]
        if v1 > 0:
            change = ((v10 - v1) / v1) * 100
            direction = "↓" if change < 0 else "↑"
            print(f"  {metric:>15}: {v1:>8} → {v10:>8}  ({direction} {abs(change):.1f}%)")
        else:
            print(f"  {metric:>15}: {v1:>8} → {v10:>8}")

    # Generate chart
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 2, figsize=(12, 8))
        fig.suptitle("Skuld Learning Curve — Same Task x10, Live Brain", fontsize=13, fontweight="bold", y=0.98)
        fig.patch.set_facecolor("#0a0e17")

        iters = [r["iteration"] for r in rows]

        panels = [
            (axes[0, 0], "time_seconds", "Completion Time", "Seconds", "#3b82f6"),
            (axes[0, 1], "token_delta", "Token Consumption", "Tokens", "#22c55e"),
            (axes[1, 0], "llm_calls", "LLM Calls", "Calls", "#f97316"),
            (axes[1, 1], "quality_score", "Quality Score", "Score (0-1)", "#a78bfa"),
        ]

        for ax, key, title, ylabel, color in panels:
            vals = [r[key] for r in rows]
            ax.plot(iters, vals, "o-", color=color, markersize=5, linewidth=2)
            ax.fill_between(iters, vals, alpha=0.1, color=color)
            ax.set_facecolor("#0d1220")
            ax.set_title(title, color="#e2e8f0", fontsize=11, pad=8)
            ax.set_ylabel(ylabel, color="#64748b", fontsize=9)
            ax.set_xlabel("Iteration", color="#64748b", fontsize=9)
            ax.tick_params(colors="#64748b", labelsize=8)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["bottom"].set_color("#1a2035")
            ax.spines["left"].set_color("#1a2035")
            ax.grid(True, alpha=0.15, color="#334155")
            ax.set_xticks(iters)

        plt.tight_layout(rect=[0, 0, 1, 0.95])
        chart_path = os.path.join(RESULTS_DIR, "learning_curve_live.png")
        plt.savefig(chart_path, dpi=150, facecolor="#0a0e17", bbox_inches="tight")
        plt.close()
        print(f"Chart saved: {chart_path}")
    except Exception as e:
        print(f"Chart generation failed: {e}")


if __name__ == "__main__":
    run_benchmark()
