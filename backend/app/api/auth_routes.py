"""Staff login (OIDC SSO, dev login), logout, current principal, staff admin, audit log."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import container, current_staff, require
from app.container import Container
from app.staff.auth import LOGIN_COOKIE, SESSION_COOKIE, AuthError, Principal, email_from_claims
from app.staff.store import ROLES, StaffUser, permissions_for

router = APIRouter(prefix="/api/auth")
staff_admin = APIRouter(prefix="/api/admin")


def _set_cookie(resp, c: Container, name: str, value: str, max_age: int, path: str = "/") -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax", path=path,
                    secure=c.settings.cookie_secure or c.settings.env == "prod")


def _safe_return(path: str | None) -> str:
    # Only relative paths on the frontend: never an open redirect.
    return path if path and path.startswith("/") and not path.startswith("//") else "/admin"


async def _issue_session(c: Container, user: StaffUser, via: str, return_to: str | None, redirect: bool):
    token = c.signer.sign({"sub": user.email, "via": via}, c.settings.staff_session_hours * 3600, "session")
    body = {"email": user.email, "roles": user.roles}
    resp = (RedirectResponse(c.settings.frontend_base_url + _safe_return(return_to), status_code=302)
            if redirect else JSONResponse(body))
    _set_cookie(resp, c, SESSION_COOKIE, token, c.settings.staff_session_hours * 3600)
    await c.staff.touch_login(user.email, user.name)
    await c.staff.audit(user.email, "login", None, {"via": via})
    return resp


@router.get("/config")
async def auth_config(c: Container = Depends(container)):
    return {"sso_enabled": c.settings.sso_enabled, "dev_login_enabled": c.settings.dev_login_enabled, "roles": ROLES}


@router.get("/login")
async def login(return_to: str | None = None, c: Container = Depends(container)):
    if not c.oidc:
        raise HTTPException(404, "SSO is not configured")
    url, state = await c.oidc.start()
    resp = RedirectResponse(url, status_code=302)
    _set_cookie(resp, c, LOGIN_COOKIE, c.signer.sign({**state, "rt": _safe_return(return_to)}, 600, "login"),
                600, path="/api/auth")
    return resp


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None,
                   error: str | None = None, c: Container = Depends(container)):
    fail = c.settings.frontend_base_url + "/admin?login_error="
    if error or not code or not state or not c.oidc:
        return RedirectResponse(fail + (error or "invalid_request"), status_code=302)
    try:
        login_state = c.signer.verify(request.cookies.get(LOGIN_COOKIE, ""), "login")
        if login_state["state"] != state:
            raise AuthError("state mismatch")
        claims = await c.oidc.finish(code, login_state)
    except AuthError:
        return RedirectResponse(fail + "verification_failed", status_code=302)

    email = email_from_claims(claims)
    domains = [d.lower() for d in c.settings.oidc_allowed_domains]
    if not email or (domains and email.split("@")[1] not in domains):
        return RedirectResponse(fail + "domain_not_allowed", status_code=302)
    user = await c.staff.get(email)
    if not user and email in [e.lower() for e in c.settings.staff_bootstrap_admins]:
        user = StaffUser(email=email, name=claims.get("name"), roles=["admin"])
        await c.staff.upsert(user)
        await c.staff.audit(email, "staff.bootstrap_admin", email)
    if not user or not user.active:
        await c.staff.audit(email, "login.denied", None, {"reason": "not_registered" if not user else "inactive"})
        return RedirectResponse(fail + "not_registered", status_code=302)
    resp = await _issue_session(c, user, "sso", login_state.get("rt"), redirect=True)
    resp.delete_cookie(LOGIN_COOKIE, path="/api/auth")
    return resp


class DevLoginIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    roles: list[Literal["operator", "admin"]] = ["admin"]


@router.post("/dev-login")
async def dev_login(body: DevLoginIn, c: Container = Depends(container)):
    """Local development only: sign in as any staff member without an IdP."""
    if not c.settings.dev_login_enabled:
        raise HTTPException(404)
    user = StaffUser(email=body.email.lower(), name=body.email.split("@")[0], roles=list(body.roles))
    await c.staff.upsert(user)
    return await _issue_session(c, user, "dev", None, redirect=False)


@router.post("/logout")
async def logout(c: Container = Depends(container)):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/me")
async def me(p: Principal = Depends(current_staff)):
    return {"email": p.email, "roles": p.roles, "permissions": sorted(p.permissions), "via": p.via}


# ------------------------------------------------------------ staff admin
class StaffIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    roles: list[Literal["operator", "admin"]]
    active: bool = True


@staff_admin.get("/staff")
async def list_staff(_: Principal = Depends(require("staff.manage")), c: Container = Depends(container)):
    return [u.__dict__ | {"permissions": sorted(permissions_for(u.roles))} for u in await c.staff.list_all()]


@staff_admin.put("/staff/{email}")
async def upsert_staff(email: str, body: StaffIn, p: Principal = Depends(require("staff.manage")),
                       c: Container = Depends(container)):
    email = email.lower()
    if email == p.email and ("admin" not in body.roles or not body.active):
        raise HTTPException(400, "you cannot remove your own admin access")
    before = await c.staff.get(email)
    await c.staff.upsert(StaffUser(email=email, name=body.name, roles=list(body.roles), active=body.active))
    await c.staff.audit(p.email, "staff.upsert", email, {
        "before": before.__dict__ if before else None, "roles": body.roles, "active": body.active})
    return {"ok": True}


@staff_admin.get("/audit")
async def audit_log(limit: int = 200, _: Principal = Depends(require("audit.read")),
                    c: Container = Depends(container)):
    return await c.staff.list_audit(min(limit, 1000))
