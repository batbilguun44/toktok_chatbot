"""Staff ID/password login: admin sets passwords, wrong passwords and brute force are refused."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.staff.passwords import hash_password, verify_password
from app.staff.store import StaffUser

H = {"X-Requested-With": "toktok-admin"}


@pytest.fixture
def client(monkeypatch):
    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "",
                 "PASSWORD_LOGIN": "true", "DEV_LOGIN": "false", "LOGIN_ATTEMPTS_PER_MINUTE": "5",
                 "STAFF_SESSION_SECRET": "x" * 40}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        staff = c.app.state.container.staff
        asyncio.run(staff.upsert(StaffUser(email="admin", roles=["admin"])))
        asyncio.run(staff.set_password_hash("admin", hash_password("correct horse battery")))
        yield c
    get_settings.cache_clear()


def login(c, login_id, password):
    return c.post("/api/auth/password-login", json={"login_id": login_id, "password": password})


def test_hash_roundtrip():
    h = hash_password("s3cret-password")
    assert h.startswith("scrypt$") and verify_password("s3cret-password", h)
    assert not verify_password("wrong-password", h) and not verify_password("x", None)
    assert hash_password("same-password") != hash_password("same-password")  # salted


def test_config_and_dev_login_disabled(client):
    cfg = client.get("/api/auth/config").json()
    assert cfg["password_login_enabled"] is True and cfg["dev_login_enabled"] is False
    assert client.post("/api/auth/dev-login", json={"email": "x@y.z", "roles": ["admin"]}).status_code == 404


def test_admin_logs_in_and_sets_operator_password(client):
    assert login(client, "admin", "wrong password").status_code == 401
    r = login(client, " Admin ", "correct horse battery")  # id is trimmed and case-insensitive
    assert r.status_code == 200 and r.json()["roles"] == ["admin"]
    assert client.get("/api/auth/me").json()["via"] == "password"

    assert client.put("/api/admin/staff/ops1", json={"roles": ["operator"]}, headers=H).status_code == 200
    assert client.put("/api/admin/staff/ops1/password", json={"password": "short"}, headers=H).status_code == 400
    assert client.put("/api/admin/staff/nobody/password", json={"password": "x" * 12}, headers=H).status_code == 404
    assert client.put("/api/admin/staff/ops1/password", json={"password": "operator-pass-1"},
                      headers=H).status_code == 200
    # Role changes keep the password.
    assert client.put("/api/admin/staff/ops1", json={"roles": ["operator"]}, headers=H).status_code == 200

    client.post("/api/auth/logout")
    assert login(client, "ops1", "operator-pass-1").status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["roles"] == ["operator"] and client.get("/api/admin/staff").status_code == 403

    # Deactivated accounts cannot sign in even with the right password.
    client.post("/api/auth/logout")
    login(client, "admin", "correct horse battery")
    client.put("/api/admin/staff/ops1", json={"roles": ["operator"], "active": False}, headers=H)
    client.post("/api/auth/logout")
    assert login(client, "ops1", "operator-pass-1").status_code == 401

    actions = [e["action"] for e in asyncio.run(client.app.state.container.staff.list_audit(50))]
    assert "staff.password_set" in actions and "login.denied" in actions


def test_unknown_user_and_rate_limit(client):
    assert login(client, "ghost", "whatever-pass").status_code == 401
    codes = [login(client, "admin", f"guess-{i}").status_code for i in range(6)]
    assert codes[-1] == 429
    assert login(client, "admin", "correct horse battery").status_code == 429  # locked for the window
