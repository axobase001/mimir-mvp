"""Onboarding routes: init brain, reset brain, get templates."""

import logging

from fastapi import APIRouter, Request, HTTPException

from ...dtypes import Belief, BeliefSource

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

TEMPLATES = {
    "financial_analyst": {
        "name": "Financial Analyst",
        "description": "Track markets, macro trends, and investment theses",
        "seed_beliefs": [
            {"statement": "The Federal Reserve's interest rate decisions significantly impact equity valuations", "confidence": 0.8, "tags": ["macro", "fed", "equities"]},
            {"statement": "Inflation expectations drive bond yield movements", "confidence": 0.75, "tags": ["macro", "inflation", "bonds"]},
            {"statement": "Corporate earnings growth is the primary long-term driver of stock prices", "confidence": 0.85, "tags": ["equities", "fundamentals"]},
            {"statement": "Currency fluctuations affect multinational corporate profitability", "confidence": 0.7, "tags": ["forex", "equities"]},
            {"statement": "Market sentiment indicators can signal short-term reversals", "confidence": 0.6, "tags": ["sentiment", "technicals"]},
        ],
    },
    "developer": {
        "name": "Developer",
        "description": "Track tech ecosystem, frameworks, and best practices",
        "seed_beliefs": [
            {"statement": "Rust adoption is growing in systems programming due to memory safety guarantees", "confidence": 0.8, "tags": ["rust", "systems", "languages"]},
            {"statement": "Large Language Models are reshaping software development workflows", "confidence": 0.85, "tags": ["ai", "llm", "devtools"]},
            {"statement": "Microservices architecture improves scalability but increases operational complexity", "confidence": 0.75, "tags": ["architecture", "microservices"]},
            {"statement": "WebAssembly will expand the scope of web applications significantly", "confidence": 0.65, "tags": ["wasm", "web", "performance"]},
            {"statement": "Type safety reduces production bugs in large codebases", "confidence": 0.8, "tags": ["typescript", "types", "quality"]},
        ],
    },
    "researcher": {
        "name": "Researcher",
        "description": "Track academic fields, papers, and scientific developments",
        "seed_beliefs": [
            {"statement": "Transformer architectures remain the dominant paradigm in deep learning", "confidence": 0.85, "tags": ["ai", "transformers", "deep_learning"]},
            {"statement": "Reproducibility crisis affects multiple scientific disciplines", "confidence": 0.8, "tags": ["science", "reproducibility", "methodology"]},
            {"statement": "Interdisciplinary research produces more impactful discoveries", "confidence": 0.7, "tags": ["research", "interdisciplinary"]},
            {"statement": "Open access publishing is accelerating scientific progress", "confidence": 0.75, "tags": ["publishing", "open_access"]},
            {"statement": "Computational methods are becoming essential in all research fields", "confidence": 0.8, "tags": ["computation", "methodology"]},
        ],
    },
    "entrepreneur": {
        "name": "Entrepreneur",
        "description": "Track market opportunities, competition, and growth strategies",
        "seed_beliefs": [
            {"statement": "Product-market fit is the most critical factor for startup success", "confidence": 0.85, "tags": ["startup", "product", "strategy"]},
            {"statement": "Customer acquisition cost must be lower than lifetime value for sustainable growth", "confidence": 0.9, "tags": ["growth", "unit_economics"]},
            {"statement": "AI-native products will disrupt traditional SaaS in many verticals", "confidence": 0.7, "tags": ["ai", "saas", "disruption"]},
            {"statement": "Network effects create the strongest competitive moats", "confidence": 0.8, "tags": ["moat", "network_effects"]},
            {"statement": "Remote-first companies can access broader talent pools at lower cost", "confidence": 0.75, "tags": ["remote", "talent", "operations"]},
        ],
    },
    "custom": {
        "name": "Custom",
        "description": "Start with your own seed beliefs",
        "seed_beliefs": [],
    },
}


@router.get("/templates")
async def get_templates(request: Request):
    """Return available onboarding templates."""
    return {
        "templates": {
            key: {
                "name": t["name"],
                "description": t["description"],
                "belief_count": len(t["seed_beliefs"]),
                "seed_beliefs": t["seed_beliefs"],
            }
            for key, t in TEMPLATES.items()
        }
    }


@router.post("/init")
async def init_brain(request: Request, data: dict):
    """Initialize a Brain for the current user."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    scheduler = request.app.state.scheduler
    brain_store = request.app.state.brain_store
    user_db = request.app.state.user_db

    # Check if brain already exists
    if brain_store.brain_exists(user_id):
        raise HTTPException(status_code=409, detail="Brain already initialized. Use /api/onboarding/reset to reset.")

    # Get seed beliefs from template or custom
    template_key = data.get("template", "custom")
    if template_key in TEMPLATES and template_key != "custom":
        seed_beliefs = TEMPLATES[template_key]["seed_beliefs"]
    else:
        seed_beliefs = data.get("seed_beliefs", [])

    # Validate seed beliefs
    if not seed_beliefs:
        raise HTTPException(status_code=400, detail="At least one seed belief is required")

    for i, sb in enumerate(seed_beliefs):
        if not sb.get("statement"):
            raise HTTPException(
                status_code=400,
                detail=f"Seed belief {i} missing 'statement'"
            )

    # Handle optional API keys
    llm_api_key = data.get("llm_api_key", "")
    brave_api_key = data.get("brave_api_key", "")
    if llm_api_key or brave_api_key:
        user_db.update_api_keys(
            user_id,
            llm_api_key=llm_api_key or None,
            brave_api_key=brave_api_key or None,
        )

    # Start brain via scheduler
    try:
        await scheduler.start_brain(user_id, seed_beliefs)
    except Exception as e:
        log.error("Failed to start brain for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to initialize brain: {e}")

    return {
        "status": "initialized",
        "user_id": user_id,
        "belief_count": len(seed_beliefs),
        "template": template_key,
    }


@router.post("/reset")
async def reset_brain(request: Request, data: dict):
    """Reset a user's Brain. Requires confirm: true."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not data.get("confirm", False):
        raise HTTPException(status_code=400, detail="Must confirm reset with confirm: true")

    scheduler = request.app.state.scheduler
    brain_store = request.app.state.brain_store

    # Stop the brain if running
    await scheduler.stop_brain(user_id)

    # Delete brain state
    brain_store.delete_brain(user_id)

    return {"status": "reset", "user_id": user_id}
