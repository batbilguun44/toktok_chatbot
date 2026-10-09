"""Runs AnthropicProvider against the real SDK with a mocked HTTP transport."""

import json

import anthropic
import httpx2
import pytest

from app.config import Settings
from app.llm.anthropic_provider import AnthropicProvider, _sanitize_after_fallback
from app.llm.base import LLMError, ToolOutcome, ToolSpec
from app.routing.intents import RouteDecision


def sse(events: list[dict]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


def message_stream(blocks: list[dict], stop_reason: str, model: str = "claude-opus-5-5", stop_details=None) -> bytes:
    ev = [{"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 50, "output_tokens": 1}}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            ev.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            ev.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}})
        elif b["type"] == "thinking":
            ev.append({"type": "content_block_start", "index": i,
                       "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            ev.append({"type": "content_block_delta", "index": i,
                       "delta": {"type": "signature_delta", "signature": b["signature"]}})
        elif b["type"] == "tool_use":
            ev.append({"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            ev.append({"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(b["input"], ensure_ascii=False)}})
        ev.append({"type": "content_block_stop", "index": i})
    delta = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details:
        delta["stop_details"] = stop_details
    ev.append({"type": "message_delta", "delta": delta, "usage": {"output_tokens": 20}})
    ev.append({"type": "message_stop"})
    return sse(ev)


class MockAPI:
    def __init__(self, responses: list[bytes | dict]):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        body = self.responses.pop(0)
        if isinstance(body, dict):
            return httpx2.Response(200, json=body)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    def body(self, i: int) -> dict:
        return json.loads(self.requests[i].content)


def provider(api: MockAPI, **overrides) -> AnthropicProvider:
    s = Settings(_env_file=None, **overrides)
    client = anthropic.AsyncAnthropic(
        api_key="test", base_url="http://anthropic.test", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(api.handler)),
    )
    return AnthropicProvider(s, client)


TOOLS = [ToolSpec("get_order_status", "Get order", {
    "type": "object", "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"], "additionalProperties": False})]


