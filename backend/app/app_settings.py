"""Small key/value settings the operation team edits in /admin (stored in Postgres when configured).

Currently: the operator phone numbers shown to users when a chat is handed to a person. A stored value
overrides the OPERATOR_PHONE_* defaults from .env; an empty value falls back to the default.
"""

import json
import re
from typing import Any, Protocol

PHONES_KEY = "operator_phones"
PHONE_PERSONAS = ("driver", "client", "customer")
# Digits with optional spaces, dashes, parentheses and a leading +; 3-30 characters.
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{1,28}[0-9]$")


class AppSettingsStore(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None:
        """{"value": ..., "updated_by": str | None, "updated_at": iso str | None} or None."""
        ...

    async def set(self, key: str, value: Any, actor: str) -> None: ...


class InMemoryAppSettingsStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        return self.rows.get(key)

    async def set(self, key: str, value: Any, actor: str) -> None:
        from datetime import datetime, timezone

        self.rows[key] = {"value": value, "updated_by": actor, "updated_at": datetime.now(timezone.utc).isoformat()}


class PostgresAppSettingsStore:
    def __init__(self, pool) -> None:
        self.pool = pool

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            r = await (await conn.execute(
                "SELECT value, updated_by, updated_at FROM app_settings WHERE key = %s", (key,)
            )).fetchone()
        if not r:
            return None
        return {"value": r[0], "updated_by": r[1], "updated_at": r[2].isoformat() if r[2] else None}

    async def set(self, key: str, value: Any, actor: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                  updated_at = now()
                """,
                (key, json.dumps(value, ensure_ascii=False), actor),
            )


async def load_phones(store: AppSettingsStore) -> dict[str, str]:
    row = await store.get(PHONES_KEY)
    value = (row or {}).get("value") or {}
    return {p: str(value[p]).strip() for p in PHONE_PERSONAS if isinstance(value.get(p), str) and value[p].strip()}
