"""Live human handoff inside the same chat.

When the agent escalates (and HANDOFF_MODE=inchat), the session is attached to
the escalation. While it is queued/active, user messages go to the operator
console instead of the bot; operator replies reach the user's chat window by
polling. When the operator resolves it, the bot takes over again and receives
the operator conversation as context.

`HandoffNotifier` mirrors events to an external helpdesk / automation (Chatwoot,
Zendesk, n8n, Slack …) via a signed webhook, so TokTok can keep its existing
tool as the operator workspace if it prefers.
"""

import hashlib
import hmac
import json
import logging
from typing import Any, Protocol

import httpx

from app.memory.repository import OPEN_HANDOFF, Repository
from app.staff.auth import Principal
from app.staff.store import StaffStore

log = logging.getLogger(__name__)


class HandoffNotifier(Protocol):
    async def notify(self, event: str, payload: dict[str, Any]) -> None: ...


class NoopNotifier:
    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        return None


class WebhookNotifier:
    """POSTs {"event", "data"} with an HMAC-SHA256 signature header (X-TokTok-Signature)."""

    def __init__(self, url: str, secret: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url, self.secret, self._transport = url, secret, transport

    async def notify(self, event: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"event": event, "data": payload}, ensure_ascii=False, default=str).encode()
        sig = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=5, transport=self._transport) as c:
                await c.post(self.url, content=body, headers={
                    "Content-Type": "application/json", "X-TokTok-Signature": f"sha256={sig}"})
        except Exception as e:  # best effort: the internal console is the source of truth
            log.warning("handoff webhook failed (%s): %s", event, e)


class HandoffError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class HandoffService:
    def __init__(self, repo: Repository, staff: StaffStore, notifier: HandoffNotifier):
        self.repo, self.staff, self.notifier = repo, staff, notifier

    async def is_open(self, escalation_id: str | None) -> dict[str, Any] | None:
        if not escalation_id:
            return None
        e = await self.repo.get_escalation(escalation_id)
        return e if e and e["status"] in OPEN_HANDOFF else None

    async def started(self, escalation: dict[str, Any], transcript: list[tuple[str, str]]) -> None:
        await self.repo.add_handoff_message(escalation["id"], "system", "Чат оператор руу шилжлээ.")
        await self.notifier.notify("handoff.created", {**escalation, "transcript": transcript})

    async def user_message(self, escalation_id: str, text: str) -> int:
        mid = await self.repo.add_handoff_message(escalation_id, "user", text)
        await self.notifier.notify("handoff.user_message", {"escalation_id": escalation_id, "id": mid, "text": text})
        return mid

    async def claim(self, p: Principal, escalation_id: str) -> None:
        if not await self.repo.claim(escalation_id, p.email):
            raise HandoffError("already taken by another operator or closed", 409)
        await self.repo.add_handoff_message(escalation_id, "system", "Оператор холбогдлоо.", author=p.email)
        await self.staff.audit(p.email, "handoff.claim", escalation_id)
        await self.notifier.notify("handoff.claimed", {"escalation_id": escalation_id, "operator": p.email})

    async def reply(self, p: Principal, escalation_id: str, text: str) -> int:
        e = await self.repo.get_escalation(escalation_id)
        if not e or e["status"] not in OPEN_HANDOFF:
            raise HandoffError("conversation is not open", 409)
        if e["assignee"] not in (None, p.email):
            raise HandoffError("another operator owns this conversation", 409)
        if e["assignee"] is None:
            await self.repo.claim(escalation_id, p.email)
        mid = await self.repo.add_handoff_message(escalation_id, "operator", text, author=p.email)
        await self.staff.audit(p.email, "handoff.reply", escalation_id, {"message_id": mid})
        return mid

    async def resolve(self, p: Principal, escalation_id: str, note: str | None) -> None:
        if not await self.repo.resolve(escalation_id, p.email, note):
            raise HandoffError("already resolved", 409)
        await self.repo.add_handoff_message(escalation_id, "system", "Оператортой яриа дууслаа. ТокТок туслах дахин хариулна.")
        await self.staff.audit(p.email, "handoff.resolve", escalation_id, {"note": note})
        await self.notifier.notify("handoff.resolved", {"escalation_id": escalation_id, "operator": p.email})

    async def user_left(self, escalation_id: str) -> bool:
        """The user started a new chat while waiting for / talking to an operator: close the request so it
        does not stay in the queue with replies nobody will see. No assignee is set if nobody claimed it."""
        if not await self.repo.resolve(escalation_id, None, "Хэрэглэгч шинэ яриа эхлүүлсэн"):
            return False
        await self.repo.add_handoff_message(escalation_id, "system",
                                            "Хэрэглэгч шинэ яриа эхлүүлж, энэ яриаг орхисон. Хүсэлт хаагдлаа.")
        await self.notifier.notify("handoff.user_left", {"escalation_id": escalation_id})
        return True

    async def transcript_for_bot(self, escalation_id: str) -> str:
        """Operator conversation, appended to the bot's history after handback."""
        e = await self.repo.get_escalation(escalation_id)
        msgs = await self.repo.list_handoff_messages(escalation_id)
        lines = [f"{m['sender']}: {m['text']}" for m in msgs if m["sender"] in ("user", "operator")]
        return (
            "<operator_handoff>\n"
            f"A human operator handled this conversation. Resolution note: {(e or {}).get('resolution') or '-'}\n"
            + "\n".join(lines[-30:])
            + "\n</operator_handoff>\nContinue helping the user; do not contradict what the operator decided."
        )
