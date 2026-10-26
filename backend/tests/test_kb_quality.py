"""Knowledge management at scale: duplicates, contradictions, content search, usage and list metadata."""

from app.cms.quality import ConflictReport, find_conflicts, find_similar, similarity
from app.cms.service import DraftInput
from app.container import build_container
from app.staff.auth import Principal
from app.staff.store import permissions_for
from tests.conftest import collect
from tests.helpers import ScriptedProvider

OP = Principal("op@toktok.mn", ["operator"], permissions_for(["operator"]), "dev")


async def test_duplicate_warning_finds_same_topic_across_spellings(settings):
    c = await build_container(settings, llm=ScriptedProvider())
    fees = c.services.kb.docs["driver-fees"]
    # Same topic, reworded and partly in Latin letters → flagged; another topic → not.
    draft = fees.body.replace("Хүргэлтийн", "Hurgeltiin")[:900]
    sim = find_similar("driver", "Хүргэлтийн төлбөр ба бонус", draft, await c.cms.candidates())
    assert sim and sim[0]["doc_id"] == "driver-fees" and sim[0]["score"] >= 0.5
    editing_itself = find_similar("driver", "Хүргэлтийн төлбөр", draft, await c.cms.candidates(), exclude_id="driver-fees")
    assert all(s["doc_id"] != "driver-fees" for s in editing_itself)  # a document is not its own duplicate
    unrelated = find_similar("driver", "Өвлийн малгай", "Цасан бороотой өдөр малгай өмсөөрэй.", await c.cms.candidates())
    assert all(s["doc_id"] != "driver-fees" for s in unrelated)
    assert similarity("бонус бодогдох", "") == 0.0
    # Other user types are not compared (a client doc is not a duplicate of a driver draft).
    assert all(s["persona"] in ("driver", "all") for s in sim)
    await c.aclose()


async def test_open_drafts_by_other_operators_count_as_duplicates(settings):
    c = await build_container(settings, llm=ScriptedProvider())
    text = "## Өвлийн хувцас\nХүргэгч өвөл дулаан хүрэм, бээлий өмсөнө. Хүрмийг агуулахаас үнэгүй авна."
    await c.cms.save_draft(OP, "driver-winter-a", DraftInput("driver", "Өвлийн хувцас", text, []))
    sim = find_similar("driver", "Өвлийн дулаан хувцас", text, await c.cms.candidates(), exclude_id="driver-winter-b")
    assert sim[0]["doc_id"] == "driver-winter-a" and sim[0]["state"] == "draft" and sim[0]["author"] == "op@toktok.mn"
    await c.aclose()


async def test_conflicts_are_reported_only_for_compared_documents(settings):
    report = ConflictReport(conflicts=[
        {"other_doc_id": "driver-fees", "new_statement": "2 хоногт", "existing_statement": "3 хоногт",
         "explanation": "Олгох хугацаа зөрж байна."},
        {"other_doc_id": "made-up-doc", "new_statement": "a", "existing_statement": "b", "explanation": "x"},
    ])
    llm = ScriptedProvider(classify_results={"ConflictReport": report})
    c = await build_container(settings, llm=llm)
    others = [x for x in await c.cms.candidates() if x.doc_id == "driver-fees"]
    out = await find_conflicts(llm, "Бонус", "Бонус 2 хоногт олгоно.", others)
    assert [o["other_doc_id"] for o in out] == ["driver-fees"] and out[0]["other_title"]
    assert "driver-fees" in llm.classify_calls[-1]["user"]
    assert await find_conflicts(llm, "x", "y", []) == []
    await c.aclose()


async def test_full_context_answers_record_their_source_and_usage(settings):
    llm = ScriptedProvider(reply="Бонус ингэж бодогдоно.", intent="delivery_fee")
    c = await build_container(settings.model_copy(update={"knowledge_mode": "full"}), llm=llm)
    user = await c.services.gateway.resolve_user("demo-driver")
    events = await collect(c.chat.stream_turn(user, None, "Хүргэлтийн бонус хэрхэн бодогддог вэ?"))
    assert not any(e["type"] == "sources" for e in events)  # attribution is for analytics, not shown to users
    turn = c.services.repo.turns[-1]
    assert turn.sources and turn.sources[0] == "driver-fees"
    await c.services.repo.add_feedback(turn.id, -1, None)
    usage = await c.services.repo.kb_usage(0)
    assert usage["driver-fees"]["uses"] == 1 and usage["driver-fees"]["negative"] == 1
    await c.aclose()


