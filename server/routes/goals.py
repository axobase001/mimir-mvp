from fastapi import APIRouter, Request, HTTPException

from ...types import Goal, GoalOrigin, GoalStatus

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


@router.get("/api/goals")
async def list_goals(request: Request):
    user_id, engine, state = _get_user_brain(request)
    gg = state["goal_generator"]
    return {
        "goals": [
            {
                "id": g.id,
                "description": g.description,
                "reason": g.reason,
                "priority": round(g.priority, 4),
                "status": g.status.value,
                "target_belief_id": g.target_belief_id,
                "created_at": g.created_at,
            }
            for g in gg.goals.values()
        ]
    }


@router.post("/api/goals")
async def add_goal(request: Request, data: dict):
    user_id, engine, state = _get_user_brain(request)
    gg = state["goal_generator"]
    desc = data.get("description", "")
    if not desc:
        return {"error": "description required"}

    target = data.get("target_belief_id", "")
    priority = float(data.get("priority", 0.5))

    goal = Goal(
        id=gg._next_id(),
        target_belief_id=target,
        description=desc,
        reason="User requested",
        status=GoalStatus.ACTIVE,
        created_at=engine.cycle_count,
        priority=priority,
        origin=GoalOrigin.EXOGENOUS,
    )
    gg.goals[goal.id] = goal
    return {"action": "created", "goal_id": goal.id}


@router.put("/api/goals/{goal_id}/complete")
async def complete_goal(goal_id: str, request: Request):
    user_id, engine, state = _get_user_brain(request)
    gg = state["goal_generator"]
    if goal_id not in gg.goals:
        return {"error": "not found"}
    gg.complete_goal(goal_id)
    return {"action": "completed", "goal_id": goal_id}


@router.put("/api/goals/{goal_id}/abandon")
async def abandon_goal(goal_id: str, request: Request):
    user_id, engine, state = _get_user_brain(request)
    gg = state["goal_generator"]
    if goal_id not in gg.goals:
        return {"error": "not found"}
    gg.abandon_goal(goal_id, "User abandoned")
    return {"action": "abandoned", "goal_id": goal_id}
