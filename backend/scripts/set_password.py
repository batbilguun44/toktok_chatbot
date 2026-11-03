"""Create a staff account or set its password (ID/password login, PASSWORD_LOGIN=true).

Use it for the first admin; after that, admins manage staff and passwords in /admin.

    cd backend
    python -m scripts.set_password admin --role admin        # prompts for the password
    STAFF_PASSWORD=... python -m scripts.set_password ops1 --role operator   # non-interactive

    # Docker:
    docker compose exec backend python -m scripts.set_password admin --role admin
"""

import argparse
import asyncio
import getpass
import os
import sys

from psycopg_pool import AsyncConnectionPool

from app.config import get_settings
from app.staff.passwords import hash_password, password_problem
from app.staff.store import ROLES, PostgresStaffStore, StaffUser


async def main(login_id: str, roles: list[str]) -> int:
    s = get_settings()
    if not s.database_url:
        print("DATABASE_URL is not set: passwords need persistent storage", file=sys.stderr)
        return 1
    password = os.environ.get("STAFF_PASSWORD") or getpass.getpass(f"Password for {login_id}: ")
    if not os.environ.get("STAFF_PASSWORD") and getpass.getpass("Repeat password: ") != password:
        print("passwords do not match", file=sys.stderr)
        return 1
    if problem := password_problem(password):
        print(problem, file=sys.stderr)
        return 1

    async with AsyncConnectionPool(s.database_url, min_size=1, max_size=1, open=False) as pool:
        async with pool.connection() as conn:  # the column exists once the backend has started
            await conn.execute("ALTER TABLE staff_users ADD COLUMN IF NOT EXISTS password_hash TEXT")
        store = PostgresStaffStore(pool)
        user = await store.get(login_id)
        if user:
            if roles:
                user.roles = roles
            user.active = True
        else:
            user = StaffUser(email=login_id, name=login_id.split("@")[0], roles=roles or ["operator"])
        await store.upsert(user)
        await store.set_password_hash(login_id, hash_password(password))
        await store.audit("cli:set_password", "staff.password_set", login_id, {"roles": user.roles})
    print(f"ok: {login_id} roles={user.roles}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("login_id", help="login ID (a name like 'admin' or an e-mail address)")
    ap.add_argument("--role", action="append", choices=ROLES, default=[], help="repeatable; default operator")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.login_id.strip().lower(), a.role)))
