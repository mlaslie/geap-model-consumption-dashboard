"""
Authentication dependency for FastAPI endpoints.

When settings.AUTH_TOKEN is set, every request must supply:
    Authorization: Bearer <token>
The comparison is constant-time to prevent timing attacks.

When AUTH_TOKEN is empty the app runs in unauthenticated dev mode; a
prominent warning is emitted once at startup (see main.py lifespan).
"""
import secrets
import logging
from typing import Optional

from fastapi import Header, HTTPException

from backend.config import settings

logger = logging.getLogger(__name__)


def require_auth(authorization: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency — call via Depends(require_auth)."""
    if not settings.AUTH_TOKEN:
        # Dev mode: unauthenticated, warning already logged at startup.
        return

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    # Use constant-time comparison to prevent timing-based token leaks.
    if not secrets.compare_digest(token.encode(), settings.AUTH_TOKEN.encode()):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
