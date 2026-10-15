"""Operation-team API: knowledge CMS, metrics, online evaluation, improvement loop."""

import csv
import io
import time
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import container, require
from app.cms.preview import preview_answer
from app.cms.service import CMSError, DraftInput, parse_upload
from app.container import Container
from app.insights.loop import run_insights
from app.memory.repository import EXPORT_COLUMNS, ConversationFilter
from app.orchestrator.graph import UB_TZ
from app.personas.prompts import INSTRUCTIONS_MAX_CHARS, locked_prompt
from app.staff.auth import Principal

admin = APIRouter(prefix="/api/admin")
Persona = Literal["driver", "client", "customer"]


def _cms_call(e: CMSError):
    return HTTPException(e.status, str(e))


# --------------------------------------------------------------- knowledge
@admin.get("/kb")
async def kb_overview(_: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    rows = await c.cms.overview()
    modes = {p: c.services.kb.mode_for(p) for p in ("driver", "client", "customer")}
    tokens = {p: pk.est_tokens for p, pk in c.services.kb.packs.items()}
    return {"docs": rows, "modes": modes, "est_tokens": tokens,
            "full_context_max_tokens": c.settings.full_context_max_tokens}


@admin.get("/kb/review")
async def kb_review(_: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    return await c.cms.review_queue()


@admin.get("/kb/import/template")
async def kb_import_template(_: Principal = Depends(require("kb.read"))):
    csv = ("id,persona,question,answer,tags,visibility\n"
           "customer-promo-expired,customer,Промо код ажиллахгүй байна,"
           "\"## Шалгах\n1. Кодын хугацааг шалгана.\",промо,public\n")
    return Response(csv.encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="toktok-faq-template.csv"'})


@admin.get("/kb/{doc_id}")
async def kb_detail(doc_id: str, _: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    try:
        return await c.cms.detail(doc_id)
    except CMSError as e:
        raise _cms_call(e)


class DraftIn(BaseModel):
    persona: Literal["driver", "client", "customer", "all"]
    title: str
    body: str
    tags: list[str] = []
    visibility: Literal["public", "internal"] = "public"


@admin.put("/kb/{doc_id}/draft")
async def kb_save_draft(doc_id: str, body: DraftIn, p: Principal = Depends(require("kb.edit")),
                        c: Container = Depends(container)):
    try:
        v = await c.cms.save_draft(p, doc_id, DraftInput(**body.model_dump()))
    except CMSError as e:
        raise _cms_call(e)
    return v.to_dict()


@admin.delete("/kb/{doc_id}/draft")
async def kb_discard(doc_id: str, p: Principal = Depends(require("kb.edit")), c: Container = Depends(container)):
    try:
        await c.cms.discard(p, doc_id)
    except CMSError as e:
        raise _cms_call(e)
    return {"ok": True}


@admin.post("/kb/{doc_id}/submit")
async def kb_submit(doc_id: str, p: Principal = Depends(require("kb.edit")), c: Container = Depends(container)):
    try:
        return (await c.cms.submit(p, doc_id)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


class ReviewIn(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)


@admin.post("/kb/{doc_id}/versions/{version}/approve")
async def kb_approve(doc_id: str, version: int, body: ReviewIn, p: Principal = Depends(require("kb.publish")),
                     c: Container = Depends(container)):
    try:
        return (await c.cms.approve(p, doc_id, version, body.comment)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


@admin.post("/kb/{doc_id}/versions/{version}/reject")
async def kb_reject(doc_id: str, version: int, body: ReviewIn, p: Principal = Depends(require("kb.publish")),
                    c: Container = Depends(container)):
    if not body.comment:
        raise HTTPException(400, "a rejection needs a comment for the author")
    try:
        return (await c.cms.reject(p, doc_id, version, body.comment)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


class RollbackIn(BaseModel):
    to_version: int


@admin.post("/kb/{doc_id}/rollback")
async def kb_rollback(doc_id: str, body: RollbackIn, p: Principal = Depends(require("kb.publish")),
                      c: Container = Depends(container)):
    try:
        return (await c.cms.rollback(p, doc_id, body.to_version)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


@admin.post("/kb/{doc_id}/archive")
async def kb_archive(doc_id: str, p: Principal = Depends(require("kb.publish")), c: Container = Depends(container)):
    try:
        await c.cms.set_archived(p, doc_id, True)
    except CMSError as e:
        raise _cms_call(e)
    return {"ok": True}


@admin.post("/kb/{doc_id}/restore")
async def kb_restore(doc_id: str, p: Principal = Depends(require("kb.publish")), c: Container = Depends(container)):
    try:
        await c.cms.set_archived(p, doc_id, False)
    except CMSError as e:
        raise _cms_call(e)
    return {"ok": True}


@admin.post("/kb-import")
async def kb_import(file: UploadFile = File(...), p: Principal = Depends(require("kb.edit")),
                    c: Container = Depends(container)):
    data = await file.read()
    if len(data) > 5_000_000:
        raise HTTPException(413, "file too large (max 5 MB)")
    try:
        rows = parse_upload(file.filename or "", data)
    except CMSError as e:
        raise _cms_call(e)
    if len(rows) > 2000:
        raise HTTPException(400, "max 2000 rows per import")
    return await c.cms.import_rows(p, rows, submit=True)


class PreviewIn(BaseModel):
    persona: Persona
    question: str = Field(min_length=2, max_length=1000)
    doc_id: str | None = None
    version: int | None = None


@admin.post("/kb-preview")
async def kb_preview(body: PreviewIn, _: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    overlay = None
    if body.doc_id:
        versions = await c.cms.store.versions(body.doc_id)
        overlay = next((v for v in versions if body.version is None and v.state in ("draft", "in_review", "rejected")
                        or v.version == body.version), None)
        if not overlay:
            raise HTTPException(404, "version not found")
    return await preview_answer(c.services, await c.cms.published_docs(), body.persona, body.question, overlay)


class SearchIn(BaseModel):
    query: str
    persona: Persona


@admin.post("/kb-search")
async def kb_search(body: SearchIn, _: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    hits = await c.services.kb.search(body.query, body.persona, k=c.settings.retrieval_top_k)
    return [{"doc_id": h.doc_id, "title": h.title, "score": round(h.score, 5), "matched_by": h.matched_by,
             "text": h.text} for h in hits]


@admin.post("/kb-reindex")
async def kb_reindex(p: Principal = Depends(require("kb.publish")), c: Container = Depends(container)):
    stats = await c.reindex()
    await c.staff.audit(p.email, "kb.reindex", None, stats)
    return stats


# ------------------------------------------------------ bot instructions
# One editable instruction document per persona (id = persona). Same rights as knowledge:
# editors draft and submit, approvers publish / reject / roll back (four-eyes applies).
@admin.get("/prompts")
async def prompts_overview(_: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    out = []
    for persona in c.prompts.PERSONAS:
        vs = await c.prompts.store.versions(persona)
        pub = next((v for v in vs if v.state == "published"), None)
        draft = next((v for v in vs if v.state in ("draft", "in_review", "rejected")), None)
        out.append({"persona": persona, "locked": locked_prompt(persona),
                    "published": pub.to_dict() if pub else None, "draft": draft.to_dict() if draft else None})
    return {"personas": out, "max_chars": INSTRUCTIONS_MAX_CHARS}


@admin.get("/prompts-review")
async def prompts_review(_: Principal = Depends(require("kb.read")), c: Container = Depends(container)):
    """Instruction drafts waiting for an admin (shown with knowledge drafts in the review tab)."""
    return await c.prompts.review_queue()


@admin.get("/prompts/{persona}")
async def prompts_detail(persona: Persona, _: Principal = Depends(require("kb.read")),
                         c: Container = Depends(container)):
    try:
        return await c.prompts.detail(persona)
    except CMSError as e:
        raise _cms_call(e)


class InstructionsIn(BaseModel):
    body: str = Field(min_length=10, max_length=INSTRUCTIONS_MAX_CHARS)


@admin.put("/prompts/{persona}/draft")
async def prompts_save_draft(persona: Persona, body: InstructionsIn, p: Principal = Depends(require("kb.edit")),
                             c: Container = Depends(container)):
    try:
        data = DraftInput(persona=persona, title=f"{persona} instructions", body=body.body, tags=[])
        return (await c.prompts.save_draft(p, persona, data)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


@admin.delete("/prompts/{persona}/draft")
async def prompts_discard(persona: Persona, p: Principal = Depends(require("kb.edit")),
                          c: Container = Depends(container)):
    try:
        await c.prompts.discard(p, persona)
    except CMSError as e:
        raise _cms_call(e)
    return {"ok": True}


@admin.post("/prompts/{persona}/submit")
async def prompts_submit(persona: Persona, p: Principal = Depends(require("kb.edit")),
                         c: Container = Depends(container)):
    try:
        return (await c.prompts.submit(p, persona)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


@admin.post("/prompts/{persona}/versions/{version}/approve")
async def prompts_approve(persona: Persona, version: int, body: ReviewIn,
                          p: Principal = Depends(require("kb.publish")), c: Container = Depends(container)):
    try:
        return (await c.prompts.approve(p, persona, version, body.comment)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


@admin.post("/prompts/{persona}/versions/{version}/reject")
async def prompts_reject(persona: Persona, version: int, body: ReviewIn,
                         p: Principal = Depends(require("kb.publish")), c: Container = Depends(container)):
    if not body.comment:
        raise HTTPException(400, "a rejection needs a comment for the author")
    try:
        return (await c.prompts.reject(p, persona, version, body.comment)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


@admin.post("/prompts/{persona}/rollback")
async def prompts_rollback(persona: Persona, body: RollbackIn, p: Principal = Depends(require("kb.publish")),
                           c: Container = Depends(container)):
    try:
        return (await c.prompts.rollback(p, persona, body.to_version)).to_dict()
    except CMSError as e:
        raise _cms_call(e)


class InstructionsPreviewIn(BaseModel):
    persona: Persona
    question: str = Field(min_length=2, max_length=1000)
    # Text to try (e.g. the unsaved editor content); omitted = published instructions.
    body: str | None = Field(default=None, max_length=INSTRUCTIONS_MAX_CHARS)


@admin.post("/prompts-preview")
async def prompts_preview(body: InstructionsPreviewIn, _: Principal = Depends(require("kb.read")),
                          c: Container = Depends(container)):
    return await preview_answer(c.services, await c.cms.published_docs(), body.persona, body.question, None,
                                instructions=body.body)


# ------------------------------------------------------------- conversations
def _conv_filter(persona: Persona | None, escalated: bool | None, negative: bool | None, q: str | None,
                 date_from: str | None, date_to: str | None, limit: int = 50, offset: int = 0) -> ConversationFilter:
    """Dates are YYYY-MM-DD in Ulaanbaatar time; date_to is inclusive."""
    def day(s: str | None, plus: int = 0) -> float | None:
        if not s:
            return None
        try:
            d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UB_TZ)
        except ValueError:
            raise HTTPException(400, "dates must be YYYY-MM-DD")
        return (d + timedelta(days=plus)).timestamp()
    return ConversationFilter(persona=persona, escalated=escalated, negative_feedback=negative,
                              q=(q or "").strip() or None, since_epoch=day(date_from), until_epoch=day(date_to, 1),
                              limit=max(1, min(limit, 200)), offset=max(0, offset))


@admin.get("/conversations")
async def conversations(persona: Persona | None = None, escalated: bool | None = None, negative: bool | None = None,
                        q: str | None = None, date_from: str | None = None, date_to: str | None = None,
                        limit: int = 50, offset: int = 0, _: Principal = Depends(require("metrics.read")),
                        c: Container = Depends(container)):
    return await c.services.repo.list_conversations(
        _conv_filter(persona, escalated, negative, q, date_from, date_to, limit, offset))


@admin.get("/conversations.csv")
async def conversations_csv(persona: Persona | None = None, escalated: bool | None = None,
                            negative: bool | None = None, q: str | None = None, date_from: str | None = None,
                            date_to: str | None = None, p: Principal = Depends(require("metrics.read")),
                            c: Container = Depends(container)):
    """Every turn of the matching conversations, one row per question/answer (UTF-8 with BOM for Excel)."""
    rows = await c.services.repo.export_turns(_conv_filter(persona, escalated, negative, q, date_from, date_to))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    await c.staff.audit(p.email, "conversations.export", None, {"rows": len(rows), "persona": persona,
                                                                 "from": date_from, "to": date_to})
    name = f"toktok-chats-{date_from or 'all'}-{date_to or 'now'}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@admin.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str, _: Principal = Depends(require("metrics.read")),
                              c: Container = Depends(container)):
    log = await c.services.repo.conversation_log(conversation_id)
    if not log:
        raise HTTPException(404, "conversation not found")
    return log


# ------------------------------------------------------- metrics & evaluation
@admin.get("/metrics")
async def metrics(days: int = 7, _: Principal = Depends(require("metrics.read")), c: Container = Depends(container)):
    return await c.services.repo.metrics(time.time() - days * 86400)


@admin.get("/escalations")
async def escalations(status: str | None = None, limit: int = 50, _: Principal = Depends(require("metrics.read")),
                      c: Container = Depends(container)):
    return await c.services.repo.list_escalations(status, min(limit, 200))


@admin.get("/judgements")
async def judgements(flagged: bool = True, limit: int = 50, _: Principal = Depends(require("insights.read")),
                     c: Container = Depends(container)):
    return await c.services.repo.list_judgements(flagged, min(limit, 200))


class InsightsIn(BaseModel):
    days: int = Field(default=14, ge=1, le=90)


@admin.post("/insights/run")
async def insights_run(body: InsightsIn, p: Principal = Depends(require("insights.run")),
                       c: Container = Depends(container)):
    if not getattr(c.services.llm, "supports_classify", True):
        raise HTTPException(409, "the configured LLM provider cannot run analysis (LLM_PROVIDER=fake)")
    run = await run_insights(llm=c.services.llm, repo=c.services.repo, cms=c.cms, kb=c.services.kb, actor=p,
                             model=c.settings.agent_model, days=body.days)
    await c.staff.audit(p.email, "insights.run", run["id"], run["stats"])
    return run


@admin.get("/insights")
async def insights_list(limit: int = 10, _: Principal = Depends(require("insights.read")),
                        c: Container = Depends(container)):
    return await c.services.repo.list_insight_runs(min(limit, 50))
