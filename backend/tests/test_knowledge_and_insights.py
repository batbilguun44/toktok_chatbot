import json

import httpx

from app.cms.service import CMSError, DraftInput
from app.container import build_container
from app.insights.judge import Judgement
from app.insights.loop import Cluster, InsightReport, run_insights
from app.rag.contextual import Contextualizer
from app.rag.documents import KnowledgeDoc, chunk_doc
from app.rag.knowledge_base import SearchHit
from app.rag.rerank import VoyageReranker
from app.staff.auth import Principal
from app.staff.store import permissions_for
from tests.conftest import collect
from tests.helpers import ScriptedProvider

EDITOR = Principal("ed@toktok.mn", ["operator"], permissions_for(["operator"]), "dev")
APPROVER = Principal("lead@toktok.mn", ["admin"], permissions_for(["admin"]), "dev")
ADMIN = Principal("admin@toktok.mn", ["admin"], permissions_for(["admin"]), "dev")


async def turn(c, token, text, sid=None):
    user = await c.services.gateway.resolve_user(token)
    events = await collect(c.chat.stream_turn(user, sid, text))
    return events, events[0]["data"].get("session_id")


async def publish(c, doc_id, persona, title, body):
    await c.cms.save_draft(EDITOR, doc_id, DraftInput(persona, title, body, []))
    v = await c.cms.submit(EDITOR, doc_id)
    await c.cms.approve(APPROVER, doc_id, v.version, None)


# ------------------------------------------------------------ knowledge modes
async def test_full_context_mode_freezes_pack_and_appends_updates(settings):
    llm = ScriptedProvider(reply="Хариу")
    c = await build_container(settings.model_copy(update={"knowledge_mode": "full"}), llm=llm)
    _, sid = await turn(c, "demo-driver", "Хүргэлтийн төлбөр яаж бодогддог вэ?")
    first = llm.calls[-1]
    assert "<knowledge_base>" in first["system"] and 'id="driver-fees"' in first["system"]
    assert 'id="client-settlement"' not in first["system"]  # persona-scoped
    assert "<verified_knowledge>" not in first["turn_context"]

    await publish(c, "driver-fees", "driver", "Хүргэлтийн төлбөр (шинэ)", "## Шинэ тариф\nСуурь хөлс 5000₮.")
    await turn(c, "demo-driver", "Тэгвэл суурь хөлс хэд вэ?", sid)
    second = llm.calls[-1]
    assert second["system"] == first["system"]  # frozen: cache + reasoning stay valid
    assert "<knowledge_updates>" in second["turn_context"] and "5000₮" in second["turn_context"]

    await turn(c, "demo-driver", "Баярлалаа", sid)
    assert "<knowledge_updates>" not in llm.calls[-1]["turn_context"]  # delivered once

    _, sid2 = await turn(c, "demo-driver", "шинэ яриа")
    assert "5000₮" in llm.calls[-1]["system"]  # new sessions start from the new pack
    await c.aclose()


async def test_auto_mode_switches_to_rag_when_knowledge_is_large(settings):
    llm = ScriptedProvider()
    c = await build_container(settings.model_copy(update={"knowledge_mode": "auto", "full_context_max_tokens": 10}),
                              llm=llm)
    assert c.services.kb.mode_for("driver") == "rag"
    await turn(c, "demo-driver", "апп ажиллахгүй байна")
    call = llm.calls[-1]
    assert "<knowledge_base>" not in call["system"] and "driver-app-troubleshooting" in call["turn_context"]
    await c.aclose()


async def test_handback_gives_bot_the_operator_conversation(settings):
    llm = ScriptedProvider(tool_calls=[("escalate_to_human", {"category": "complaint",
                                                               "summary": "Хоол дутуу ирсэн, O-23950 захиалга."})])
    c = await build_container(settings, llm=llm)
    _, sid = await turn(c, "demo-customer", "Хоол дутуу ирсэн")
    session = await c.services.sessions.get(sid)
    esc_id = session.handoff_id
    assert esc_id
    await c.services.handoff.claim(ADMIN, esc_id)
    await c.services.handoff.reply(ADMIN, esc_id, "Буцаалт 2 хоногт орно.")
    await c.services.handoff.resolve(ADMIN, esc_id, "Буцаалт олгосон")
    llm.tool_calls = []
    await turn(c, "demo-customer", "За ойлголоо", sid)
    ctx = llm.calls[-1]["turn_context"]
    assert "<operator_handoff>" in ctx and "Буцаалт 2 хоногт" in ctx and "Буцаалт олгосон" in ctx
    assert (await c.services.sessions.get(sid)).handoff_id is None
    await c.aclose()


