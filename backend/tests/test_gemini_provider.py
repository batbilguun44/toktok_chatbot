"""GeminiProvider against a mocked REST API (no network)."""

import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm.base import ToolOutcome, ToolSpec
from app.llm.gemini_provider import GeminiProvider


def _sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks).encode()


def _provider(handler) -> tuple[GeminiProvider, list[dict]]:
    seen: list[dict] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append({"url": str(req.url), "body": json.loads(req.content)})
        return handler(req, len(seen))

    s = Settings(llm_provider="gemini", gemini_api_key="test", agent_model="gemini-x", classifier_model="gemini-y")
    client = httpx.AsyncClient(base_url="https://g.test/v1beta", transport=httpx.MockTransport(wrapped))
    return GeminiProvider(s, client=client), seen


class Pick(BaseModel):
    intent: str
    confidence: float


@pytest.mark.asyncio
async def test_classify_parses_json():
    def handler(req, n):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": '{"intent": "order_status", "confidence": 0.9}'}]}}]})

    p, seen = _provider(handler)
    out = await p.classify(system="s", user="u", schema=Pick)
    assert out.intent == "order_status"
    assert seen[0]["url"].endswith("/models/gemini-y:generateContent")
    assert seen[0]["body"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.asyncio
async def test_run_turn_tool_loop():
    def handler(req, n):
        if n == 1:
            return httpx.Response(200, content=_sse(
                {"candidates": [{"content": {"role": "model", "parts": [
                    {"functionCall": {"name": "list_my_recent_orders", "args": {}}, "thoughtSignature": "sig"}]},
                    "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}}))
        return httpx.Response(200, content=_sse(
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "Таны захиалга "}]}}]},
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "замдаа явж байна."}]},
                             "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5}}))

    p, seen = _provider(handler)
    events: list = []

    async def execute(name, args):
        return ToolOutcome(content=json.dumps({"orders": [{"order_id": "A1"}]}))

    res = await p.run_turn(
        system="sys", history=[], user_text="zahialga haana", turn_context="intent=order_status",
        tools=[ToolSpec("list_my_recent_orders", "list", {"type": "object", "properties": {}})],
        execute_tool=execute, emit=lambda k, d: events.append((k, d)),
    )
    assert res.stop == "end"
    assert res.text == "Таны захиалга замдаа явж байна."
    assert [c["name"] for c in res.tool_calls] == ["list_my_recent_orders"]
    assert res.usage == {"input_tokens": 30, "output_tokens": 8}
    # Second request echoes the model's call (with signature) and the function response.
    contents = seen[1]["body"]["contents"]
    assert contents[1]["parts"][0]["thoughtSignature"] == "sig"
    assert contents[2]["parts"][0]["functionResponse"]["response"]["orders"][0]["order_id"] == "A1"
    assert [m["role"] for m in res.new_messages] == ["user", "model", "user", "model"]
    assert ("tool", {"name": "list_my_recent_orders", "state": "start"}) in events


@pytest.mark.asyncio
async def test_falls_back_when_model_overloaded(monkeypatch):
    monkeypatch.setattr("app.llm.gemini_provider.asyncio.sleep", lambda s: _noop())

    def handler(req, n):
        if "gemini-x" in str(req.url):
            return httpx.Response(503, json={"error": {"code": 503, "message": "high demand"}})
        return httpx.Response(200, content=_sse(
            {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}, "finishReason": "STOP"}],
             "modelVersion": "gemini-lite"}))

    p, seen = _provider(handler)
    p.settings.gemini_fallback_models = ["gemini-lite"]
    res = await p.run_turn(system="s", history=[], user_text="hi", turn_context="", tools=[],
                           execute_tool=None, emit=lambda k, d: None)
    assert res.text == "OK" and res.model == "gemini-lite"
    assert sum("gemini-x" in s["url"] for s in seen) == 3  # retried, then fell back


async def _noop():
    return None
