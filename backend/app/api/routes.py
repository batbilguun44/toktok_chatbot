"""End-user API: chat (SSE), operator-reply polling, feedback."""

import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import container, current_user
from app.container import Container
from app.domain import UserContext
from app.orchestrator.service import SessionForbidden

router = APIRouter(prefix="/api")


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
    except StopAsyncIteration:
        first = {"type": "done", "data": {"stop": "empty"}}

    async def gen() -> AsyncIterator[str]:
        yield _sse(first)
        async for ev in events:
            yield _sse(ev)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/handoff")
async def handoff_updates(session_id: str, after: int = 0, user: UserContext = Depends(current_user),
                          c: Container = Depends(container)):
    """Operator replies for the user's chat window (polled every few seconds during a handoff)."""
    try:
        return await c.chat.handoff_updates(user, session_id, after)
    except SessionForbidden:
        raise HTTPException(403, "session belongs to another user")


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