async def test_replicas_pick_up_published_knowledge(settings):
    """Two containers sharing stores: a publish on one is seen by the other (revision check)."""
    s = settings.model_copy(update={"kb_revision_check_seconds": 0, "knowledge_mode": "rag"})
    a = await build_container(s, llm=ScriptedProvider())
    b = await build_container(s, llm=ScriptedProvider())
    b.cms.store = a.cms.store  # shared database
    await publish(a, "driver-winter", "driver", "Өвлийн замын хурд", "Мөстэй замд хурдаа сааруул.")
    await b.ensure_fresh()
    assert "driver-winter" in b.services.kb.docs
    await a.aclose()
    await b.aclose()


# ------------------------------------------------------------------- CMS rules
async def test_cms_rules(settings):
    c = await build_container(settings, llm=ScriptedProvider())
    await c.cms.save_draft(EDITOR, "driver-x", DraftInput("driver", "Гарчиг", "Агуулга 1234567890", []))
    v = await c.cms.submit(EDITOR, "driver-x")
    try:
        await c.cms.approve(EDITOR.__class__("ed@toktok.mn", ["operator"], permissions_for(["operator"]), "dev"),
                            "driver-x", v.version, None)
        raise AssertionError("author approved own change")
    except CMSError as e:
        assert e.status == 403
    await c.cms.reject(APPROVER, "driver-x", v.version, "Дутуу")
    v = await c.cms.save_draft(EDITOR, "driver-x", DraftInput("driver", "Гарчиг", "Агуулга засварласан", []))
    assert v.state == "draft" and v.version == 1  # same open version, edited
    await c.cms.submit(EDITOR, "driver-x")
    await c.cms.approve(ADMIN, "driver-x", 1, "admin may approve")
    await publish(c, "driver-x", "driver", "Гарчиг 2", "Агуулга хоёр дахь хувилбар")
    rb = await c.cms.rollback(APPROVER, "driver-x", 1)
    states = {x.version: x.state for x in await c.cms.store.versions("driver-x")}
    assert rb.version == 3 and states == {1: "superseded", 2: "superseded", 3: "published"}
    assert "засварласан" in c.services.kb.docs["driver-x"].body
    for bad in [("BAD ID", DraftInput("driver", "Гарчиг", "Агуулга 1234567890", [])),
                ("ok-id", DraftInput("nobody", "Гарчиг", "Агуулга 1234567890", []))]:
        try:
            await c.cms.save_draft(EDITOR, *bad)
            raise AssertionError("validation missed")
        except CMSError:
            pass
    await c.aclose()


async def test_xlsx_import(settings):
    from io import BytesIO

    from openpyxl import Workbook

    from app.cms.service import parse_upload

    wb = Workbook()
    ws = wb.active
    ws.append(["persona", "question", "answer"])
    ws.append(["driver", "Цүнх солих", "Гэмтсэн цүнхийг оффист авчирч солиулна."])
    buf = BytesIO()
    wb.save(buf)
    rows = parse_upload("faq.xlsx", buf.getvalue())
    c = await build_container(settings, llm=ScriptedProvider())
    r = await c.cms.import_rows(EDITOR, rows)
    assert r["created"] == ["driver-tsunh-solih"] and not r["errors"]
    await c.aclose()


# ---------------------------------------------------------- judge & insights
async def test_judge_scores_sampled_turns_and_metrics(settings):
    j = Judgement(grounded="no", answered=False, tone_ok=True, score=2, unsupported_claims=["5000₮"],
                  rationale="Тариф баримтад алга.")
    llm = ScriptedProvider(reply="Суурь хөлс 5000₮", classify_results={"Judgement": j})
    c = await build_container(settings.model_copy(update={"judge_sample_rate": 1.0}), llm=llm)
    events, _ = await turn(c, "demo-driver", "Суурь хөлс хэд вэ?")
    await c.services.drain()
    judge_call = next(x for x in llm.classify_calls if x["schema"] == "Judgement")
    assert "Суурь хөлс 5000₮" in judge_call["user"] and "knowledge_evidence" in judge_call["user"]
    flagged = await c.services.repo.list_judgements(True, 10)
    assert flagged[0]["grounded"] == "no"
    m = await c.services.repo.metrics(0)
    assert m["groundedness"] == 0.0 and m["judged_answers"] == 1
    await c.aclose()


