from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


def _get_user_brain(request: Request):
    """Get the Brain engine and state for the current user."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    scheduler = request.app.state.scheduler
    engine = scheduler.get_brain_engine(user_id)
    state = scheduler.get_brain_state(user_id)

    if engine is None or state is None:
        raise HTTPException(status_code=404, detail="Brain not initialized. Complete onboarding first.")

    return user_id, engine, state


@router.get("/api/dashboard")
async def get_dashboard(request: Request):
    user_id, engine, state = _get_user_brain(request)

    bg = state["belief_graph"]
    sec = state["sec_matrix"]
    gg = state["goal_generator"]
    mem = state["memory"]
    llm_client = state["llm_client"]

    nodes = []
    for b in bg.get_all_beliefs():
        nodes.append({
            "id": b.id,
            "statement": b.statement,
            "confidence": round(b.confidence, 4),
            "source": b.source.value,
            "tags": b.tags,
            "pe_latest": round(b.pe_history[-1], 4) if b.pe_history else 0,
            "created_at": b.created_at,
            "last_updated": b.last_updated,
        })

    edges = [
        {"from": u, "to": v, "weight": d.get("weight", 1.0)}
        for u, v, d in bg.graph.edges(data=True)
    ]

    clusters = sorted(
        [
            {
                "name": name,
                "c_value": round(e.c_value, 4),
                "obs_count": e.obs_count,
                "not_count": e.not_count,
            }
            for name, e in sec.entries.items()
        ],
        key=lambda x: x["c_value"],
        reverse=True,
    )

    goals = [
        {
            "id": g.id,
            "description": g.description,
            "reason": g.reason,
            "priority": round(g.priority, 4),
            "status": g.status.value,
            "created_at": g.created_at,
        }
        for g in gg.goals.values()
    ]

    episodes = [
        {
            "cycle": ep.cycle,
            "action": ep.action,
            "outcome": ep.outcome,
            "pe_change": round(ep.pe_before - ep.pe_after, 4),
        }
        for ep in mem.episodes[-10:]
    ]

    # Notification history from notifier (already pulled, but we track in engine)
    notifications = []

    return {
        "cycle_count": engine.cycle_count,
        "belief_count": len(nodes),
        "belief_graph": {"nodes": nodes, "edges": edges},
        "sec_matrix": {"clusters": clusters},
        "active_goals": [g for g in goals if g["status"] == "active"],
        "all_goals": goals,
        "recent_episodes": episodes,
        "notifications": notifications,
        "usage_stats": llm_client.get_usage_stats(),
    }


@router.get("/api/beliefs/{belief_id}")
async def get_belief_detail(belief_id: str, request: Request):
    user_id, engine, state = _get_user_brain(request)
    bg = state["belief_graph"]
    b = bg.get_belief(belief_id)
    if b is None:
        return {"error": "not found"}
    return {
        "id": b.id,
        "statement": b.statement,
        "confidence": b.confidence,
        "source": b.source.value,
        "tags": b.tags,
        "pe_history": b.pe_history,
        "parent_ids": b.parent_ids,
        "created_at": b.created_at,
        "last_updated": b.last_updated,
        "last_verified": b.last_verified,
    }


@router.get("/api/sec/{cluster_name}")
async def get_sec_detail(cluster_name: str, request: Request):
    user_id, engine, state = _get_user_brain(request)
    sec = state["sec_matrix"]
    entry = sec.entries.get(cluster_name)
    if entry is None:
        return {"error": "not found"}
    return {
        "cluster": entry.cluster,
        "c_value": entry.c_value,
        "d_obs": entry.d_obs,
        "d_not": entry.d_not,
        "obs_count": entry.obs_count,
        "not_count": entry.not_count,
    }


@router.get("/api/cycle_log")
async def get_cycle_log(request: Request, last_n: int = 20):
    user_id, engine, state = _get_user_brain(request)
    mem = state["memory"]
    return {"episodes": [
        {
            "cycle": ep.cycle,
            "action": ep.action,
            "outcome": ep.outcome,
            "pe_before": ep.pe_before,
            "pe_after": ep.pe_after,
        }
        for ep in mem.episodes[-last_n:]
    ]}
