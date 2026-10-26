"""Staff accounts, roles and the audit log."""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

ROLES = ("operator", "admin")

# role -> permissions. A user may hold several roles.
# Operators answer handed-off chats and draft knowledge / bot-instruction changes;
# an admin approves and publishes them (an admin may also publish their own change).
PERMISSIONS: dict[str, set[str]] = {
    "operator": {"handoff", "kb.read", "kb.edit", "conversations.read"},
    "admin": {
        "kb.read", "kb.edit", "kb.publish", "metrics.read", "insights.read", "insights.run",
        "handoff", "staff.manage", "audit.read", "conversations.read", "conversations.export",
    },
}


def permissions_for(roles: list[str]) -> set[str]:
    out: set[str] = set()
    for r in roles:
        out |= PERMISSIONS.get(r, set())
    return out


@dataclass
class StaffUser:
    email: str
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    active: bool = True
    last_login_at: str | None = None


class StaffStore(Protocol):
    async def get(self, email: str) -> StaffUser | None: ...
    async def list_all(self) -> list[StaffUser]: ...
    async def upsert(self, user: StaffUser) -> None: ...
    async def touch_login(self, email: str, name: str | None) -> None: ...
    async def audit(self, actor: str, action: str, target: str | None, detail: dict[str, Any] | None = None) -> None: ...
    async def list_audit(self, limit: int, actions: list[str] | None = None) -> list[dict[str, Any]]:
        """Newest first; `actions` keeps only those action names."""
        ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InMemoryStaffStore:
    def __init__(self) -> None:
        self.users: dict[str, StaffUser] = {}
        self.audit_log: list[dict[str, Any]] = []

    async def get(self, email: str) -> StaffUser | None:
        return self.users.get(email.lower())

    async def list_all(self) -> list[StaffUser]:
        return sorted(self.users.values(), key=lambda u: u.email)

    async def upsert(self, user: StaffUser) -> None:
        user.email = user.email.lower()
        self.users[user.email] = user

    async def touch_login(self, email: str, name: str | None) -> None:
        u = self.users.get(email.lower())
        if u:
            u.last_login_at = _now()
            u.name = u.name or name

    async def audit(self, actor, action, target, detail=None) -> None:
        self.audit_log.append({"id": len(self.audit_log) + 1, "actor": actor, "action": action, "target": target,
                               "detail": detail or {}, "created_at": _now(), "_ts": time.time()})

    async def list_audit(self, limit: int, actions: list[str] | None = None) -> list[dict[str, Any]]:
        rows = [e for e in reversed(self.audit_log) if not actions or e["action"] in actions]
        return [{k: v for k, v in e.items() if k != "_ts"} for e in rows][:limit]


class PostgresStaffStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get(self, email: str) -> StaffUser | None:
        async with self.pool.connection() as conn:
            r = await (await conn.execute(
                "SELECT email, name, roles, active, last_login_at FROM staff_users WHERE email = %s", (email.lower(),)
            )).fetchone()
        return _user(r) if r else None

    async def list_all(self) -> list[StaffUser]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT email, name, roles, active, last_login_at FROM staff_users ORDER BY email"
            )).fetchall()
        return [_user(r) for r in rows]

    async def upsert(self, user: StaffUser) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO staff_users (email, name, roles, active) VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name, roles = EXCLUDED.roles,
                  active = EXCLUDED.active
                """,
                (user.email.lower(), user.name, json.dumps(user.roles), user.active),
            )

    async def touch_login(self, email: str, name: str | None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE staff_users SET last_login_at = now(), name = COALESCE(name, %s) WHERE email = %s",
                (name, email.lower()),
            )

    async def audit(self, actor, action, target, detail=None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s, %s, %s, %s)",
                (actor, action, target, json.dumps(detail or {}, ensure_ascii=False, default=str)),
            )

    async def list_audit(self, limit: int, actions: list[str] | None = None) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id, actor, action, target, detail, created_at FROM audit_log "
                "WHERE %s::text[] IS NULL OR action = ANY(%s) ORDER BY id DESC LIMIT %s",
                (actions, actions, limit),
            )).fetchall()
        return [{"id": r[0], "actor": r[1], "action": r[2], "target": r[3], "detail": r[4],
                 "created_at": r[5].isoformat()} for r in rows]


def _user(r) -> StaffUser:
    return StaffUser(email=r[0], name=r[1], roles=list(r[2] or []), active=r[3],
                     last_login_at=r[4].isoformat() if r[4] else None)
