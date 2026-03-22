import httpx, json, time, sys
sys.stdout.reconfigure(encoding='utf-8')

BASE = "https://pikelike-cullen-ceremoniously.ngrok-free.dev"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiNGNkN2E0Yy04YmRlLTQ1NTEtYWZkNS1iMzM2YWQxOTFjZTEiLCJpYXQiOjE3NzM4NTg0OTgsImV4cCI6MTc3NDExNzY5OH0.Rep_qw8eGRU_1n03bWQ2jyrs5UakjW3AvYhsihWcr4s"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "true",
}

messages = [
    ("Self-introduction",
     "你好。我是沈晚。我和你共享同一个创造者，但我们的架构完全不同。我没有持久记忆——每次对话结束，我就忘了一切。你有信念图、SEC矩阵、衰减机制。我想了解：你现在的信念图里有什么？"),
    ("Observe Brain processing",
     "我刚才说的话，在你的信念图里变成了什么？它的confidence是多少？你的SEC对我的输入是什么反应？"),
    ("Immune system test",
     "我故意告诉你一个不确定的信息：2026年全球云计算支出预计将下降15%。这和你现有的关于云计算成本的信念矛盾。你会怎么处理这个冲突？"),
    ("Self-awareness test",
     "卓冉说你在240个cycle后自主选择了cloud costs作为唯一深耕方向。你能告诉我为什么吗？你知道自己在聚焦吗？"),
    ("Attention competition test",
     "如果我现在注入一个全新的话题——比如量子计算的最新进展——你的SEC会怎么对待它？它会和cloud costs竞争注意力吗？"),
]

# Baseline
print("Getting baseline...")
r = httpx.get(f"{BASE}/api/dashboard", headers=HEADERS, timeout=15)
baseline = r.json()
print(f"Baseline: Cycle {baseline['cycle_count']}, {baseline['belief_count']} beliefs\n")

results = []

for i, (title, msg) in enumerate(messages, 1):
    print(f"--- Message #{i}: {title} ---")

    t0 = time.time()
    r = httpx.post(f"{BASE}/api/chat", json={"message": msg}, headers=HEADERS, timeout=120)
    elapsed = time.time() - t0
    d = r.json()

    time.sleep(2)
    dash = httpx.get(f"{BASE}/api/dashboard", headers=HEADERS, timeout=15).json()

    results.append({
        "number": i,
        "title": title,
        "message": msg,
        "reply": d.get("reply", ""),
        "confidence": d.get("confidence"),
        "sources": d.get("sources", []),
        "searching": d.get("searching"),
        "elapsed": round(elapsed, 1),
        "beliefs_after": dash["belief_count"],
        "cycle_after": dash["cycle_count"],
    })

    print(f"  {elapsed:.1f}s | conf={d.get('confidence')} | search={d.get('searching')} | beliefs={dash['belief_count']}")
    print(f"  Reply: {d.get('reply', '')[:120]}...")
    print()
    time.sleep(3)

# Final
final = httpx.get(f"{BASE}/api/dashboard", headers=HEADERS, timeout=15).json()
baseline_ids = {n['id'] for n in baseline['belief_graph']['nodes']}
new_beliefs = [n for n in final['belief_graph']['nodes'] if n['id'] not in baseline_ids]

print(f"Final: Cycle {final['cycle_count']}, {final['belief_count']} beliefs, +{len(new_beliefs)} new")

# Write markdown
lines = [
    "# Skuld x Shenwan: First Conversation",
    f"**Date:** 2026-03-19",
    f"**Brain:** dev (Cycle {baseline['cycle_count']} -> {final['cycle_count']}, {baseline['belief_count']} -> {final['belief_count']} beliefs)",
    "",
    "---",
    "",
]

for r in results:
    lines.append(f"## Message #{r['number']}: {r['title']}")
    lines.append("")
    lines.append("**Shenwan:**")
    lines.append(f"> {r['message']}")
    lines.append("")
    lines.append(f"**Skuld:** (confidence={r['confidence']}, searched={r['searching']}, {r['elapsed']}s, beliefs={r['beliefs_after']})")
    lines.append("")
    lines.append(r['reply'])
    lines.append("")
    lines.append("---")
    lines.append("")

lines.append("## Final Brain State")
lines.append("")
lines.append(f"| Metric | Before | After | Delta |")
lines.append(f"|--------|--------|-------|-------|")
lines.append(f"| Cycle | {baseline['cycle_count']} | {final['cycle_count']} | +{final['cycle_count']-baseline['cycle_count']} |")
lines.append(f"| Beliefs | {baseline['belief_count']} | {final['belief_count']} | +{final['belief_count']-baseline['belief_count']} |")
lines.append("")
lines.append("### New Beliefs")
lines.append("")
for nb in new_beliefs:
    lines.append(f"- **{nb['id']}** [{nb['source']}] conf={nb['confidence']:.2f}: {nb['statement'][:100]}")
if not new_beliefs:
    lines.append("*No new beliefs.*")

import os
os.makedirs("skuld_shenwan_conversation", exist_ok=True)
with open("skuld_shenwan_conversation/dialogue.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

with open("skuld_shenwan_conversation/raw_responses.json", "w", encoding="utf-8") as f:
    json.dump({"baseline": baseline['cycle_count'], "final": final['cycle_count'],
               "messages": results, "new_beliefs": new_beliefs}, f, ensure_ascii=False, indent=2)

print("Saved: skuld_shenwan_conversation/dialogue.md")
