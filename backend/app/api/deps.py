"""Shared FastAPI dependencies: app container, end-user auth, staff auth."""

import secrets
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request

from app.container import Container
from app.domain import UserContext
from app.staff.auth import SESSION_COOKIE, AuthError, Principal
from app.staff.store import permissions_for

# Mutating staff requests must carry this header. A custom header forces a CORS
# preflight, so another site cannot forge a cookie-authenticated request.
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "toktok-admin"


def container(request: Request) -> Container:
    return request.app.state.container


async def current_user(
    authorization: str | None = Header(default=None), c: Container = Depends(container)
) -> UserContext:
    """End user (driver / merchant / customer), from the TokTok app token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    user = await c.services.gateway.resolve_user(authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(401, "invalid token")
    return user


async def current_staff(request: Request, c: Container = Depends(container)) -> Principal:
    key = request.headers.get("x-admin-key")
    if key:
        if c.settings.admin_api_key and secrets.compare_digest(key, c.settings.admin_api_key):
            return Principal("service:admin-key", ["admin"], permissions_for(["admin"]), via="admin-key")
        raise HTTPException(401, "invalid admin key")
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "login required")
    try:
        claims = c.signer.verify(token, "session")
    except AuthError:
        raise HTTPException(401, "session expired")
    user = await c.staff.get(claims["sub"])
    if not user or not user.active:
        raise HTTPException(403, "account disabled")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, "missing X-Requested-With header")
    return Principal(user.email, user.roles, permissions_for(user.roles), via=claims.get("via", "sso"))


def require(perm: str) -> Callable:
    async def dep(p: Principal = Depends(current_staff)) -> Principal:
        if not p.can(perm):
            raise HTTPException(403, f"permission '{perm}' required")
        return p

    return dep
