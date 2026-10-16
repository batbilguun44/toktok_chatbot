"""GATEWAY_MODE=faq_only: phase 1 before TokTok's API — no personal data, anonymous users."""

import pytest

from app.container import _check_prod, build_container
from app.tools.gateway import AnonymousGateway, GatewayError
from tests.conftest import collect
from tests.helpers import ScriptedProvider

ANON = "anon:driver:0123456789abcdef0123456789abcdef"


async def test_only_knowledge_and_escalation_tools_are_offered(settings):
    llm = ScriptedProvider()
    c = await build_container(settings.model_copy(update={"gateway_mode": "faq_only"}), llm=llm)
    user = await c.services.gateway.resolve_user(ANON)
    assert user.persona == "driver" and user.user_id.startswith("anon-") and user.display_name is None
    await collect(c.chat.stream_turn(user, None, "Надад хэдэн захиалга байна?"))
    call = llm.calls[-1]
    assert sorted(call["tools"]) == ["escalate_to_human", "search_knowledge"]
    assert "Account data is not connected yet" in call["system"]
    await c.aclose()


async def test_mock_mode_keeps_account_tools(settings):
    llm = ScriptedProvider()
    c = await build_container(settings, llm=llm)
    user = await c.services.gateway.resolve_user("demo-driver")
    await collect(c.chat.stream_turn(user, None, "Надад хэдэн захиалга байна?"))
    assert "get_my_active_orders" in llm.calls[-1]["tools"]
    assert "Account data is not connected yet" not in llm.calls[-1]["system"]
    await c.aclose()


async def test_anonymous_tokens_are_validated_and_separate():
    gw = AnonymousGateway()
    for bad in ["anon:driver:short", "anon:boss:0123456789abcdef0123", "demo-driver", "anon:driver:<script>xxxxxxxxxxxx"]:
        assert await gw.resolve_user(bad) is None
    a = await gw.resolve_user(ANON)
    b = await gw.resolve_user("anon:driver:ffffffffffffffffffffffffffffffff")
    assert a.user_id != b.user_id  # two browsers never share a conversation
    with pytest.raises(GatewayError):
        await gw.get_driver_active_orders("d-501")


async def test_demo_tokens_still_work_outside_production(settings):
    c = await build_container(settings.model_copy(update={"gateway_mode": "faq_only"}), llm=ScriptedProvider())
    assert (await c.services.gateway.resolve_user("demo-client")).persona == "client"
    await c.aclose()


def test_production_refuses_fake_data(settings):
    prod = settings.model_copy(update={
        "env": "prod", "admin_api_key": "k" * 40, "user_ref_salt": "s" * 40, "staff_session_secret": "x" * 40,
        "oidc_issuer": "https://login.example", "oidc_client_id": "id", "oidc_client_secret": "secret",
        "llm_provider": "gemini", "database_url": "postgresql://x", "redis_url": "redis://x",
    })
    with pytest.raises(RuntimeError, match="GATEWAY_MODE=mock"):
        _check_prod(prod)
    _check_prod(prod.model_copy(update={"gateway_mode": "faq_only"}))  # allowed


def test_ip_limit_stops_rotating_anonymous_ids(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    for k, v in {"LLM_PROVIDER": "fake", "DATABASE_URL": "", "REDIS_URL": "", "QDRANT_URL": "",
                 "GATEWAY_MODE": "faq_only", "RATE_LIMIT_PER_MINUTE": "100", "RATE_LIMIT_PER_IP_PER_MINUTE": "3",
                 "STAFF_SESSION_SECRET": "s" * 40, "JUDGE_SAMPLE_RATE": "0"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        codes = []
        for i in range(4):  # a new anonymous id every time, same IP
            token = f"anon:driver:{i:032d}"
            r = client.post("/api/chat/stream", json={"message": "Сайн байна уу"},
                            headers={"Authorization": f"Bearer {token}"})
            codes.append("rate_limited" in r.text)
        assert codes == [False, False, False, True]
    get_settings.cache_clear()
