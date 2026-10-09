"""Operator console API: live handoff queue, conversation view, replies, resolution."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import container, require
from app.container import Container
from app.handoff.service import HandoffError
from app.staff.auth import Principal

operator = APIRouter(prefix="/api/operator")


@operator.get("/queue")
async def queue(status: Literal["queued", "active", "resolved"] = "queued", mine: bool = False,
                p: Principal = Depends(require("handoff")), c: Container = Depends(container)):
    rows = await c.services.repo.list_escalations(status, 100, assignee=p.email if mine else None)
    return [r for r in rows if r.get("mode") == "inchat" or status == "resolved"]


@operator.get("/handoffs/{escalation_id}")
async def detail(escalation_id: str, after: int = 0, _: Principal = Depends(require("handoff")),
                 c: Container = Depends(container)):
    esc = await c.services.repo.get_escalation(escalation_id)
    if not esc:
        raise HTTPException(404, "not found")
    session = await c.services.sessions.get(esc["conversation_id"])
    return {
        "escalation": esc,
        # Bot conversation before the handoff (session memory; expires with the session).
        "transcript": [{"role": r, "text": t} for r, t in (session.recent if session else [])],
        "messages": await c.services.repo.list_handoff_messages(escalation_id, after),
    }


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _err(e: HandoffError):
    return HTTPException(e.status, str(e))


@operator.post("/handoffs/{escalation_id}/claim")
async def claim(escalation_id: str, p: Principal = Depends(require("handoff")), c: Container = Depends(container)):
    try:
        await c.services.handoff.claim(p, escalation_id)
    except HandoffError as e:
        raise _err(e)
    return {"ok": True}


@operator.post("/handoffs/{escalation_id}/messages")
async def reply(escalation_id: str, body: ReplyIn, p: Principal = Depends(require("handoff")),
                c: Container = Depends(container)):
    try:
        return {"id": await c.services.handoff.reply(p, escalation_id, body.text)}
    except HandoffError as e:
        raise _err(e)


@operator.post("/handoffs/{escalation_id}/resolve")
async def resolve(escalation_id: str, body: ResolveIn, p: Principal = Depends(require("handoff")),
                  c: Container = Depends(container)):
    try:
        await c.services.handoff.resolve(p, escalation_id, body.note)
    except HandoffError as e:
        raise _err(e)
    return {"ok": True}
