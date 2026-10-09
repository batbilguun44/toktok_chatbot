"""Operation-team API: knowledge CMS, metrics, online evaluation, improvement loop."""

import time
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import container, require
from app.cms.preview import preview_answer
from app.cms.service import CMSError, DraftInput, parse_upload
from app.container import Container
from app.insights.loop import run_insights
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
