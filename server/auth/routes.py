"""Authentication routes: register, login, refresh, me."""

import logging

from fastapi import APIRouter, Request, HTTPException

from .jwt import create_token, verify_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
async def register(request: Request, data: dict):
    """Register a new user. Returns token immediately."""
    user_db = request.app.state.user_db

    email = data.get("email", "").strip()
    password = data.get("password", "")
    display_name = data.get("display_name", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check max users
    max_users = getattr(request.app.state, "max_users", 1000)
    if user_db.get_user_count() >= max_users:
        raise HTTPException(status_code=403, detail="Maximum user limit reached")

    try:
        user = user_db.create_user(
            email=email,
            password=password,
            display_name=display_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    token = create_token(user["id"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "plan": user["plan"],
        },
    }


@router.post("/login")
async def login(request: Request, data: dict):
    """Authenticate and return token."""
    user_db = request.app.state.user_db

    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    user = user_db.authenticate(email, password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user["id"])
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "plan": user["plan"],
        },
    }


@router.post("/refresh")
async def refresh(request: Request):
    """Refresh token. Requires valid current token."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_db = request.app.state.user_db
    user = user_db.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    token = create_token(user_id)
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "plan": user["plan"],
        },
    }


@router.get("/me")
async def me(request: Request):
    """Get current user info."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_db = request.app.state.user_db
    user = user_db.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    # Check if brain exists
    brain_store = request.app.state.brain_store
    has_brain = brain_store.brain_exists(user_id)

    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "plan": user["plan"],
        "cycles_today": user["cycles_today"],
        "cycles_limit": user["cycles_limit"],
        "beliefs_count": user["beliefs_count"],
        "beliefs_limit": user["beliefs_limit"],
        "has_brain": has_brain,
        "created_at": user["created_at"],
    }
