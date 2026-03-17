"""JWT token creation and verification using PyJWT."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

log = logging.getLogger(__name__)

# Module-level config — set by app startup
_secret: str = ""
_expire_hours: int = 72
_algorithm: str = "HS256"


def configure(secret: str, expire_hours: int = 72) -> None:
    """Configure JWT module. Must be called before use."""
    global _secret, _expire_hours
    _secret = secret
    _expire_hours = expire_hours


def create_token(user_id: str) -> str:
    """Create a JWT token for the given user_id."""
    if not _secret:
        raise RuntimeError("JWT secret not configured — call configure() first")
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=_expire_hours),
    }
    return jwt.encode(payload, _secret, algorithm=_algorithm)


def verify_token(token: str) -> Optional[str]:
    """Verify a JWT token. Returns user_id or None if invalid/expired."""
    if not _secret:
        return None
    try:
        payload = jwt.decode(token, _secret, algorithms=[_algorithm])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        log.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        log.debug("Invalid token: %s", e)
        return None
