"""Operator phones edited in /admin override the .env defaults at once; empty falls back."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

H = {"X-Requested-With": "toktok-admin", "X-Admin-Key": "test-admin-key"}


@pytest.fixture
def client(monkeypatch):
    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "",
                 "ADMIN_API_KEY": "test-admin-key", "OPERATOR_PHONE_DRIVER": "7000-0000",
                 "OPERATOR_PHONE_CLIENT": "7000-0001", "OPERATOR_PHONE_CUSTOMER": "7000-0002"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def test_defaults_then_override_then_fallback(client):
    s = client.app.state.container.settings
    r = client.get("/api/admin/settings/phones", headers=H)
    assert r.status_code == 200
    assert r.json()["phones"]["driver"] == {"value": "7000-0000", "saved": "", "default": "7000-0000"}

    r = client.put("/api/admin/settings/phones", headers=H, json={"driver": " 7711-2233 ", "client": "", "customer": ""})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phones"]["driver"]["value"] == "7711-2233"
    assert body["phones"]["client"]["value"] == "7000-0001"
    assert body["updated_by"] == "service:admin-key"
    assert s.operator_phone("driver") == "7711-2233"  # used by the bot and the handoff box right away

    audit = client.app.state.container.staff.audit_log[-1]
    assert audit["action"] == "settings.phones" and audit["detail"]["after"] == {"driver": "7711-2233"}

    client.put("/api/admin/settings/phones", headers=H, json={"driver": ""})
    assert s.operator_phone("driver") == "7000-0000"


def test_rejects_bad_numbers_and_non_admins(client):
    assert client.put("/api/admin/settings/phones", headers=H, json={"driver": "call me"}).status_code == 400
    assert client.put("/api/admin/settings/phones", headers={"X-Requested-With": "toktok-admin"},
                      json={"driver": "7711-2233"}).status_code == 401