async def test_overview_shows_who_added_and_published(settings):
    c = await build_container(settings, llm=ScriptedProvider())
    await c.cms.save_draft(OP, "driver-new-rule", DraftInput("driver", "Шинэ дүрэм", "Дүрэм нэг хоёр гурав дөрөв", ["дүрэм"]))
    rows = {r["id"]: r for r in await c.cms.overview()}
    assert rows["driver-new-rule"]["created_by"] == "op@toktok.mn" and rows["driver-new-rule"]["tags"] == ["дүрэм"]
    assert rows["driver-fees"]["created_by"] == "git" and rows["driver-fees"]["published_at"]
    found = await c.cms.store.find_text("дүрэм нэг", 10)
    assert [v.doc_id for v in found] == ["driver-new-rule"]
    await c.aclose()


def test_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "",
                 "STAFF_SESSION_SECRET": "s" * 40, "JUDGE_SAMPLE_RATE": "0"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        h = {"X-Requested-With": "toktok-admin"}
        client.post("/api/auth/dev-login", json={"email": "op@toktok.mn", "roles": ["operator"]})
        kb = client.get("/api/admin/kb").json()
        assert kb["stale_days"] == 180 and "usage" in kb["docs"][0] and "created_by" in kb["docs"][0]
        found = client.get("/api/admin/kb-find", params={"q": "bonus", "persona": "driver"}).json()
        assert found and found[0]["doc_id"] == "driver-fees" and "meaning" in found[0]["match"]
        sim = client.post("/api/admin/kb-similar", headers=h, json={
            "persona": "driver", "title": "Хүргэлтийн төлбөр, бонус", "body": "Хүргэлтийн төлбөр, бонус хэрхэн бодогддог"}).json()
        assert sim and sim[0]["doc_id"] == "driver-fees"
        # The offline provider has no classifier: the AI check reports it is unavailable instead of failing hard.
        r = client.post("/api/admin/kb-conflicts", headers=h, json={
            "persona": "driver", "title": "Бонус", "body": "Хүргэлтийн бонус 2 хоногт олгоно"})
        assert r.status_code == 503
    get_settings.cache_clear()


def test_review_history_keeps_rejections_after_edits(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "",
                 "STAFF_SESSION_SECRET": "s" * 40, "JUDGE_SAMPLE_RATE": "0"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        h = {"X-Requested-With": "toktok-admin"}
        body = {"persona": "driver", "title": "Шөнийн хүргэлт", "body": "Шөнийн хүргэлтэд нэмэгдэл олгоно.", "tags": []}
        client.post("/api/auth/dev-login", json={"email": "op@toktok.mn", "roles": ["operator"]})
        client.put("/api/admin/kb/driver-night/draft", json=body, headers=h)
        client.post("/api/admin/kb/driver-night/submit", headers=h)
        assert client.get("/api/admin/review-history").status_code == 403  # admins only
        client.post("/api/auth/dev-login", json={"email": "boss@toktok.mn", "roles": ["admin"]})
        client.post("/api/admin/kb/driver-night/versions/1/reject", json={"comment": "Дүн дутуу"}, headers=h)
        client.post("/api/auth/dev-login", json={"email": "op@toktok.mn", "roles": ["operator"]})
        client.put("/api/admin/kb/driver-night/draft", json={**body, "body": body["body"] + " 3000₮."}, headers=h)
        client.post("/api/admin/kb/driver-night/submit", headers=h)
        client.post("/api/auth/dev-login", json={"email": "boss@toktok.mn", "roles": ["admin"]})
        client.post("/api/admin/kb/driver-night/versions/1/approve", json={"comment": "ok"}, headers=h)
        hist = client.get("/api/admin/review-history").json()
        mine = [(r["decision"], r["reviewer"], r["comment"]) for r in hist if r["doc_id"] == "driver-night"]
        assert mine == [("approve", "boss@toktok.mn", "ok"), ("reject", "boss@toktok.mn", "Дүн дутуу")]
        assert hist[0]["title"] == "Шөнийн хүргэлт" and hist[0]["author"] == "op@toktok.mn" and hist[0]["kind"] == "kb"
    get_settings.cache_clear()
