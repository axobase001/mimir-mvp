"""API routes for tool management — list built-in + custom tools, CRUD custom tools."""

from fastapi import APIRouter, Request, HTTPException

from ...skills.custom_tool import CustomToolManager

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


@router.get("/api/tools")
async def list_tools(request: Request):
    """List all tools (built-in + custom)."""
    user_id, engine, state = _get_user_brain(request)
    registry = state["skill_registry"]

    tools = registry.discover()
    return {"tools": tools}


@router.post("/api/tools/custom")
async def create_custom_tool(request: Request, data: dict):
    """Create a new custom tool from JSON definition."""
    user_id, engine, state = _get_user_brain(request)

    custom_mgr: CustomToolManager = state.get("custom_tool_manager")
    if custom_mgr is None:
        return {"error": "Custom tool manager not available"}

    try:
        name = custom_mgr.register_tool(data)
    except ValueError as e:
        return {"error": str(e)}

    # Also register into the live skill registry
    registry = state["skill_registry"]
    skill = custom_mgr.get_skill(name)
    if skill is not None:
        registry.register(skill)

    return {"action": "created", "tool_name": f"custom:{name}"}


@router.delete("/api/tools/custom/{name}")
async def delete_custom_tool(name: str, request: Request):
    """Delete a custom tool by name."""
    user_id, engine, state = _get_user_brain(request)

    custom_mgr: CustomToolManager = state.get("custom_tool_manager")
    if custom_mgr is None:
        return {"error": "Custom tool manager not available"}

    removed = custom_mgr.remove_tool(name)
    if not removed:
        return {"error": f"Custom tool '{name}' not found"}

    return {"action": "deleted", "tool_name": name}
