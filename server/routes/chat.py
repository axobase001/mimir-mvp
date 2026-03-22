import logging
import uuid

from fastapi import APIRouter, Request, HTTPException

from ...llm.client import parse_json_response
from ...dtypes import Belief, BeliefCategory, BeliefSource, PEType, TypedPE

router = APIRouter()
log = logging.getLogger(__name__)

# In-memory store for feedback tokens (maps token -> cycle info)
_feedback_tokens: dict[str, dict] = {}


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
            caller="classify_intent",
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
            caller="extract_tags",
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
            search_skill = registry.get("web_search") or registry.get("brave_search")

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


_NEGATIVE_FEEDBACK_WORDS = {
    "不好", "太模糊", "wrong", "错了", "不对", "不准确", "差", "糟糕",
    "bad", "incorrect", "inaccurate", "vague", "useless", "no",
}


def _is_negative_feedback(text: str) -> bool:
    """Check if user message contains negative feedback keywords."""
    text_lower = text.lower().strip()
    return any(word in text_lower for word in _NEGATIVE_FEEDBACK_WORDS)


@router.post("/api/chat")
async def chat(request: Request, message: dict):
    user_id, engine, state = _get_user_brain(request)

    bg = state["belief_graph"]
    llm_client = state["llm_client"]
    external_llm = state["external_llm"]

    user_msg = message.get("message", "")
    if not user_msg:
        return {"reply": "请输入消息。", "confidence": 0, "sources": [], "searching": False}

    # Check for negative feedback on previous response
    feedback_token = message.get("feedback_token")
    if feedback_token and feedback_token in _feedback_tokens and _is_negative_feedback(user_msg):
        token_info = _feedback_tokens.pop(feedback_token)
        # Generate INTERACTION PE
        pe_engine = state.get("prediction_engine") or engine.pe_engine
        interaction_pe = pe_engine.compute_interaction_pe(
            expected=0.0, actual=0.7,
            cycle=engine.cycle_count,
            source_id=f"chat_feedback_{feedback_token[:8]}",
        )
        log.info("Negative feedback received, INTERACTION PE=%.3f", interaction_pe.value)

    # Step 0: Classify — query vs action
    intent_type = await _classify_intent(llm_client, user_msg)

    # Action requests → multi-step action engine
    if intent_type == "action" and engine.action_engine is not None:
        try:
            # Build belief context for the action
            all_beliefs = bg.get_all_beliefs()
            belief_ctx = "\n".join(
                f"- ({b.confidence:.2f}) {b.statement}"
                for b in sorted(all_beliefs, key=lambda x: x.confidence, reverse=True)[:10]
            )

            # Plan multi-step
            steps = await engine.action_engine.plan_multistep(
                intent=user_msg,
                belief_context=belief_ctx,
                sec_matrix=state.get("sec_matrix") or engine.sec,
                memory=state.get("memory") or engine.mem,
            )

            if steps:
                result = await engine.action_engine.execute_plan(
                    steps=steps,
                    intent=user_msg,
                    belief_context=belief_ctx,
                    pe_before=0.0,
                )

                # Generate human-readable reply from the execution results
                reply = result.get("details", result.get("summary", ""))
                if result.get("accumulated_output"):
                    try:
                        reply = await external_llm.chat_answer(
                            question=user_msg,
                            beliefs_context="",
                            search_results=result["accumulated_output"][-2000:],
                        )
                    except Exception:
                        pass

                return {
                    "reply": reply,
                    "confidence": 1.0 if result["success"] else 0.5,
                    "sources": [],
                    "searching": False,
                    "action": {
                        "steps": len(steps),
                        "success": result["success"],
                        "summary": result["summary"],
                        "artifacts": result.get("artifacts", []),
                    },
                }
        except Exception as e:
            log.warning("Multi-step action failed, falling back to query: %s", e)

    # Use fast_path for query processing
    fast_result = await engine.run_fast_path(user_msg)

    # Generate feedback token for this response
    new_feedback_token = str(uuid.uuid4())
    _feedback_tokens[new_feedback_token] = {
        "cycle": engine.cycle_count,
        "query": user_msg[:100],
    }
    # Cap stored tokens to prevent memory leak
    if len(_feedback_tokens) > 100:
        oldest_keys = list(_feedback_tokens.keys())[:50]
        for k in oldest_keys:
            _feedback_tokens.pop(k, None)

    return {
        "reply": fast_result["answer"],
        "confidence": 0.8 if fast_result["beliefs_used"] else 0.5,
        "sources": fast_result["beliefs_used"],
        "searching": fast_result["searched"],
        "feedback_token": new_feedback_token,
    }
