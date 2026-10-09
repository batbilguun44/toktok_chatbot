"""Runs one chat turn through the graph and yields UI events."""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.domain import UserContext
from app.memory.repository import new_id, user_ref
from app.memory.session import SessionState
from app.orchestrator.graph import Services, build_graph

log = logging.getLogger(__name__)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class SessionForbidden(Exception):
    pass


class ChatService:
    def __init__(self, svc: Services):
        self.svc = svc
        self.graph = build_graph(svc)

    def user_ref(self, user: UserContext) -> str:
        return user_ref(f"{user.persona}:{user.user_id}", self.svc.settings.user_ref_salt)

    async def _load_session(self, user: UserContext, session_id: str | None) -> SessionState:
        if session_id and _SESSION_ID_RE.match(session_id):
            st = await self.svc.sessions.get(session_id)
            if st:
                if st.user_id != user.user_id or st.persona != user.persona:
                    raise SessionForbidden(session_id)
                if st.provider == self.svc.llm.name:
                    return st
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
            turn_id = new_id("turn")
            uref = self.user_ref(user)
            yield {"type": "meta", "data": {"session_id": session.session_id, "turn_id": turn_id}}
            trace = self.svc.tracer.start_turn(
                turn_id=turn_id, session_id=session.session_id, user_ref=uref, persona=user.persona, input=text[:500]
            )
            state = {
                "turn_id": turn_id, "user": user, "user_ref": uref, "session": session,
                "raw_text": text, "started": time.time(), "trace": trace,
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
