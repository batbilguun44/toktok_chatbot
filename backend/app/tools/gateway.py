"""Boundary to TokTok's own systems (orders, drivers, merchants, auth).

Replace `MockTokTokGateway` with an HTTP/DB-backed implementation once TokTok's
API spec is available; nothing else in the chatbot needs to change.
Every method takes the authenticated user's id, so ownership is checked here,
not by the model.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from app.domain import UserContext

OrderStatus = Literal[
    "placed", "accepted", "preparing", "ready_for_pickup", "on_the_way", "delivered", "cancelled"
]
ADDRESS_CHANGEABLE: set[str] = {"placed", "accepted", "preparing"}
FULFILLMENT_SWITCHABLE: set[str] = {"placed", "accepted"}


class GatewayError(Exception):
    """Business-rule failure whose message is safe to show to the user (Mongolian)."""


@dataclass
class Order:
    id: str
    customer_id: str
    restaurant: str
    status: OrderStatus
    fulfillment: Literal["delivery", "pickup"]
    address: str
    created_at: datetime
    eta_minutes: int | None = None
    items: list[str] = field(default_factory=list)
    driver_first_name: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "order_id": self.id,
            "restaurant": self.restaurant,
            "status": self.status,
            "fulfillment": self.fulfillment,
            "address": self.address,
            "created_at": self.created_at.isoformat(timespec="minutes"),
            "eta_minutes": self.eta_minutes,
            "items": self.items,
            "driver_first_name": self.driver_first_name,
            "address_changeable": self.status in ADDRESS_CHANGEABLE,
            "fulfillment_switchable": self.status in FULFILLMENT_SWITCHABLE,
        }


class TokTokGateway(Protocol):
    async def resolve_user(self, token: str) -> UserContext | None: ...

    # customer
    async def list_orders(self, customer_id: str, limit: int) -> list[Order]: ...
    async def get_order(self, customer_id: str, order_id: str) -> Order: ...
    async def change_address(self, customer_id: str, order_id: str, address: str) -> Order: ...
    async def switch_fulfillment(self, customer_id: str, order_id: str, to: str) -> Order: ...

    # driver
    async def get_driver(self, driver_id: str) -> dict[str, Any]: ...
    async def get_driver_active_orders(self, driver_id: str) -> list[dict[str, Any]]: ...

    # merchant (client)
    async def get_merchant(self, merchant_id: str) -> dict[str, Any]: ...
    async def get_settlement(self, merchant_id: str, period: str) -> dict[str, Any]: ...
    async def create_merchant_request(self, merchant_id: str, request_type: str, details: dict) -> str: ...


class MockTokTokGateway:
    """In-memory TokTok with demo accounts. Tokens: demo-customer, demo-driver,
    demo-driver-new, demo-client."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.users = {
            "demo-customer": UserContext("customer", "c-1001", "Болд"),
            "demo-driver": UserContext("driver", "d-501", "Ганбаа"),
            "demo-driver-new": UserContext("driver", "d-777", "Тэмүүлэн"),
            "demo-client": UserContext("client", "m-301", "Хаан Бууз ХХК"),
        }
        self.orders: dict[str, Order] = {
            o.id: o
            for o in [
                Order("O-24001", "c-1001", "Хаан Бууз (3,4 хороолол)", "on_the_way", "delivery",
                      "БЗД, 26-р хороо, 88-р байр, 2-р орц, 45 тоот", now - timedelta(minutes=28), 12,
                      ["Бууз x10", "Сүүтэй цай x2"], "Ганбаа"),
                Order("O-24002", "c-1001", "Pizza Hut (Зайсан)", "preparing", "delivery",
                      "ХУД, 11-р хороо, Зайсан хотхон 3-р байр", now - timedelta(minutes=6), 35,
                      ["Пепперони пицца (L)"], None),
                Order("O-23950", "c-1001", "KFC (Төв)", "delivered", "delivery",
                      "БЗД, 26-р хороо, 88-р байр", now - timedelta(days=1), None, ["Combo #2"], "Сараа"),
                Order("O-24100", "c-2002", "Other customer's order", "preparing", "delivery",
                      "СБД, 1-р хороо", now - timedelta(minutes=3), 30, ["Хуушуур x5"], None),
            ]
        }
        self.drivers = {
            "d-501": {
                "driver_id": "d-501", "first_name": "Ганбаа", "status": "online", "zone": "Сүхбаатар дүүрэг",
                "rating": 4.8, "deliveries_today": 7, "deliveries_total": 1342,
                "onboarding_completed": True, "documents": {"license": "valid", "vehicle_insurance": "expires_in_21_days"},
                "uniform_check": "passed_today",
            },
            "d-777": {
                "driver_id": "d-777", "first_name": "Тэмүүлэн", "status": "offline", "zone": None,
                "rating": None, "deliveries_today": 0, "deliveries_total": 0,
                "onboarding_completed": False, "pending_steps": ["training_video", "bag_pickup"],
                "documents": {"license": "valid", "vehicle_insurance": "missing"},
                "uniform_check": "not_done",
            },
        }
        self.driver_orders = {
            "d-501": [
                {"order_id": "O-24001", "stage": "delivering", "pickup": "Хаан Бууз (3,4 хороолол)",
                 "dropoff_area": "БЗД 26-р хороо", "customer_first_name": "Болд"},
                {"order_id": "O-24010", "stage": "heading_to_restaurant", "pickup": "Modern Nomads (Сөүл)",
                 "dropoff_area": "СБД 8-р хороо", "customer_first_name": "Нараа"},
            ],
        }
        self.merchants = {
            "m-301": {
                "merchant_id": "m-301", "name": "Хаан Бууз ХХК", "account_manager": "Б. Энхжин",
                "branches": [
                    {"name": "Хаан Бууз - 3,4 хороолол", "visible_in_app": True, "open_now": True,
                     "hours": "10:00-22:00"},
                    {"name": "Хаан Бууз - Зайсан", "visible_in_app": False, "open_now": False,
                     "hours": "10:00-21:00", "hidden_reason": "menu_not_approved"},
                ],
                "systems": {"client_system_users": 3, "order_system_devices": 2},
            }
        }
        self.settlements = {
            ("m-301", "current"): {"period": "2026-10-01..2026-10-15", "orders": 412, "gross_mnt": 9_870_000,
                                   "commission_mnt": 1_480_500, "payout_mnt": 8_389_500, "status": "accumulating",
                                   "payout_date": "2026-10-20"},
            ("m-301", "previous"): {"period": "2026-09-16..2026-09-30", "orders": 389, "gross_mnt": 9_120_000,
                                    "commission_mnt": 1_368_000, "payout_mnt": 7_752_000, "status": "paid",
                                    "payout_date": "2026-10-05", "invoice_status": "issued"},
        }
        self.merchant_requests: list[dict[str, Any]] = []

    async def resolve_user(self, token: str) -> UserContext | None:
        return self.users.get(token)

    async def list_orders(self, customer_id: str, limit: int) -> list[Order]:
        mine = [o for o in self.orders.values() if o.customer_id == customer_id]
        return sorted(mine, key=lambda o: o.created_at, reverse=True)[:limit]

    async def get_order(self, customer_id: str, order_id: str) -> Order:
        o = self.orders.get(order_id.strip().upper())
        # Same message whether the order is missing or belongs to someone else.
        if not o or o.customer_id != customer_id:
            raise GatewayError("Таны бүртгэлд ийм дугаартай захиалга олдсонгүй.")
        return o

    async def change_address(self, customer_id: str, order_id: str, address: str) -> Order:
        o = await self.get_order(customer_id, order_id)
        if o.fulfillment != "delivery":
            raise GatewayError("Энэ захиалга очиж авах төрлийнх тул хүргэлтийн хаяг байхгүй.")
        if o.status not in ADDRESS_CHANGEABLE:
            raise GatewayError("Хүргэгч захиалгыг авсан тул хаягийг системээр солих боломжгүй.")
        o.address = address
        return o

    async def switch_fulfillment(self, customer_id: str, order_id: str, to: str) -> Order:
        o = await self.get_order(customer_id, order_id)
        if o.status not in FULFILLMENT_SWITCHABLE:
            raise GatewayError("Ресторан хоолыг бэлтгэж эхэлсэн тул төрлийг солих боломжгүй.")
        o.fulfillment = to  # type: ignore[assignment]
        return o

    async def get_driver(self, driver_id: str) -> dict[str, Any]:
        return self.drivers[driver_id]

    async def get_driver_active_orders(self, driver_id: str) -> list[dict[str, Any]]:
        return self.driver_orders.get(driver_id, [])

    async def get_merchant(self, merchant_id: str) -> dict[str, Any]:
        return self.merchants[merchant_id]

    async def get_settlement(self, merchant_id: str, period: str) -> dict[str, Any]:
        s = self.settlements.get((merchant_id, period))
        if not s:
            raise GatewayError("Энэ хугацааны тооцооны мэдээлэл олдсонгүй.")
        return s

    async def create_merchant_request(self, merchant_id: str, request_type: str, details: dict) -> str:
        rid = f"REQ-{len(self.merchant_requests) + 5001}"
        self.merchant_requests.append({"id": rid, "merchant_id": merchant_id, "type": request_type, **details})
        return rid


