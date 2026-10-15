"""Conversation history: chat window restore, /admin conversations list, detail and CSV export."""

import csv
import io
import os

import pytest

from app.container import build_container
from app.memory.repository import ConversationFilter
from app.orchestrator.service import SessionForbidden
from app.staff.auth import Principal
from app.staff.store import permissions_for
from tests.conftest import collect
from tests.helpers import ScriptedProvider

OP = Principal("op@toktok.mn", ["operator"], permissions_for(["operator"]), "dev")
DB = os.getenv("TEST_DATABASE_URL")


async def turn(c, token, text, sid=None):
    user = await c.services.gateway.resolve_user(token)
    events = await collect(c.chat.stream_turn(user, sid, text))
    return user, events[0]["data"].get("session_id"), events


async def _scenario(c, llm):
    """Driver asks, bot escalates, operator answers, user writes again (goes to the operator)."""
    user, sid, _ = await turn(c, "demo-driver", "Бонус яаж бодогддог вэ? утас 99112233")
    llm.tool_calls = [("escalate_to_human", {"category": "user_requested", "summary": "Хүн хүссэн"})]
    await turn(c, "demo-driver", "Оператортой ярья", sid)
    llm.tool_calls = []
    session = await c.services.sessions.get(sid)
    await c.services.handoff.claim(OP, session.handoff_id)
    await c.services.handoff.reply(OP, session.handoff_id, "Сайн байна уу, оператор байна.")
    await turn(c, "demo-driver", "Баярлалаа", sid)  # delivered to the operator, not the bot
    return user, sid, session.handoff_id


async def _check(c, llm):
    user, sid, esc_id = await _scenario(c, llm)

    # chat window restore: in order, PII masked, handoff still open with the latest message id
    h = await c.chat.history(user, sid)
    roles = [m["role"] for m in h["messages"]]
    assert roles[:4] == ["user", "assistant", "user", "assistant"]
    assert "[PHONE]" in h["messages"][0]["text"] and "99112233" not in h["messages"][0]["text"]
    assert h["messages"][3].get("escalation", {}).get("ticket_id") == esc_id
    texts = [m["text"] for m in h["messages"]]
    assert "Сайн байна уу, оператор байна." in texts and "Баярлалаа" in texts
    assert texts.index("Сайн байна уу, оператор байна.") < texts.index("Баярлалаа")
    assert h["handoff"]["active"] and h["handoff"]["last_id"] > 0

    # another user cannot read it
    other = await c.services.gateway.resolve_user("demo-driver-new")
    with pytest.raises(SessionForbidden):
        await c.chat.history(other, sid)

    # /admin list + filters + detail + export
    _, sid2, _ = await turn(c, "demo-client", "Салбар харагдахгүй байна")
    r = c.services.repo
    assert {x["id"] for x in (await r.list_conversations(ConversationFilter()))["items"]} == {sid, sid2}
    only = (await r.list_conversations(ConversationFilter(escalated=True)))["items"]
    assert [x["id"] for x in only] == [sid] and only[0]["preview"].startswith("Бонус")
    assert [x["id"] for x in (await r.list_conversations(ConversationFilter(persona="client")))["items"]] == [sid2]
    assert [x["id"] for x in (await r.list_conversations(ConversationFilter(q="салбар")))["items"]] == [sid2]
    log = await r.conversation_log(sid)
    kinds = [e["kind"] for e in log["events"]]
    assert kinds.count("turn") == 2 and "escalation" in kinds and kinds.count("handoff") >= 3
    rows = await r.export_turns(ConversationFilter())
    assert len(rows) == 3 and {row["persona"] for row in rows} == {"driver", "client"}


async def test_history_list_detail_export_in_memory(settings):
    llm = ScriptedProvider(reply="Хариу")
    c = await build_container(settings, llm=llm)
    await _check(c, llm)
    await c.aclose()


@pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL not set")
async def test_history_list_detail_export_postgres(settings):
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(DB, open=False) as pool:
        await pool.open()
        async with pool.connection() as conn:
            await conn.execute("DROP TABLE IF EXISTS feedback, judgements, handoff_messages, turns, conversations, "
                               "escalations CASCADE")
    llm = ScriptedProvider(reply="Хариу")
    c = await build_container(settings.model_copy(update={"database_url": DB}), llm=llm)
    await _check(c, llm)
    await c.aclose()


def test_csv_endpoint_and_admin_only(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "",
                 "STAFF_SESSION_SECRET": "s" * 40, "JUDGE_SAMPLE_RATE": "0"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        r = client.post("/api/chat/stream", json={"message": "Сайн байна уу"},
                        headers={"Authorization": "Bearer demo-driver"})
        assert r.status_code == 200
        h = {"X-Requested-With": "toktok-admin"}
        client.post("/api/auth/dev-login", json={"email": "op@toktok.mn", "roles": ["operator"]})
        assert client.get("/api/admin/conversations").status_code == 403
        client.post("/api/auth/dev-login", json={"email": "boss@toktok.mn", "roles": ["admin"]})
        assert client.get("/api/admin/conversations").json()["total"] == 1
        assert client.get("/api/admin/conversations?date_from=2026-13-01").status_code == 400
        csv_resp = client.get("/api/admin/conversations.csv", headers=h)
        assert csv_resp.headers["content-type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(csv_resp.content.decode("utf-8-sig"))))
        assert len(rows) == 1 and rows[0]["persona"] == "driver" and rows[0]["user_text"] == "Сайн байна уу"
    get_settings.cache_clear()
