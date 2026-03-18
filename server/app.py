import os
import time
import logging
from collections import defaultdict

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .routes import dashboard, chat, beliefs, goals, ws, tools
from .routes import onboarding
from .auth.routes import router as auth_router
from .auth.jwt import verify_token

log = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Skuld", version="0.2.0")

# ── CORS ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth Middleware ──────────────────────────────────

# Paths that don't require authentication
_PUBLIC_PREFIXES = ("/api/auth/", "/ws", "/static", "/docs", "/openapi.json")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # ── DEV MODE: bypass auth, inject default user ──
        # But if a valid Bearer token is present, respect it (multi-user support)
        _dev_uid = getattr(request.app.state, "_dev_user_id", None)
        if _dev_uid:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token_uid = verify_token(auth_header[7:])
                if token_uid and token_uid != _dev_uid:
                    request.state.user_id = token_uid
                    return await call_next(request)
            request.state.user_id = _dev_uid
            return await call_next(request)

        # Allow public paths
        if path == "/" or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            if path in ("/api/auth/refresh", "/api/auth/me"):
                self._try_extract_user(request)
            return await call_next(request)

        # Also allow onboarding.html
        if path == "/onboarding.html" or path.endswith(".html"):
            return await call_next(request)

        # Require auth for all other /api/ paths
        if path.startswith("/api/"):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                )
            token = auth_header[7:]
            user_id = verify_token(token)
            if user_id is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or expired token"},
                )
            request.state.user_id = user_id

        return await call_next(request)

    def _try_extract_user(self, request: Request) -> None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            user_id = verify_token(token)
            if user_id:
                request.state.user_id = user_id


# ── Rate Limit Middleware ────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-IP rate limiter: 60 requests per minute."""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Only rate-limit API calls
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Clean old entries
        self._requests[client_ip] = [
            t for t in self._requests[client_ip]
            if now - t < self.window
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        self._requests[client_ip].append(now)
        return await call_next(request)


# Add middlewares (order matters: rate limit first, then auth)
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)

# ── Routes ───────────────────────────────────────────

app.include_router(auth_router)
app.include_router(onboarding.router)
app.include_router(dashboard.router)
app.include_router(chat.router)
app.include_router(beliefs.router)
app.include_router(goals.router)
app.include_router(tools.router)
app.include_router(ws.router)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/onboarding.html")
async def onboarding_page():
    return FileResponse(os.path.join(_STATIC_DIR, "onboarding.html"))
