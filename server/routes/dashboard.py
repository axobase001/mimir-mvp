from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


class EmailSettingsPayload(BaseModel):
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    from_addr: str = ""
    to_addr: str = ""
    daily_digest: bool = True
    weekly_digest: bool = True
    realtime_alerts: bool = True
    digest_hour: int = 8
    send_test: bool = False


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
            "status": b.status,
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
        "status": b.status,
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


@router.get("/api/metrics/llm_calls")
async def get_llm_call_metrics(request: Request):
    """Return per-caller LLM call stats, fast_path hit rate, and recent call log."""
    user_id, engine, state = _get_user_brain(request)
    llm_client = state["llm_client"]

    # Per-caller aggregated stats
    caller_stats = llm_client.get_caller_stats()

    # Fast path hit/miss from cycle engine
    hits = engine.fast_path_hits
    misses = engine.fast_path_misses
    total = hits + misses
    hit_rate = hits / total if total > 0 else 0.0

    # Recent per-call log (last 50)
    call_log = llm_client.get_call_log(last_n=50)

    return {
        "caller_stats": caller_stats,
        "fast_path": {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hit_rate, 4),
        },
        "call_log": call_log,
    }


@router.get("/api/metrics/learning_curve")
async def get_learning_curve(request: Request):
    """Return learning curve metrics extracted from cycle logs."""
    user_id, engine, state = _get_user_brain(request)
    mem = state["memory"]
    bg = state["belief_graph"]
    sec = state["sec_matrix"]
    llm_client = state["llm_client"]

    # Per-cycle token consumption trend (from usage stats)
    usage = llm_client.get_usage_stats()
    avg_tokens_per_cycle = 0
    if engine.cycle_count > 0 and usage.get("prompt_tokens"):
        avg_tokens_per_cycle = (
            usage["prompt_tokens"] + usage["completion_tokens"]
        ) / engine.cycle_count

    # New belief acquisition efficiency: beliefs per cycle
    total_beliefs = len(bg.get_all_beliefs())
    beliefs_per_cycle = total_beliefs / max(1, engine.cycle_count)

    # SEC differentiation: spread of C values
    c_values = [e.c_value for e in sec.entries.values()]
    sec_spread = max(c_values) - min(c_values) if c_values else 0.0

    # Repeated task PE trend from episodes
    pe_trend = []
    for ep in mem.episodes:
        pe_trend.append({
            "cycle": ep.cycle,
            "pe_before": round(ep.pe_before, 4),
            "pe_after": round(ep.pe_after, 4),
            "pe_delta": round(ep.pe_before - ep.pe_after, 4),
        })

    return {
        "cycle_count": engine.cycle_count,
        "avg_tokens_per_cycle": round(avg_tokens_per_cycle, 1),
        "beliefs_per_cycle": round(beliefs_per_cycle, 2),
        "total_beliefs": total_beliefs,
        "sec_spread": round(sec_spread, 4),
        "sec_cluster_count": len(sec.entries),
        "pe_trend": pe_trend[-50:],  # Last 50 cycles
    }


@router.get("/api/email_settings")
async def get_email_settings(request: Request):
    """Return current email notification settings."""
    user_id, engine, state = _get_user_brain(request)
    notifier = state.get("email_notifier")
    if notifier is None:
        return {
            "configured": False,
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "from_addr": "",
            "to_addr": "",
            "daily_digest": False,
            "weekly_digest": False,
            "realtime_alerts": True,
            "digest_hour": 8,
        }
    cfg = notifier.config
    return {
        "configured": cfg.enabled,
        "smtp_host": cfg.smtp_host,
        "smtp_port": cfg.smtp_port,
        "smtp_user": cfg.smtp_user,
        "from_addr": cfg.from_addr,
        "to_addr": cfg.to_addr,
        "daily_digest": cfg.daily_digest,
        "weekly_digest": cfg.weekly_digest,
        "realtime_alerts": cfg.realtime_alerts,
        "digest_hour": cfg.digest_hour,
    }


@router.post("/api/email_settings")
async def update_email_settings(request: Request, payload: EmailSettingsPayload):
    """Update email notification settings and optionally send a test email."""
    from ...core.email_notifier import EmailNotifier, EmailConfig

    user_id, engine, state = _get_user_brain(request)

    email_config = EmailConfig(
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_user=payload.smtp_user,
        smtp_pass=payload.smtp_pass,
        from_addr=payload.from_addr or payload.smtp_user,
        to_addr=payload.to_addr,
        enabled=bool(payload.smtp_host and payload.smtp_user),
        daily_digest=payload.daily_digest,
        weekly_digest=payload.weekly_digest,
        realtime_alerts=payload.realtime_alerts,
        digest_hour=payload.digest_hour,
    )

    notifier = EmailNotifier(email_config)
    state["email_notifier"] = notifier
    engine.email_notifier = notifier

    result = {"saved": True, "test_sent": False, "test_error": None}

    # Send test email if requested
    if payload.send_test and email_config.enabled:
        subject = "[Skuld] 测试邮件 — 通知系统已配置"
        html = """
        <div style="font-family:'Source Sans 3',-apple-system,sans-serif;max-width:600px;margin:0 auto;color:#1A1A1F;line-height:1.6;">
            <div style="padding:20px 0;border-bottom:1px solid rgba(0,0,0,0.06);">
                <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#5DCAA5;letter-spacing:3px;text-transform:uppercase;">SKULD 测试邮件</span>
            </div>
            <div style="padding:20px 0;">
                <p>Email通知系统已成功配置。你将在以下情况收到通知：</p>
                <ul>
                    <li>每日/每周总结</li>
                    <li>Brain产生新推理</li>
                    <li>目标完成或放弃</li>
                    <li>重大预测误差</li>
                </ul>
            </div>
        </div>
        """
        success = await notifier.send_email_async(subject, html)
        result["test_sent"] = success
        if not success:
            result["test_error"] = "发送失败，请检查SMTP配置"

    return result
