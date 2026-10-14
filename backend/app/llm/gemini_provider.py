"""Google Gemini implementation of `LLMProvider` (REST API via httpx).

Intended for free-tier testing (Google AI Studio key, no card); Claude stays the
production provider.

Design notes
- History is stored in Gemini's wire format (`contents`: role user/model, parts).
  Model parts are echoed back verbatim, so thought signatures stay valid.
- Gemini has no mid-conversation system role: per-turn facts (`turn_context`)
  ride along as a second part of the user's message.
- Schemas (structured output, tool parameters) are sent as plain JSON Schema
  (`responseJsonSchema` / `parametersJsonSchema`) with `$ref`s inlined.
- Free-tier rate limits are low; a 429 is retried a couple of times.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.llm.base import EventSink, LLMError, SchemaT, ToolExecutor, ToolSpec, TurnResult

log = logging.getLogger(__name__)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_ITER_TEXT = "Уучлаарай, хүсэлтийг бүрэн боловсруулж чадсангүй. Оператортой холбогдоно уу."
BLOCKED_TEXT = (
    "Уучлаарай, энэ асуултад би хариулах боломжгүй байна. "
    "Шаардлагатай бол оператортой холбогдоно уу."
)
_RETRY_DELAY_RE = re.compile(r"(\d+(?:\.\d+)?)s")
# Statuses worth trying GEMINI_FALLBACK_MODELS for (overload / quota / server error).
_TRANSIENT = (429, 500, 503)


class _Overloaded(Exception):
    """A model stayed unavailable after retries; the next fallback model may still work."""


class GeminiProvider:
    name = "gemini"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini requires GEMINI_API_KEY")
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=API_BASE,
            headers={"x-goog-api-key": settings.gemini_api_key},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    # ------------------------------------------------------------------ classify
    async def classify(self, *, system: str, user: str, schema: type[SchemaT], model: str | None = None,
                       max_tokens: int = 4096) -> SchemaT:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": _inline_refs(schema.model_json_schema()),
            },
        }
        data = await self._post(model or self.settings.classifier_model, body)
        text = _text_of(_first_candidate(data))
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            raise LLMError(f"classifier returned invalid JSON: {e}") from e

    async def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        data = await self._post(self.settings.classifier_model, body)
        return _text_of(_first_candidate(data)).strip()

    # ------------------------------------------------------------------ run_turn
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
        s = self.settings
        contents: list[dict[str, Any]] = list(history)
        user_parts: list[dict[str, Any]] = [{"text": user_text}]
        if turn_context:
            user_parts.append({"text": f"<turn_context>\n{turn_context}\n</turn_context>"})
        contents.append({"role": "user", "parts": user_parts})
        first_new = len(history)

        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {"maxOutputTokens": s.agent_max_tokens,
                                 **_thinking(s.agent_model, s.agent_effort)},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description,
                 "parametersJsonSchema": _inline_refs(t.input_schema)}
                for t in sorted(tools, key=lambda t: t.name)
            ]}]

        texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        model = s.agent_model
        iteration = 0

        while True:
            parts, finish, model = await self._stream_once({**body, "contents": contents}, emit, usage)
            turn_text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            if turn_text.strip():
                texts.append(turn_text.strip())
            calls = [p["functionCall"] for p in parts if "functionCall" in p]

            if finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"):
                contents.append(_model_msg(parts, fallback_text=BLOCKED_TEXT, drop_calls=True))
                if not turn_text:
                    emit("text", {"delta": BLOCKED_TEXT})
                    texts.append(BLOCKED_TEXT)
                return self._result(texts, contents, first_new, "refusal", model, tool_calls, usage)

            if finish == "MAX_TOKENS":
                contents.append(_model_msg(parts, fallback_text=MAX_ITER_TEXT, drop_calls=True))
                return self._result(texts, contents, first_new, "max_tokens", model, tool_calls, usage)

            if calls:
                iteration += 1
                if iteration >= s.agent_max_iterations:
                    contents.append(_model_msg(parts, fallback_text=MAX_ITER_TEXT, drop_calls=True))
                    emit("text", {"delta": MAX_ITER_TEXT})
                    texts.append(MAX_ITER_TEXT)
                    return self._result(texts, contents, first_new, "max_iterations", model, tool_calls, usage)

                contents.append({"role": "model", "parts": parts})
                outcomes = await asyncio.gather(*(execute_tool(c["name"], c.get("args") or {}) for c in calls))
                responses = []
                for c, out in zip(calls, outcomes):
                    tool_calls.append({"name": c["name"], "input": c.get("args"), "is_error": out.is_error,
                                       "output": out.content[:1500]})
                    try:
                        payload = json.loads(out.content)
                    except ValueError:
                        payload = out.content
                    if not isinstance(payload, dict):
                        payload = {"result": payload}
                    if out.is_error:
                        payload = {"error": payload}
                    fr: dict[str, Any] = {"name": c["name"], "response": payload}
                    if c.get("id"):
                        fr["id"] = c["id"]
                    responses.append({"functionResponse": fr})
                # All responses of one model turn go back in a single user message.
                contents.append({"role": "user", "parts": responses})
                continue

            contents.append(_model_msg(parts, fallback_text=BLOCKED_TEXT, drop_calls=True))
            return self._result(texts, contents, first_new, "end", model, tool_calls, usage)

    async def _stream_once(self, body: dict[str, Any], emit: EventSink,
                           usage: dict[str, int]) -> tuple[list[dict[str, Any]], str | None, str]:
        models = [self.settings.agent_model, *self.settings.gemini_fallback_models]
        for i, model in enumerate(models):
            try:
                return await self._stream_model(model, body, emit, usage)
            except _Overloaded as e:
                if i + 1 == len(models):
                    raise LLMError(str(e)) from e
                log.warning("gemini %s overloaded, falling back to %s", model, models[i + 1])
        raise AssertionError("unreachable")

    async def _stream_model(self, model: str, body: dict[str, Any], emit: EventSink,
                            usage: dict[str, int]) -> tuple[list[dict[str, Any]], str | None, str]:
        url = f"/models/{model}:streamGenerateContent"
        for attempt in range(3):
            parts: list[dict[str, Any]] = []
            finish: str | None = None
            meta: dict[str, Any] | None = None
            async with self.client.stream("POST", url, params={"alt": "sse"}, json=body) as resp:
                if resp.status_code != 200:
                    err = (await resp.aread()).decode("utf-8", "replace")
                    delay = _retry_delay(resp.status_code, err, attempt)
                    if delay is not None:
                        log.warning("gemini %s, retrying in %.0fs", resp.status_code, delay)
                        await asyncio.sleep(delay)
                        continue
                    if resp.status_code in _TRANSIENT:
                        raise _Overloaded(f"gemini HTTP {resp.status_code}: {err[:300]}")
                    raise LLMError(f"gemini HTTP {resp.status_code}: {err[:500]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = json.loads(line[5:])
                    meta = chunk.get("usageMetadata") or meta
                    model = chunk.get("modelVersion", model)
                    cand = (chunk.get("candidates") or [{}])[0]
                    for p in (cand.get("content") or {}).get("parts") or []:
                        if "text" in p and not p.get("thought"):
                            emit("text", {"delta": p["text"]})
                        elif "functionCall" in p:
                            emit("tool", {"name": p["functionCall"]["name"], "state": "start"})
                        _merge_part(parts, p)
                    finish = cand.get("finishReason") or finish
            _add_usage(usage, meta)
            return parts, finish, model
        raise _Overloaded("gemini: retries exhausted")

    async def _post(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        models = [model, *self.settings.gemini_fallback_models]
        for i, m in enumerate(models):
            try:
                return await self._post_model(m, body)
            except _Overloaded as e:
                if i + 1 == len(models):
                    raise LLMError(str(e)) from e
                log.warning("gemini %s overloaded, falling back to %s", m, models[i + 1])
        raise AssertionError("unreachable")

    async def _post_model(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            try:
                resp = await self.client.post(f"/models/{model}:generateContent", json=body)
            except httpx.HTTPError as e:
                raise LLMError(f"gemini request failed: {e}") from e
            if resp.status_code == 200:
                return resp.json()
            delay = _retry_delay(resp.status_code, resp.text, attempt)
            if delay is None:
                if resp.status_code in _TRANSIENT:
                    raise _Overloaded(f"gemini HTTP {resp.status_code}: {resp.text[:300]}")
                raise LLMError(f"gemini HTTP {resp.status_code}: {resp.text[:500]}")
            log.warning("gemini %s, retrying in %.0fs", resp.status_code, delay)
            await asyncio.sleep(delay)
        raise _Overloaded("gemini: retries exhausted")

    @staticmethod
    def _result(texts, contents, first_new, stop, model, tool_calls, usage) -> TurnResult:
        return TurnResult(
            text="\n\n".join(texts).strip(),
            new_messages=contents[first_new:],
            stop=stop,
            model=model,
            tool_calls=tool_calls,
            usage=usage,
        )

    # -------------------------------------------------------------- trim_history
    def trim_history(self, history: list[dict[str, Any]], keep_turns: int) -> list[dict[str, Any]]:
        """Keep the last `keep_turns` user turns (a turn starts at a user text message)."""
        starts = [i for i, m in enumerate(history)
                  if m["role"] == "user" and any("text" in p for p in m.get("parts", []))]
        if len(starts) <= keep_turns:
            return history
        return history[starts[-keep_turns]:]

    def reset_reasoning(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for m in history:
            if m["role"] == "model":
                parts = [{k: v for k, v in p.items() if k != "thoughtSignature"}
                         for p in m.get("parts", []) if not p.get("thought")]
                m = {"role": "model", "parts": parts or [{"text": "…"}]}
            out.append(m)
        return out


# ---------------------------------------------------------------------- helpers
def _first_candidate(data: dict[str, Any]) -> dict[str, Any]:
    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise LLMError(f"gemini returned no candidates (blockReason={reason})")
    return cands[0]


def _text_of(cand: dict[str, Any]) -> str:
    parts = (cand.get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _merge_part(parts: list[dict[str, Any]], p: dict[str, Any]) -> None:
    """Concatenate streamed text deltas; keep function calls and signatures as separate parts."""
    if parts and "text" in p and "text" in parts[-1] and set(p) <= {"text"} \
            and set(parts[-1]) <= {"text"}:
        parts[-1] = {"text": parts[-1]["text"] + p["text"]}
    else:
        parts.append(dict(p))


def _model_msg(parts: list[dict[str, Any]], *, fallback_text: str, drop_calls: bool) -> dict[str, Any]:
    """Final model message for stored history: unanswered calls removed, visible text guaranteed."""
    kept = [p for p in parts if not (drop_calls and "functionCall" in p)]
    if not any(p.get("text", "").strip() and not p.get("thought") for p in kept):
        kept.append({"text": fallback_text})
    return {"role": "model", "parts": kept}


def _thinking(model: str, effort: str) -> dict[str, Any]:
    """AGENT_EFFORT -> Gemini 3 thinking level (Gemini 2.x models use a token budget instead)."""
    if model.startswith("gemini-2"):
        return {}
    level = {"low": "low", "medium": "medium"}.get(effort, "high")
    return {"thinkingConfig": {"thinkingLevel": level}}


def _retry_delay(status: int, body: str, attempt: int) -> float | None:
    """Seconds to wait before retrying the same model, or None to give up on it
    (the caller then tries GEMINI_FALLBACK_MODELS). A user is waiting, so a quota
    reset longer than a few seconds is not worth waiting for."""
    if status not in _TRANSIENT or attempt >= 2:
        return None
    m = _RETRY_DELAY_RE.search(body) if status == 429 else None
    if m:
        wait = float(m.group(1)) + 1
        return wait if wait <= 8 else None
    return 2.0 * (attempt + 1)


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local `$ref`s (`#/$defs/...`) so the schema is self-contained."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/"):
                target = defs.get(ref.rsplit("/", 1)[-1], {})
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**target, **extra})
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "definitions")}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _add_usage(acc: dict[str, int], meta: dict[str, Any] | None) -> None:
    """Add one request's final usageMetadata (streamed chunks report running totals)."""
    if not meta:
        return
    for src, dst in (("promptTokenCount", "input_tokens"), ("candidatesTokenCount", "output_tokens"),
                     ("cachedContentTokenCount", "cache_read_input_tokens")):
        if isinstance(meta.get(src), int):
            acc[dst] = acc.get(dst, 0) + meta[src]
