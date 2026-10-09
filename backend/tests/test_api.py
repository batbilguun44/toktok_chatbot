"""End-to-end through FastAPI + LangGraph with the offline provider."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "ADMIN_API_KEY": "test-admin",
                 "EMBEDDING_PROVIDER": "none", "LANGFUSE_PUBLIC_KEY": "", "RATE_LIMIT_PER_MINUTE": "5"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def chat(client, token, message, session_id=None):
    r = client.post("/api/chat/stream", json={"message": message, "session_id": session_id},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    events = []
    for block in r.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append({"type": lines["event"], "data": json.loads(lines["data"])})
    return events


def test_chat_stream_session_feedback_and_metrics(client):
    ev = chat(client, "demo-customer", "zahialga mini haana yavj bn")
    sid = ev[0]["data"]["session_id"]
    turn_id = ev[0]["data"]["turn_id"]
    types = [e["type"] for e in ev]
    assert types[0] == "meta" and types[-1] == "done"
    assert any(e["type"] == "tool" and e["data"]["name"] == "get_order_status" for e in ev)
    assert "O-24" in "".join(e["data"]["delta"] for e in ev if e["type"] == "text")

    ev2 = chat(client, "demo-customer", "operatortoi holbogdmoor baina", session_id=sid)
    assert ev2[0]["data"]["session_id"] == sid
    esc = [e for e in ev2 if e["type"] == "escalation"]
    assert esc and esc[0]["data"]["phone"]

    r = client.post("/api/feedback", json={"turn_id": turn_id, "rating": 1},
                    headers={"Authorization": "Bearer demo-customer"})
    assert r.json() == {"ok": True}

    m = client.get("/api/admin/metrics", headers={"X-Admin-Key": "test-admin"}).json()
    assert m["conversations"] == 1 and m["turns"] == 2 and m["satisfaction"] == 1.0
    assert m["escalated_conversations"] == 1 and m["bot_resolution_rate"] == 0.0
    assert client.get("/api/admin/metrics").status_code == 401


def test_session_cannot_be_reused_by_another_user(client):
    sid = chat(client, "demo-customer", "сайн байна уу")[0]["data"]["session_id"]
    r = client.post("/api/chat/stream", json={"message": "hi", "session_id": sid},
                    headers={"Authorization": "Bearer demo-driver"})
    assert r.status_code == 403


def test_auth_and_rate_limit(client):
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401
    assert client.post("/api/chat/stream", json={"message": "hi"},
                       headers={"Authorization": "Bearer nope"}).status_code == 401
    for _ in range(5):
        chat(client, "demo-driver", "сайн уу")
    ev = chat(client, "demo-driver", "сайн уу")
    assert ev[0]["type"] == "error" and ev[0]["data"]["code"] == "rate_limited"


def test_operation_team_can_update_knowledge(client):
    h = {"X-Admin-Key": "test-admin"}
    body = {"persona": "driver", "title": "Өвлийн улиралд ажиллах заавар",
            "body": "## Гулгаа\nӨвлийн улиралд мөстэй зам дээр хурдаа 30 км/ц-аас хэтрүүлэхгүй."}
    r = client.put("/api/admin/knowledge/driver-winter", json=body, headers=h)
    assert r.status_code == 200 and r.json()["index"]["docs"] == 17
    hits = client.post("/api/admin/knowledge/search", json={"query": "mostei zam hurd", "persona": "driver"},
                       headers=h).json()
    assert hits[0]["doc_id"] == "driver-winter"
    docs = client.get("/api/admin/knowledge", headers=h).json()
    assert any(d["id"] == "driver-ops-internal" and not d["indexed"] for d in docs)
    assert client.delete("/api/admin/knowledge/driver-winter", headers=h).json()["index"]["docs"] == 16
    assert client.delete("/api/admin/knowledge/driver-fees", headers=h).status_code == 404  # file-based
