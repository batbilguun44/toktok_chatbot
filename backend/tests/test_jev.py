import httpx
import pytest

from app.routing.jev import API_URL, build_request, jev_route


def _answers(intent="delivery_fee"):
    return {
        "model": "jev-1.13.0",
        "answers": {
            "intent": {"type": "choice", "choice": intent, "probabilities": {intent: 0.9}, "confidence": 0.8},
            "safety": {"type": "choice", "choice": "ok", "probabilities": {"ok": 1.0}, "confidence": 0.99},
            "language": {"type": "choice", "choice": "mn_latin", "probabilities": {"mn_latin": 1.0}, "confidence": 0.9},
            "needs_clarification": {"type": "noul", "noul": 0.1},
            "needs_strong": {"type": "noul", "noul": 0.2},
            "frustration": {"type": "score", "score": 0.3, "legend": {}, "probabilities": {}, "confidence": 0.9},
        },
        "usage": {"input_tokens": 300, "output_tokens": 20},
    }


def test_request_uses_persona_intents():
    req = build_request("client", "tootsoo buruu", [("user", "sain uu")], "jev-latest")
    assert req["model"] == "jev-latest"
    assert "settlement" in req["questions"]["intent"]["criteria"]
    assert "delivery_fee" not in req["questions"]["intent"]["criteria"]
    assert req["state"]["recent_conversation"] == [{"role": "user", "text": "sain uu"}]
    assert req["questions"]["frustration"]["criteria"] == ["Calm", "Frustrated", "Very angry"]


@pytest.mark.asyncio
async def test_jev_route_maps_answers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        return httpx.Response(200, json=_answers())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        r = await jev_route(c, "k", "driver", "tolbor yaaj bodogddog ve", [])
    assert seen == {"url": API_URL, "auth": "Bearer k"}
    assert r.decision.intent == "delivery_fee" and r.decision.confidence == 0.8
    assert r.decision.language == "mn_latin" and r.decision.normalized_query == "tolbor yaaj bodogddog ve"
    assert not r.decision.needs_clarification and r.needs_strong == 0.2


@pytest.mark.asyncio
async def test_unknown_intent_becomes_other():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=_answers("settlement")))) as c:
        r = await jev_route(c, "k", "driver", "x", [])
    assert r.decision.intent == "other"