_ANON_TOKEN = re.compile(r"^anon:(driver|client|customer):([A-Za-z0-9_-]{16,64})$")


class AnonymousGateway:
    """GATEWAY_MODE=faq_only: no TokTok API yet, so no personal data and no identity check.

    The host page (widget.js) sends "anon:<persona>:<random id>"; the random id only keeps one
    browser's conversation separate from another's. Personal-data tools are not registered in this
    mode, so the methods below are never reached by the model. `demo` lets local demo tokens
    (demo-driver, ...) keep working outside production.
    """

    def __init__(self, demo: MockTokTokGateway | None = None) -> None:
        self.demo = demo

    async def resolve_user(self, token: str) -> UserContext | None:
        m = _ANON_TOKEN.match(token)
        if m:
            return UserContext(m.group(1), f"anon-{m.group(2)}", None)  # type: ignore[arg-type]
        return await self.demo.resolve_user(token) if self.demo else None

    async def _unavailable(self, *args: Any, **kwargs: Any) -> Any:
        raise GatewayError("TokTok data is not connected yet (GATEWAY_MODE=faq_only)")

    list_orders = get_order = change_address = switch_fulfillment = _unavailable
    get_driver = get_driver_active_orders = get_merchant = get_settlement = _unavailable
    create_merchant_request = _unavailable
