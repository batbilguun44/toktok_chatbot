"""Runs one chat turn (bot or live operator) and yields UI events."""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from app.domain import UserContext
from app.guardrails.checks import check_input
from app.memory.repository import new_id, user_ref
from app.memory.session import SessionState
from app.orchestrator.graph import Services, build_graph

log = logging.getLogger(__name__)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class SessionForbidden(Exception):
    pass


async def _noop() -> None:
    return None


class ChatService:
    def __init__(self, svc: Services, ensure_fresh: Callable[[], Awaitable[None]] = _noop):
        self.svc = svc
        self.graph = build_graph(svc)
        self.ensure_fresh = ensure_fresh

    def user_ref(self, user: UserContext) -> str:
        return user_ref(f"{user.persona}:{user.user_id}", self.svc.settings.user_ref_salt)

    async def _load_session(self, user: UserContext, session_id: str | None, create: bool = True) -> SessionState | None:
        if session_id and _SESSION_ID_RE.match(session_id):
            st = await self.svc.sessions.get(session_id)
            if st:
                if st.user_id != user.user_id or st.persona != user.persona:
                    raise SessionForbidden(session_id)
                if st.provider == self.svc.llm.name:
                    return st
        if not create:
            return None
        return SessionState(
            session_id=f"s_{uuid.uuid4().hex}", persona=user.persona, user_id=user.user_id,
            provider=self.svc.llm.name,
        )

    async def stream_turn(self, user: UserContext, session_id: str | None, text: str) -> AsyncIterator[dict[str, Any]]:
        s = self.svc.settings
        rate_key = f"{user.persona}:{user.user_id}"
        if await self.svc.sessions.hit_rate(rate_key) > s.rate_limit_per_minute:
            yield {"type": "error", "data": {"code": "rate_limited",
                                             "message": "Хэт олон хүсэлт илгээлээ. Түр хүлээгээд дахин оролдоно уу."}}
            return

        session = await self._load_session(user, session_id)
        if not await self.svc.sessions.acquire(session.session_id):
            yield {"type": "error", "data": {"code": "busy", "message": "Өмнөх асуултад хариулж байна. Түр хүлээнэ үү."}}
            return
        try:
            # 1) Live operator: messages go to the operator console, not the bot.
            handoff_note = None
            if session.handoff_id:
                open_esc = await self.svc.handoff.is_open(session.handoff_id)
                if open_esc:
                    masked = check_input(text, s.max_message_chars)
                    if masked.blocked:
                        return
                    mid = await self.svc.handoff.user_message(session.handoff_id, masked.text)
                    yield {"type": "meta", "data": {"session_id": session.session_id}}
                    yield {"type": "handoff", "data": {"ticket_id": session.handoff_id, "status": open_esc["status"],
                                                       "message_id": mid}}
                    yield {"type": "done", "data": {"stop": "handoff"}}
                    return
                # Operator finished: the bot continues with the operator conversation as context.
                handoff_note = await self.svc.handoff.transcript_for_bot(session.handoff_id)
                session.handoff_id = None

            # 2) Bot turn.
            await self.ensure_fresh()
            turn_id = new_id("turn")
            uref = self.user_ref(user)
            yield {"type": "meta", "data": {"session_id": session.session_id, "turn_id": turn_id}}
            trace = self.svc.tracer.start_turn(
                turn_id=turn_id, session_id=session.session_id, user_ref=uref, persona=user.persona, input=text[:500]
            )
            state = {
                "turn_id": turn_id, "user": user, "user_ref": uref, "session": session,
                "raw_text": text, "started": time.time(), "trace": trace, "handoff_note": handoff_note,
            }
            try:
                async for event in self.graph.astream(state, stream_mode="custom"):
                    yield event
            except Exception:
                log.exception("turn %s failed", turn_id)
                phone = s.operator_phone(user.persona)
                yield {"type": "error", "data": {"code": "internal",
                                                 "message": f"Алдаа гарлаа. Оператор: {phone}"}}
                yield {"type": "done", "data": {"turn_id": turn_id, "stop": "error"}}
        finally:
            await self.svc.sessions.release(session.session_id)

    async def handoff_updates(self, user: UserContext, session_id: str, after_id: int) -> dict[str, Any]:
        """Polled by the chat UI while an operator is handling the conversation."""
        session = await self._load_session(user, session_id, create=False)
        if not session or not session.handoff_id:
            return {"active": False, "messages": []}
        esc = await self.svc.repo.get_escalation(session.handoff_id)
        msgs = await self.svc.repo.list_handoff_messages(session.handoff_id, after_id)
        return {
            "active": bool(esc and esc["status"] in ("queued", "active")),
            "status": esc["status"] if esc else None,
            "ticket_id": session.handoff_id,
            "messages": [{"id": m["id"], "sender": m["sender"], "text": m["text"], "created_at": m["created_at"]}
                         for m in msgs if m["sender"] in ("operator", "system")],
            "last_id": msgs[-1]["id"] if msgs else after_id,
        }
