from fastapi import APIRouter, Request, HTTPException

from ...dtypes import Belief, BeliefSource

router = APIRouter()


def _get_user_brain(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    scheduler = request.app.state.scheduler
    engine = scheduler.get_brain_engine(user_id)
    state = scheduler.get_brain_state(user_id)

    if engine is None or state is None:
        raise HTTPException(status_code=404, detail="Brain not initialized")

    return user_id, engine, state


@router.post("/api/beliefs")
async def add_belief(request: Request, data: dict):
    user_id, engine, state = _get_user_brain(request)
    bg = state["belief_graph"]
    dedup = state["dedup"]

    statement = data.get("statement", "")
    tags = data.get("tags", [])
    confidence = float(data.get("confidence", 0.7))

    if not statement:
        return {"error": "statement is required"}

    # Dedup check
    if dedup:
        same_tag_beliefs = []
        for tag in tags:
            same_tag_beliefs.extend(bg.get_beliefs_by_tag(tag))
        if same_tag_beliefs:
            is_dup, match_id = await dedup.is_duplicate(statement, same_tag_beliefs)
            if is_dup and match_id:
                existing = bg.get_belief(match_id)
                if existing:
                    existing.confidence = max(existing.confidence, confidence)
                    return {
                        "action": "merged",
                        "belief_id": match_id,
                        "message": f"Merged with existing belief: {existing.statement[:60]}",
                    }

    cycle = engine.cycle_count
    b = Belief(
        id="",
        statement=statement,
        confidence=max(0.0, min(1.0, confidence)),
        source=BeliefSource.SEED,
        created_at=cycle,
        last_updated=cycle,
        last_verified=cycle,
        tags=tags,
    )
    bid = bg.add_belief(b)

    # Persist immediately
    scheduler = request.app.state.scheduler
    scheduler._save_brain_state(user_id)

    return {"action": "created", "belief_id": bid}


@router.put("/api/beliefs/{belief_id}")
async def update_belief(belief_id: str, request: Request, data: dict):
    user_id, engine, state = _get_user_brain(request)
    bg = state["belief_graph"]
    b = bg.get_belief(belief_id)
    if b is None:
        return {"error": "not found"}

    if "confidence" in data:
        b.confidence = max(0.0, min(1.0, float(data["confidence"])))
    if "statement" in data:
        b.statement = data["statement"]
    if "tags" in data:
        b.tags = data["tags"]
    if "status" in data:
        b.status = str(data["status"])

    return {"action": "updated", "belief_id": belief_id}


@router.delete("/api/beliefs/{belief_id}")
async def delete_belief(belief_id: str, request: Request):
    user_id, engine, state = _get_user_brain(request)
    bg = state["belief_graph"]
    if belief_id not in bg.graph:
        return {"error": "not found"}

    bg.graph.remove_node(belief_id)
    return {"action": "deleted", "belief_id": belief_id}
