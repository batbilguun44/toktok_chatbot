"""Deterministic offline provider (LLM_PROVIDER=fake).

For UI development and demos without an API key. It does not "understand"
anything: it echoes the top retrieved knowledge snippet, looks up the
customer's latest order for order questions, and escalates on request.
Never use it in production.
"""

import asyncio
import json
import re
from typing import Any

from app.llm.base import EventSink, LLMError, SchemaT, ToolExecutor, ToolSpec, TurnResult

_DOC_RE = re.compile(r'<doc id="([^"]+)" title="([^"]+)">\n(.*?)\n</doc>', re.DOTALL)
_INTENT_RE = re.compile(r"intent=(\w+)")


class FakeProvider:
    name = "fake"
    supports_classify = False

    async def classify(self, *, system: str, user: str, schema: type[SchemaT], model: str | None = None,
                       max_tokens: int = 4096) -> SchemaT:
        raise LLMError("fake provider has no classifier; keyword routing is used")

    async def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> str:
        return ""

    async def run_turn(
        self,
        *,
        system: str,
        history: list[dict[str, Any]],
        user_text: str,
        turn_context: str,
        tools: list[ToolSpec],
        execute_tool: ToolExecutor,
        emit: EventSink,
    ) -> TurnResult:
        names = {t.name for t in tools}
        m = _INTENT_RE.search(turn_context)
        intent = m.group(1) if m else "other"
        calls: list[dict[str, Any]] = []

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            emit("tool", {"name": name, "state": "start"})
            out = await execute_tool(name, args)
            calls.append({"name": name, "input": args, "is_error": out.is_error, "output": out.content[:1500]})
            return json.loads(out.content)

        if intent == "greeting":
            reply = "Сайн байна уу! TokTok туслах байна. Юугаар туслах вэ?"
        elif intent == "human_request" and "escalate_to_human" in names:
            r = await call("escalate_to_human", {"category": "user_requested",
                                                 "summary": f"Хэрэглэгч оператортой холбогдохыг хүссэн: {user_text}"})
            reply = f"Таны хүсэлтийг {r['ticket_id']} дугаартайгаар бүртгэлээ. Оператор: {r['operator_phone']}."
        elif intent in ("order_status", "order_eta") and "list_my_recent_orders" in names:
            orders = (await call("list_my_recent_orders", {}))["orders"]
            active = [o for o in orders if o["status"] not in ("delivered", "cancelled")]
            if not active:
                reply = "Танд одоогоор идэвхтэй захиалга алга байна."
            else:
                o = (await call("get_order_status", {"order_id": active[0]["order_id"]}))["order"]
                eta = f", ойролцоогоор {o['eta_minutes']} минутын дараа хүрнэ" if o.get("eta_minutes") else ""
                reply = f"{o['order_id']} ({o['restaurant']}) захиалгын төлөв: {o['status']}{eta}."
        else:
            docs = _DOC_RE.findall(turn_context)
            if docs:
                _, title, text = docs[0]
                reply = f"[DEMO] {title}\n\n{text[:600]}"
            else:
                reply = "[DEMO] Энэ талаар баталгаатай мэдээлэл олдсонгүй. Оператортой холбогдох уу?"

        for i in range(0, len(reply), 24):
            emit("text", {"delta": reply[i : i + 24]})
            await asyncio.sleep(0)
        return TurnResult(
            text=reply,
            new_messages=[{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}],
            stop="end",
            model="fake",
            tool_calls=calls,
        )

    def trim_history(self, history: list[dict[str, Any]], keep_turns: int) -> list[dict[str, Any]]:
        return history[-2 * keep_turns :]

    def reset_reasoning(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return history
