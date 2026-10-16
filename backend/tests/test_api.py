"""End-to-end through FastAPI + LangGraph with the offline provider."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "", "ADMIN_API_KEY": "test-admin",
                 "EMBEDDING_PROVIDER": "none", "LANGFUSE_PUBLIC_KEY": "", "RATE_LIMIT_PER_MINUTE": "5",
                 "STAFF_SESSION_SECRET": "s" * 40, "JUDGE_SAMPLE_RATE": "0"}.items():
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


def staff_login(client, email, roles):
    r = client.post("/api/auth/dev-login", json={"email": email, "roles": roles})
    assert r.status_code == 200, r.text
    return {"X-Requested-With": "toktok-admin"}


def test_cms_workflow_end_to_end(client):
    # Operator drafts and submits; admin publishes; chatbot search sees it immediately.
    h = staff_login(client, "editor@toktok.mn", ["operator"])
    body = {"persona": "driver", "title": "Өвлийн улиралд ажиллах заавар",
            "body": "## Гулгаа\nӨвлийн улиралд мөстэй зам дээр хурдаа 30 км/ц-аас хэтрүүлэхгүй."}
    assert client.put("/api/admin/kb/driver-winter/draft", json=body).status_code == 403  # CSRF header missing
    v = client.put("/api/admin/kb/driver-winter/draft", json=body, headers=h).json()
    assert v["state"] == "draft" and v["version"] == 1
    assert client.post("/api/admin/kb/driver-winter/submit", headers=h).json()["state"] == "in_review"
    assert client.post("/api/admin/kb/driver-winter/versions/1/approve", json={}, headers=h).status_code == 403

    h = staff_login(client, "lead@toktok.mn", ["admin"])
    review = client.get("/api/admin/kb/review").json()
    assert [r["draft"]["doc_id"] for r in review] == ["driver-winter"]
    assert client.post("/api/admin/kb/driver-winter/versions/1/approve", json={"comment": "ok"},
                       headers=h).json()["state"] == "published"
    hits = client.post("/api/admin/kb-search", json={"query": "mostei zam hurd", "persona": "driver"},
                       headers=h).json()
    assert hits[0]["doc_id"] == "driver-winter"

    # Seeded Git docs are published v1; archive hides them, restore brings them back.
    assert client.post("/api/admin/kb/driver-fees/archive", headers=h).status_code == 200
    ids = [x["doc_id"] for x in client.post("/api/admin/kb-search", json={"query": "төлбөр бонус",
                                                                          "persona": "driver"}, headers=h).json()]
    assert "driver-fees" not in ids
    assert client.post("/api/admin/kb/driver-fees/restore", headers=h).status_code == 200
    overview = client.get("/api/admin/kb").json()
    assert overview["modes"]["driver"] in ("full", "rag")
    assert any(d["id"] == "driver-fees" and d["status"] == "active" for d in overview["docs"])

    # Audit trail (admin) records who did what.
    staff_login(client, "admin@toktok.mn", ["admin"])
    actions = [a["action"] for a in client.get("/api/admin/audit").json()]
    assert {"kb.save_draft", "kb.submit", "kb.approve", "kb.archive", "kb.restore"} <= set(actions)


def test_kb_import_csv_and_preview(client):
    h = staff_login(client, "editor@toktok.mn", ["operator"])
    csv = ("id,persona,question,answer,tags\n"
           'customer-promo-expired,customer,Промо код ажиллахгүй байна,"## Шалгах\n1. Кодын хугацааг шалгана.",промо\n'
           ",customer,Хоол хүйтэн ирсэн,Оператор шалгаж шийднэ. Захиалгын дугаараа бэлдээрэй.,гомдол\n"
           "bad id!,customer,x,y,\n")
    r = client.post("/api/admin/kb-import", files={"file": ("faq.csv", csv.encode(), "text/csv")}, headers=h).json()
    assert r["created"] == ["customer-promo-expired", "customer-hool-huiten-irsen"]
    assert r["errors"][0]["row"] == 4
    review = client.get("/api/admin/kb/review").json()
    assert {x["draft"]["doc_id"] for x in review} >= set(r["created"])
    p = client.post("/api/admin/kb-preview", json={"persona": "customer", "question": "Промо код ажиллахгүй",
                                                    "doc_id": "customer-promo-expired"}, headers=h).json()
    assert p["overlay"]["doc_id"] == "customer-promo-expired" and p["answer"]


def test_operator_handoff_round_trip(client):
    ev = chat(client, "demo-customer", "operatortoi holbogdmoor baina")
    sid = ev[0]["data"]["session_id"]
    esc = next(e for e in ev if e["type"] == "escalation")["data"]
    assert esc["mode"] == "inchat"

    # While the handoff is open, the user's messages go to the operator, not the bot.
    ev = chat(client, "demo-customer", "Захиалга O-24001 хоцорч байна", session_id=sid)
    assert [e["type"] for e in ev] == ["meta", "handoff", "done"]

    h = staff_login(client, "op@toktok.mn", ["operator"])
    queue = client.get("/api/operator/queue").json()
    assert [q["id"] for q in queue] == [esc["ticket_id"]]
    detail = client.get(f"/api/operator/handoffs/{esc['ticket_id']}").json()
    assert any(m["sender"] == "user" and "O-24001" in m["text"] for m in detail["messages"])
    assert detail["transcript"][0]["text"] == "operatortoi holbogdmoor baina"
    assert client.post(f"/api/operator/handoffs/{esc['ticket_id']}/claim", headers=h).status_code == 200
    client.post(f"/api/operator/handoffs/{esc['ticket_id']}/messages", json={"text": "Шалгаж байна, 5 минут."},
                headers=h)

    poll = client.get("/api/chat/handoff", params={"session_id": sid},
                      headers={"Authorization": "Bearer demo-customer"}).json()
    assert poll["active"] and any(m["sender"] == "operator" and "5 минут" in m["text"] for m in poll["messages"])
    # Another user cannot read it.
    assert client.get("/api/chat/handoff", params={"session_id": sid},
                      headers={"Authorization": "Bearer demo-driver"}).status_code == 403

    client.post(f"/api/operator/handoffs/{esc['ticket_id']}/resolve", json={"note": "Хүргэгчтэй холбогдсон"},
                headers=h)
    ev = chat(client, "demo-customer", "Баярлалаа", session_id=sid)
    assert any(e["type"] == "text" for e in ev)  # the bot is back


def test_new_chat_closes_open_handoff(client):
    ev = chat(client, "demo-customer", "operatortoi holbogdmoor baina")
    sid = ev[0]["data"]["session_id"]
    tid = next(e for e in ev if e["type"] == "escalation")["data"]["ticket_id"]
    user = {"Authorization": "Bearer demo-customer"}
    # Someone else cannot close it.
    assert client.post("/api/chat/handoff/close", params={"session_id": sid},
                       headers={"Authorization": "Bearer demo-driver"}).status_code == 403
    assert client.post("/api/chat/handoff/close", params={"session_id": sid}, headers=user).json() == {"closed": True}

    h = staff_login(client, "op@toktok.mn", ["operator"])
    assert client.get("/api/operator/queue").json() == []  # gone from the queue
    detail = client.get(f"/api/operator/handoffs/{tid}").json()
    assert "шинэ яриа" in detail["messages"][-1]["text"]
    assert client.post(f"/api/operator/handoffs/{tid}/messages", json={"text": "Сайн уу"}, headers=h).status_code == 409
    # Closing again is a no-op.
    assert client.post("/api/chat/handoff/close", params={"session_id": sid}, headers=user).json() == {"closed": False}


def test_instruction_change_waits_for_admin_review(client):
    h = staff_login(client, "op@toktok.mn", ["operator"])
    body = {"body": "- Хэл: зөвхөн кирилл монголоор хариул.\n- Хамгийн богино хариулт өг."}
    assert client.put("/api/admin/prompts/driver/draft", json=body, headers=h).status_code == 200
    v = client.post("/api/admin/prompts/driver/submit", headers=h).json()["version"]
    assert client.post(f"/api/admin/prompts/driver/versions/{v}/approve", json={}, headers=h).status_code == 403
    assert client.get("/api/admin/staff").status_code == 403  # operators cannot manage staff

    h = staff_login(client, "admin@toktok.mn", ["admin"])
    queue = client.get("/api/admin/prompts-review").json()
    assert [(q["draft"]["doc_id"], q["draft"]["author"]) for q in queue] == [("driver", "op@toktok.mn")]
    assert queue[0]["published"]["version"] == 1  # diff base
    r = client.post(f"/api/admin/prompts/driver/versions/{v}/approve", json={"comment": "ok"}, headers=h)
    assert r.json()["state"] == "published"
    assert client.get("/api/admin/prompts-review").json() == []
