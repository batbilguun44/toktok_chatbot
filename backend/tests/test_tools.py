import json

import pytest

from app.memory.session import SessionState
from app.tools.registry import ToolContext


@pytest.fixture
async def ctx_factory(container):
    async def make(token: str, turn_index: int = 0, session: SessionState | None = None):
        user = await container.services.gateway.resolve_user(token)
        session = session or SessionState("s_test12345", user.persona, user.user_id, "fake")
        session.turn_index = turn_index
        events = []
        ctx = ToolContext(
            user=user, session=session, gateway=container.services.gateway, repo=container.services.repo,
            kb=container.services.kb, settings=container.settings, user_ref="ref",
            emit=lambda k, d: events.append((k, d)), sources={}, escalated=[],
        )
        return ctx, events

    return make


async def run(container, ctx, name, args):
    out = await container.services.tools.execute(ctx, name, args)
    return out, json.loads(out.content)


async def test_customer_cannot_read_other_customers_order(container, ctx_factory):
    ctx, _ = await ctx_factory("demo-customer")
    out, body = await run(container, ctx, "get_order_status", {"order_id": "O-24100"})
    assert out.is_error and "олдсонгүй" in body["error"]
    out, body = await run(container, ctx, "get_order_status", {"order_id": "O-24001"})
    assert not out.is_error and body["order"]["status"] == "on_the_way"


async def test_tools_are_persona_scoped(container, ctx_factory):
    ctx, _ = await ctx_factory("demo-driver")
    out, _ = await run(container, ctx, "get_order_status", {"order_id": "O-24001"})
    assert out.is_error
    names = {t.name for t in container.services.tools.for_persona("driver")}
    assert "get_order_status" not in names and "get_my_driver_profile" in names


async def test_invalid_or_truncated_input_never_runs(container, ctx_factory):
    ctx, _ = await ctx_factory("demo-customer")
    out, body = await run(container, ctx, "get_order_status", {"order_id": "O-24001", "extra": 1})
    assert out.is_error and "INVALID_INPUT" in body
    out, body = await run(container, ctx, "get_order_status", {})
    assert out.is_error and "INVALID_INPUT" in body


async def test_address_change_requires_confirmation_in_a_later_turn(container, ctx_factory):
    ctx, _ = await ctx_factory("demo-customer", turn_index=3)
    args = {"order_id": "O-24002", "new_address": "ХУД, 11-р хороо, 5-р байр", "user_confirmed": True}
    # Model claims confirmation in the same turn it first proposes: refused.
    _, body = await run(container, ctx, "change_delivery_address", args)
    assert body["status"] == "confirmation_required"
    _, body = await run(container, ctx, "change_delivery_address", args)
    assert body["status"] == "confirmation_required"
    # Next user turn: allowed.
    ctx.session.turn_index = 4
    _, body = await run(container, ctx, "change_delivery_address", args)
    assert body["status"] == "done" and body["order"]["address"] == args["new_address"]


async def test_address_change_blocked_after_pickup(container, ctx_factory):
    ctx, _ = await ctx_factory("demo-customer")
    _, body = await run(container, ctx, "change_delivery_address",
                        {"order_id": "O-24001", "new_address": "Шинэ хаяг 12", "user_confirmed": False})
    assert body["status"] == "not_allowed"


async def test_bank_account_change_is_never_done_in_chat(container, ctx_factory):
    ctx, _ = await ctx_factory("demo-client", turn_index=5)
    _, body = await run(container, ctx, "submit_merchant_request", {
        "request_type": "bank_account_change", "details": "Хаан банк 5000...", "user_confirmed": True})
    assert body["status"] == "not_allowed_in_chat"


async def test_escalation_creates_ticket_and_emits_event(container, ctx_factory):
    ctx, events = await ctx_factory("demo-driver")
    _, body = await run(container, ctx, "escalate_to_human",
                        {"category": "incident", "summary": "Хүргэгч бага зэргийн осолд орсон, захиалга O-24001."})
    assert body["ticket_id"].startswith("esc_") and body["operator_phone"] == container.settings.operator_phone_driver
    assert ("escalation", {"ticket_id": body["ticket_id"], "phone": body["operator_phone"], "category": "incident"}) in events
    assert ctx.session.escalated
    rows = await container.services.repo.list_escalations("open", 10)
    assert rows[0]["category"] == "incident"
