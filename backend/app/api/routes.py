"""HTTP API: chat (SSE), feedback, and the Operation-team admin endpoints."""

import json
import secrets
import time
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.container import Container
from app.domain import UserContext
from app.orchestrator.service import SessionForbidden
from app.rag.documents import KnowledgeDoc

router = APIRouter(prefix="/api")


def container(request: Request) -> Container:
    return request.app.state.container


async def current_user(
    authorization: str | None = Header(default=None), c: Container = Depends(container)
) -> UserContext:
    """Resolve the TokTok app token (passed by the host app / webview)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    user = await c.services.gateway.resolve_user(authorization.removeprefix("Bearer ").strip())
    if not user:
        raise HTTPException(401, "invalid token")
    return user


async def require_admin(x_admin_key: str | None = Header(default=None), c: Container = Depends(container)) -> None:
    if not x_admin_key or not secrets.compare_digest(x_admin_key, c.settings.admin_api_key):
        raise HTTPException(401, "admin key required")


# --------------------------------------------------------------------- chat
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatIn, user: UserContext = Depends(current_user), c: Container = Depends(container)):
    events = c.chat.stream_turn(user, body.session_id, body.message)
    try:
        first = await anext(events)  # surface session ownership errors as HTTP 403
    except SessionForbidden:
        raise HTTPException(403, "session belongs to another user")

    async def gen() -> AsyncIterator[str]:
        yield _sse(first)
        async for ev in events:
            yield _sse(ev)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/me")
async def me(user: UserContext = Depends(current_user)):
    return {"persona": user.persona, "display_name": user.display_name}


class FeedbackIn(BaseModel):
    turn_id: str
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/feedback")
async def feedback(body: FeedbackIn, user: UserContext = Depends(current_user), c: Container = Depends(container)):
    if not await c.services.repo.add_feedback(body.turn_id, body.rating, body.comment):
        raise HTTPException(404, "unknown turn")
    c.services.tracer.score_feedback(turn_id=body.turn_id, rating=body.rating, comment=body.comment)
    return {"ok": True}


# -------------------------------------------------------------------- admin
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@admin.get("/metrics")
async def metrics(days: int = 7, c: Container = Depends(container)):
    return await c.services.repo.metrics(time.time() - days * 86400)


@admin.get("/escalations")
async def escalations(status: str | None = "open", limit: int = 50, c: Container = Depends(container)):
    return await c.services.repo.list_escalations(status, min(limit, 200))


@admin.get("/knowledge")
async def list_knowledge(c: Container = Depends(container)):
    indexed = c.services.kb.docs
    return [
        {"id": d.id, "persona": d.persona, "title": d.title, "tags": d.tags, "visibility": d.visibility,
         "source": d.source, "updated_at": d.updated_at, "indexed": d.id in indexed, "body": d.body}
        for d in sorted(await c.all_docs(), key=lambda d: (d.persona, d.id))
    ]


class DocIn(BaseModel):
    persona: Literal["driver", "client", "customer", "all"]
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=50_000)
    tags: list[str] = []
    visibility: Literal["public", "internal"] = "public"
    owner: str | None = None


@admin.put("/knowledge/{doc_id}")
async def upsert_knowledge(doc_id: str, body: DocIn, c: Container = Depends(container)):
    await c.services.repo.upsert_doc(KnowledgeDoc(id=doc_id, source="admin", **body.model_dump()))
    return {"ok": True, "index": await c.reindex()}


@admin.delete("/knowledge/{doc_id}")
async def delete_knowledge(doc_id: str, c: Container = Depends(container)):
    """Removes an admin-edited doc (repository files are changed via git)."""
    if not await c.services.repo.delete_doc(doc_id):
        raise HTTPException(404, "no admin-edited doc with this id")
    return {"ok": True, "index": await c.reindex()}


@admin.post("/knowledge/reindex")
async def reindex(c: Container = Depends(container)):
    return await c.reindex()


class SearchIn(BaseModel):
    query: str
    persona: Literal["driver", "client", "customer"]


@admin.post("/knowledge/search")
async def search_knowledge(body: SearchIn, c: Container = Depends(container)):
    hits = await c.services.kb.search(body.query, body.persona, k=c.settings.retrieval_top_k)
    return [{"doc_id": h.doc_id, "title": h.title, "score": round(h.score, 5), "matched_by": h.matched_by,
             "text": h.text} for h in hits]
