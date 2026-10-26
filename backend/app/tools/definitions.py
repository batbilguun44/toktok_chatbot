"""Concrete tools available to the agent, per persona."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.tools.registry import Tool, ToolContext, ToolRegistry, confirmation_gate


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ common
class SearchKnowledgeIn(_Input):
    query: str = Field(description="Search query in Cyrillic Mongolian.", min_length=2, max_length=300)


async def search_knowledge(ctx: ToolContext, a: SearchKnowledgeIn):
    hits = await ctx.kb.search(a.query, ctx.user.persona, k=ctx.settings.retrieval_top_k)
    for h in hits:
        ctx.sources.setdefault(h.doc_id, h.doc_title)
    if not hits:
        return {"results": [], "note": "No verified document covers this. Do not guess; offer escalation."}
    return {"results": [{"doc_id": h.doc_id, "title": h.title, "text": h.text} for h in hits]}


class EscalateIn(_Input):
    category: Literal[
        "user_requested", "not_in_knowledge", "needs_human_decision", "incident", "complaint",
        "account_security", "sales_manager", "technical_issue",
    ]
    summary: str = Field(
        description="2-4 sentence summary for the operator, in Mongolian: the problem, what was "
        "already tried, relevant order/branch ids. No passwords or card numbers.",
        min_length=10, max_length=1200,
    )


# Escalation reasons, e.g. for the conversation-history filters.
ESCALATION_CATEGORIES: tuple[str, ...] = get_args(EscalateIn.model_fields["category"].annotation)


async def escalate_to_human(ctx: ToolContext, a: EscalateIn):
    if ctx.session.handoff_id:
        return {"status": "already_with_operator", "ticket_id": ctx.session.handoff_id}
    inchat = ctx.settings.handoff_mode == "inchat" and ctx.handoff is not None
    mode = "inchat" if inchat else "phone"
    ticket = await ctx.repo.create_escalation(
        ctx.session.session_id, ctx.user.persona, ctx.user_ref, a.category, a.summary, mode=mode
    )
    phone = ctx.settings.operator_phone(ctx.user.persona)
    ctx.session.escalated = True
    ctx.escalated.append(ticket)
    if inchat:
        ctx.session.handoff_id = ticket
        await ctx.handoff.started(await ctx.repo.get_escalation(ticket), ctx.session.recent[-12:])
    ctx.emit("escalation", {"ticket_id": ticket, "phone": phone, "category": a.category, "mode": mode})
    if inchat:
        note = ("An operator will continue in THIS chat. Tell the user briefly that they can keep writing "
                "here and an operator will reply; give the ticket number; the phone is only for urgent cases.")
    else:
        note = "Tell the user the ticket number and the phone number; an operator will follow up."
    return {"ticket_id": ticket, "operator_phone": phone, "mode": mode, "note": note}


# ---------------------------------------------------------------- customer
class NoInput(_Input):
    pass


class OrderIdIn(_Input):
    order_id: str = Field(description="Order id, e.g. O-24001.", pattern=r"^[A-Za-z]-?\d{3,10}$")


async def list_my_recent_orders(ctx: ToolContext, a: NoInput):
    orders = await ctx.gateway.list_orders(ctx.user.user_id, limit=5)
    return {"orders": [o.public() for o in orders]}


async def get_order_status(ctx: ToolContext, a: OrderIdIn):
    o = await ctx.gateway.get_order(ctx.user.user_id, a.order_id)
    return {"order": o.public()}


class ChangeAddressIn(_Input):
    order_id: str = Field(pattern=r"^[A-Za-z]-?\d{3,10}$")
    new_address: str = Field(min_length=5, max_length=300)
    user_confirmed: bool = Field(
        description="true only if the user explicitly confirmed this exact change in their latest message."
    )


async def change_delivery_address(ctx: ToolContext, a: ChangeAddressIn):
    o = await ctx.gateway.get_order(ctx.user.user_id, a.order_id)
    if not o.public()["address_changeable"]:
        return {"status": "not_allowed", "reason": "Order already picked up; only an operator can help.",
                "order": o.public()}
    gate = confirmation_gate(
        ctx, f"addr:{o.id}:{a.new_address.strip()}", f"{o.id} захиалгын хаягийг '{a.new_address}' болгох",
        a.user_confirmed,
    )
    if gate:
        return gate
    o = await ctx.gateway.change_address(ctx.user.user_id, a.order_id, a.new_address)
    return {"status": "done", "order": o.public()}


class SwitchFulfillmentIn(_Input):
    order_id: str = Field(pattern=r"^[A-Za-z]-?\d{3,10}$")
    to: Literal["delivery", "pickup"]
    user_confirmed: bool


async def switch_fulfillment_type(ctx: ToolContext, a: SwitchFulfillmentIn):
    o = await ctx.gateway.get_order(ctx.user.user_id, a.order_id)
    if o.fulfillment == a.to:
        return {"status": "no_change", "order": o.public()}
    if not o.public()["fulfillment_switchable"]:
        return {"status": "not_allowed", "reason": "Restaurant already started preparing.", "order": o.public()}
    label = "хүргүүлж авах" if a.to == "delivery" else "очиж авах"
    gate = confirmation_gate(ctx, f"ful:{o.id}:{a.to}", f"{o.id} захиалгыг '{label}' болгох", a.user_confirmed)
    if gate:
        return gate
    o = await ctx.gateway.switch_fulfillment(ctx.user.user_id, a.order_id, a.to)
    return {"status": "done", "order": o.public()}


# ------------------------------------------------------------------ driver
async def get_my_driver_profile(ctx: ToolContext, a: NoInput):
    return {"driver": await ctx.gateway.get_driver(ctx.user.user_id)}


async def get_my_active_orders(ctx: ToolContext, a: NoInput):
    return {"active_orders": await ctx.gateway.get_driver_active_orders(ctx.user.user_id)}


# ------------------------------------------------------------------ client
async def get_my_merchant_profile(ctx: ToolContext, a: NoInput):
    return {"merchant": await ctx.gateway.get_merchant(ctx.user.user_id)}


class SettlementIn(_Input):
    period: Literal["current", "previous"]


async def get_settlement_summary(ctx: ToolContext, a: SettlementIn):
    return {"settlement": await ctx.gateway.get_settlement(ctx.user.user_id, a.period)}


class MerchantRequestIn(_Input):
    request_type: Literal[
        "hours_change", "temporary_close", "reopen", "add_branch", "add_system_user",
        "bank_account_change", "promotion", "menu_change", "branch_visibility",
    ]
    branch_name: str | None = Field(default=None, max_length=120)
    details: str = Field(description="What exactly should change, in Mongolian.", min_length=5, max_length=1000)
    user_confirmed: bool


async def submit_merchant_request(ctx: ToolContext, a: MerchantRequestIn):
    if a.request_type == "bank_account_change":
        # Bank details are verified by a person with signed documents, never via chat.
        return {"status": "not_allowed_in_chat",
                "reason": "Bank account changes require signed documents; explain the documented "
                          "procedure from the knowledge base or escalate."}
    gate = confirmation_gate(
        ctx, f"req:{a.request_type}:{a.branch_name}:{a.details.strip()}",
        f"{a.request_type} хүсэлт ({a.branch_name or 'бүх салбар'}): {a.details}", a.user_confirmed,
    )
    if gate:
        return gate
    rid = await ctx.gateway.create_merchant_request(
        ctx.user.user_id, a.request_type, {"branch": a.branch_name, "details": a.details}
    )
    return {"status": "submitted", "request_id": rid,
            "note": "Partner team reviews requests; tell the user it is submitted, not completed."}


# ----------------------------------------------------------------- registry
ALL = frozenset({"driver", "client", "customer"})


# Tools that only use TokTok knowledge / the handoff queue (no personal data).
KNOWLEDGE_ONLY_TOOLS = frozenset({"search_knowledge", "escalate_to_human"})


def build_registry(personal_data: bool = True) -> ToolRegistry:
    """`personal_data=False` (GATEWAY_MODE=faq_only) leaves out every tool that reads or changes
    a user's orders, profile or settlement, so the model cannot offer or invent such data."""
    T = Tool
    tools = (
        [
            T("search_knowledge",
              "Search TokTok's verified knowledge base (rules, FAQs, how-to guides) for the current "
              "user type. Use for any policy, fee, procedure or app-usage question.",
              SearchKnowledgeIn, search_knowledge, ALL, "Мэдээлэл хайж байна…"),
            T("escalate_to_human",
              "Hand the conversation to a human operator: creates a support ticket and returns the "
              "operator phone. Use when the user asks for a person, when verified knowledge does "
              "not cover the question, or when a human decision is required.",
              EscalateIn, escalate_to_human, ALL, "Оператор руу шилжүүлж байна…"),
            T("list_my_recent_orders", "List the signed-in customer's 5 most recent orders.",
              NoInput, list_my_recent_orders, frozenset({"customer"}), "Захиалгуудыг шалгаж байна…"),
            T("get_order_status",
              "Get live status, ETA, address and items for one of the signed-in customer's orders.",
              OrderIdIn, get_order_status, frozenset({"customer"}), "Захиалгын төлөв шалгаж байна…"),
            T("change_delivery_address",
              "Change the delivery address of the customer's order (only before pickup). Requires "
              "explicit user confirmation in a separate message.",
              ChangeAddressIn, change_delivery_address, frozenset({"customer"}), "Хаяг шалгаж байна…"),
            T("switch_fulfillment_type",
              "Switch an order between delivery and pickup (only before preparation starts). Requires "
              "explicit user confirmation in a separate message.",
              SwitchFulfillmentIn, switch_fulfillment_type, frozenset({"customer"}), "Захиалга шалгаж байна…"),
            T("get_my_driver_profile",
              "Signed-in driver's status, zone, rating, onboarding steps, document and uniform-check state.",
              NoInput, get_my_driver_profile, frozenset({"driver"}), "Таны мэдээллийг шалгаж байна…"),
            T("get_my_active_orders", "Signed-in driver's currently assigned orders and their stage.",
              NoInput, get_my_active_orders, frozenset({"driver"}), "Идэвхтэй захиалгууд шалгаж байна…"),
            T("get_my_merchant_profile",
              "Signed-in merchant's branches, visibility in the customer app, opening hours, system users.",
              NoInput, get_my_merchant_profile, frozenset({"client"}), "Харилцагчийн мэдээлэл шалгаж байна…"),
            T("get_settlement_summary", "Signed-in merchant's settlement (тооцоо) for the current or previous period.",
              SettlementIn, get_settlement_summary, frozenset({"client"}), "Тооцоо шалгаж байна…"),
            T("submit_merchant_request",
              "File a change request to TokTok's partner team (hours, temporary close/reopen, new "
              "branch, new system user, promotion, menu change, branch visibility). Requires explicit "
              "user confirmation in a separate message. The team still reviews it.",
              MerchantRequestIn, submit_merchant_request, frozenset({"client"}), "Хүсэлт бүртгэж байна…"),
        ]
    )
    return ToolRegistry(tools if personal_data else [t for t in tools if t.name in KNOWLEDGE_ONLY_TOOLS])