async def test_insights_cluster_failures_into_reviewable_drafts(settings):
    llm = ScriptedProvider(reply="Мэдэхгүй", tool_calls=[("escalate_to_human", {
        "category": "not_in_knowledge", "summary": "Промо код ажиллахгүй, мэдээлэл алга."})])
    c = await build_container(settings, llm=llm)
    events, _ = await turn(c, "demo-customer", "promo kod ajillahgui bn")
    turn_id = next(e["data"]["turn_id"] for e in events if e["type"] == "meta" and "turn_id" in e["data"])

    def report(system, user):
        assert turn_id in user and "customer-order-tracking" in user  # sees failures + existing docs
        return InsightReport(clusters=[
            Cluster(persona="customer", topic="Промо код", turn_ids=[turn_id, "made-up-id"], covered_by_doc_id=None,
                    proposed_doc_id="customer-promo-code", proposed_title="Промо код ажиллахгүй байна",
                    proposed_body="## Шалгах\n1. Кодын хугацаа: [ТОДРУУЛАХ: хугацааны дүрэм]", notes=""),
            Cluster(persona="customer", topic="Хүргэлт", turn_ids=[], covered_by_doc_id="customer-order-tracking",
                    proposed_doc_id=None, proposed_title=None, proposed_body=None, notes="ok"),
        ])

    llm.classify_results["InsightReport"] = report
    run = await run_insights(llm=llm, repo=c.services.repo, cms=c.cms, kb=c.services.kb, actor=APPROVER,
                             model="claude-opus-5-5")
    assert run["stats"] == {"questions": 1, "days": 14, "drafts": 1}
    assert run["clusters"][0]["turn_ids"] == [turn_id]  # hallucinated ids dropped
    v = (await c.cms.store.versions("customer-promo-code"))[0]
    assert v.state == "draft" and v.source == "ai-suggestion"  # never auto-published
    assert "customer-promo-code" not in c.services.kb.docs
    assert next(x for x in llm.classify_calls if x["schema"] == "InsightReport")["model"] == "claude-opus-5-5"
    await c.aclose()


# --------------------------------------------------- rerank & contextual RAG
async def test_voyage_rerank_reorders_and_degrades():
    hits = [SearchHit(f"c{i}", f"d{i}", "T", "T", f"text {i}", 1.0, ["bm25"]) for i in range(3)]

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        assert body["model"] == "rerank-2.5" and body["top_k"] == 2 and len(body["documents"]) == 3
        return httpx.Response(200, json={"data": [{"index": 2, "relevance_score": 0.9},
                                                  {"index": 0, "relevance_score": 0.4}]})

    out = await VoyageReranker("k", "rerank-2.5", httpx.MockTransport(handler)).rerank("q", hits, 2)
    assert [h.chunk_id for h in out] == ["c2", "c0"] and out[0].matched_by == ["bm25", "rerank"]
    broken = VoyageReranker("k", "m", httpx.MockTransport(lambda r: httpx.Response(500)))
    assert [h.chunk_id for h in await broken.rerank("q", hits, 2)] == ["c0", "c1"]


async def test_contextual_retrieval_prefixes_and_caches():
    llm = ScriptedProvider()
    doc = KnowledgeDoc("d", "driver", "Апп", "## Нэг\n" + "а" * 950 + "\n\n## Хоёр\nБатарей хэмнэх горим унтраа")
    chunks = chunk_doc(doc)
    ctx = Contextualizer(llm)
    out = await ctx.apply(doc, chunks)
    assert all(c.context.startswith("КОНТЕКСТ") for c in out) and len(llm.completions) == len(chunks)
    assert out[0].content_hash != chunks[0].content_hash  # re-indexed when context is added
    await ctx.apply(doc, chunks)
    assert len(llm.completions) == len(chunks)  # cached