async def test_tool_loop_streams_text_and_keeps_history_append_only():
    api = MockAPI([
        message_stream([{"type": "thinking", "signature": "sig-1"}, {"type": "text", "text": "Шалгая. "},
                        {"type": "tool_use", "id": "toolu_1", "name": "get_order_status",
                         "input": {"order_id": "O-24001"}}], "tool_use"),
        message_stream([{"type": "text", "text": "Таны захиалга замд явж байна."}], "end_turn"),
    ])
    p = provider(api)
    events, executed = [], []

    async def execute(name, raw):
        executed.append((name, raw))
        return ToolOutcome(json.dumps({"status": "on_the_way"}))

    history = [{"role": "user", "content": [{"type": "text", "text": "сайн уу"}]},
               {"role": "assistant", "content": [{"type": "text", "text": "Сайн байна уу!"}]}]
    result = await p.run_turn(system="SYS", history=history, user_text="захиалга хаана?",
                              turn_context="<turn_context>x</turn_context>", tools=TOOLS,
                              execute_tool=execute, emit=lambda k, d: events.append((k, d)))

    assert executed == [("get_order_status", {"order_id": "O-24001"})]
    assert result.stop == "end"
    assert result.text == "Шалгая.\n\nТаны захиалга замд явж байна."
    assert "".join(d["delta"] for k, d in events if k == "text") == "Шалгая. Таны захиалга замд явж байна."
    assert ("tool", {"name": "get_order_status", "state": "start"}) in events

    # Request shape
    req0, req1 = api.body(0), api.body(1)
    assert req0["model"] == "claude-opus-5-5"
    assert req0["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in api.requests[0].headers["anthropic-beta"]
    assert req0["output_config"] == {"effort": "low"}
    assert req0["cache_control"] == {"type": "ephemeral"}
    assert req0["tools"][0]["eager_input_streaming"] is True
    assert "thinking" not in req0  # Opus 5.5: thinking is always on; never send disabled
    # Turn context is a mid-conversation system message right after the user message.
    assert [m["role"] for m in req0["messages"]] == ["user", "assistant", "user", "system"]
    # Second request is the first one plus appended turns (prefix unchanged).
    assert req1["messages"][: len(req0["messages"])] == req0["messages"]
    assistant_turn = req1["messages"][4]
    assert assistant_turn["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    assert req1["messages"][5]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"status": "on_the_way"}'}]
    assert req1["system"] == req0["system"] and req1["tools"] == req0["tools"]

    # new_messages = everything after the given history, ending with the final answer.
    assert [m["role"] for m in result.new_messages] == ["user", "system", "assistant", "user", "assistant"]
    assert result.usage["output_tokens"] == 40


async def test_refusal_does_not_run_tools_and_stores_valid_turn():
    api = MockAPI([message_stream(
        [{"type": "tool_use", "id": "toolu_9", "name": "get_order_status", "input": {"order_id": "O-1"}}],
        "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})])
    p = provider(api)
    ran = []

    async def execute(name, raw):
        ran.append(name)
        return ToolOutcome("{}")

    r = await p.run_turn(system="S", history=[], user_text="x", turn_context="", tools=TOOLS,
                         execute_tool=execute, emit=lambda k, d: None)
    assert ran == []
    assert r.stop == "refusal" and r.refusal_category == "cyber"
    last = r.new_messages[-1]
    assert last["role"] == "assistant"
    assert all(b["type"] != "tool_use" for b in last["content"])  # no unanswered tool_use
    assert last["content"][-1]["type"] == "text"


async def test_fallbacks_can_be_disabled_for_platforms_without_them():
    api = MockAPI([message_stream([{"type": "text", "text": "ok"}], "end_turn")])
    p = provider(api, llm_refusal_fallbacks=False)
    await p.run_turn(system="S", history=[], user_text="x", turn_context="", tools=[],
                     execute_tool=None, emit=lambda k, d: None)
    assert "fallbacks" not in api.body(0)
    assert "anthropic-beta" not in api.requests[0].headers


async def test_api_errors_become_llm_error():
    def boom(request):
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})

    client = anthropic.AsyncAnthropic(api_key="t", base_url="http://anthropic.test", max_retries=0,
                                      http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(boom)))
    p = AnthropicProvider(Settings(_env_file=None), client)
    with pytest.raises(LLMError):
        await p.run_turn(system="S", history=[], user_text="x", turn_context="", tools=[],
                         execute_tool=None, emit=lambda k, d: None)


async def test_classify_uses_structured_output_with_classifier_model():
    decision = {"intent": "order_status", "confidence": 0.93, "safety": "ok", "language": "mn_latin",
                "normalized_query": "захиалга хаана явж байна", "needs_clarification": False}
    api = MockAPI([{
        "id": "msg_c", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": json.dumps(decision, ensure_ascii=False)}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 5}}])
    p = provider(api)
    out = await p.classify(system="S", user="zahialga haana", schema=RouteDecision)
    assert out.intent == "order_status" and out.normalized_query.startswith("захиалга")
    body = api.body(0)
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "low"


def test_sanitize_after_mid_output_fallback():
    content = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "partial "},
        {"type": "tool_use", "id": "t", "name": "x", "input": {}},
        {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "rest"},
    ]
    assert _sanitize_after_fallback(content) == [
        {"type": "text", "text": "partial "},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "rest"},
    ]


def test_trim_history_cuts_at_turn_boundary_and_strips_thinking():
    p = AnthropicProvider(Settings(_env_file=None), client=object())
    h = []
    for i in range(4):
        h += [
            {"role": "user", "content": [{"type": "text", "text": f"q{i}"}]},
            {"role": "system", "content": "ctx"},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                                              {"type": "tool_use", "id": f"t{i}", "name": "x", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": "{}"}]},
            {"role": "assistant", "content": [{"type": "text", "text": f"a{i}"}]},
        ]
    trimmed = p.trim_history(h, keep_turns=2)
    assert trimmed[0]["content"][0]["text"] == "q2"
    assert len(trimmed) == 10
    assert not any(b.get("type") == "thinking" for m in trimmed if isinstance(m["content"], list) for b in m["content"])
    assert p.trim_history(h, keep_turns=10) is h
