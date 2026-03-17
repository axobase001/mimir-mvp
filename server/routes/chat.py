import logging

from fastapi import APIRouter, Request, HTTPException

from ...llm.client import parse_json_response
from ...types import Belief, BeliefSource

router = APIRouter()
log = logging.getLogger(__name__)


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


async def _classify_intent(llm_client, user_msg: str) -> str:
    try:
        text = await llm_client.complete(
            "Classify the user message as either a 'query' (asking for information) "
            "or an 'action' (requesting to do something). "
            'Output only JSON: {"type": "query"} or {"type": "action"}',
            f"User message: {user_msg}",
            temperature=0.0,
        )
        data = parse_json_response(text)
        if isinstance(data, dict) and data.get("type") in ("query", "action"):
            return data["type"]
    except Exception:
        pass
    return "query"


async def _extract_tags(llm_client, user_msg: str) -> list[str]:
    try:
        text = await llm_client.complete(
            "Extract 1-3 keyword tags (English lowercase, underscore separated) from the user message. "
            'Only output a JSON array: ["tag1", "tag2"]',
            f"User message: {user_msg}",
            temperature=0.0,
        )
        tags = parse_json_response(text)
        return tags if isinstance(tags, list) else []
    except Exception:
        return []


def _find_relevant_beliefs(bg, tags: list[str], user_msg: str) -> list[Belief]:
    relevant: list[Belief] = []
    for tag in tags:
        for b in bg.get_beliefs_by_tag(tag):
            if b not in relevant:
                relevant.append(b)

    if not relevant:
        for b in bg.get_all_beliefs():
            if any(
                word in b.statement.lower()
                for word in user_msg.lower().split()
                if len(word) > 2
            ):
                relevant.append(b)
                if len(relevant) >= 10:
                    break

    return relevant


async def _live_search(state, user_msg: str, tags: list[str]) -> tuple[str, list[dict]]:
    """Immediately search + extract. Returns (raw_results, new_belief_dicts)."""
    external_llm = state["external_llm"]
    search_skill = state.get("search_skill")

    # Find search skill from registry or skills dict
    if search_skill is None:
        registry = state.get("skill_registry")
        if registry:
            search_skill = registry.get("brave_search")

    if search_skill is None:
        return "", []

    query = await external_llm.intent_to_query(user_msg)
    result = await search_skill.execute({"query": query})

    if not result.get("success"):
        return "", []

    raw = result.get("result", "")

    # Extract structured facts
    dummy_belief = Belief(
        id="chat_query",
        statement=user_msg,
        confidence=0.5,
        source=BeliefSource.SEED,
        created_at=0,
        last_updated=0,
        last_verified=0,
        tags=tags[:3] if tags else ["user_query"],
    )
    extraction = await external_llm.extract_beliefs(raw, dummy_belief)
    new_beliefs = extraction.get("new_beliefs", [])

    return raw, new_beliefs


@router.post("/api/chat")
async def chat(request: Request, message: dict):
    user_id, engine, state = _get_user_brain(request)

    bg = state["belief_graph"]
    llm_client = state["llm_client"]
    external_llm = state["external_llm"]

    user_msg = message.get("message", "")
    if not user_msg:
        return {"reply": "请输入消息。", "confidence": 0, "sources": [], "searching": False}

    # Step 0: Classify — query vs action
    intent_type = await _classify_intent(llm_client, user_msg)

    # Action requests → action engine
    if intent_type == "action" and engine.action_engine is not None:
        try:
            plan = await engine.action_engine.plan_action(
                intent=user_msg,
                goal=user_msg,
                belief_context="",
                sec_matrix=state.get("sec_matrix") or engine.sec,
                memory=state.get("memory") or engine.mem,
            )
            if plan.get("skill_name"):
                result = await engine.action_engine.execute_action(
                    plan, pe_before=0.0,
                )
                return {
                    "reply": result.summary if result.summary else str(result.result),
                    "confidence": 1.0 if result.success else 0.0,
                    "sources": [],
                    "searching": False,
                    "action": {
                        "skill": plan["skill_name"],
                        "success": result.success,
                        "error": result.error,
                    },
                }
        except Exception as e:
            log.warning("Action failed, falling back to query: %s", e)

    # Step 1: Extract tags
    tags = await _extract_tags(llm_client, user_msg)

    # Step 2: Find existing beliefs
    relevant = _find_relevant_beliefs(bg, tags, user_msg)

    # Step 3: Build beliefs context
    high_conf = sorted(
        [b for b in relevant if b.confidence > 0.2],
        key=lambda b: b.confidence,
        reverse=True,
    )[:10]

    beliefs_ctx = ""
    if high_conf:
        beliefs_ctx = "\n".join(
            f"- [{b.id}] (conf={b.confidence:.2f}) {b.statement}"
            for b in high_conf
        )

    # Step 4: ALWAYS search if beliefs are thin (< 3 high-conf) or no beliefs at all
    search_results = ""
    new_from_search: list[dict] = []
    needs_search = len(high_conf) < 3

    if needs_search:
        search_results, new_from_search = await _live_search(state, user_msg, tags)

        # Inject new beliefs into graph immediately
        for nb in new_from_search:
            new_b = Belief(
                id="",
                statement=nb["statement"],
                confidence=nb.get("confidence", 0.5),
                source=BeliefSource.OBSERVATION,
                created_at=engine.cycle_count,
                last_updated=engine.cycle_count,
                last_verified=engine.cycle_count,
                tags=nb.get("tags", tags[:3] if tags else ["user_query"]),
            )
            bg.add_belief(new_b)

    # Step 5: Generate answer using the dedicated chat method
    avg_conf = (
        sum(b.confidence for b in high_conf) / len(high_conf) if high_conf else 0.3
    )

    reply = await external_llm.chat_answer(
        question=user_msg,
        beliefs_context=beliefs_ctx,
        search_results=search_results,
    )

    return {
        "reply": reply,
        "confidence": round(avg_conf, 3),
        "sources": [b.id for b in high_conf[:5]],
        "searching": needs_search,
    }
