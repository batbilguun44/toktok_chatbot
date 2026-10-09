"""Claude implementation of `LLMProvider` (official Anthropic Python SDK).

Design notes
- History is append-only. Claude Opus 5.5 binds each thinking block to the exact
  conversation prefix before it, so we never rewrite earlier turns; per-turn
  facts (retrieved knowledge, routing hints) are appended as a mid-conversation
  `role: "system"` message right after the user's message.
- `system` and `tools` are fixed per persona, which also keeps the prompt cache warm.
- Refusals: server-side `fallbacks="default"` re-runs a declined request on the
  recommended fallback model inside the same call.
- Tools stream eagerly; every tool input is validated (pydantic) by the executor
  before it runs, and tools are never run on a `max_tokens` / `refusal` stop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic

from app.config import Settings
from app.llm.base import EventSink, LLMError, SchemaT, ToolExecutor, ToolSpec, TurnResult

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Blocks the model produced before a mid-output fallback that must not be echoed back.
_PRE_FALLBACK_DROP = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
_KNOWN_BLOCKS = {"text", "thinking", "redacted_thinking", "tool_use"}

REFUSAL_TEXT = (
    "Уучлаарай, энэ асуултад би хариулах боломжгүй байна. "
    "Шаардлагатай бол оператортой холбогдоно уу."
)
MAX_ITER_TEXT = "Уучлаарай, хүсэлтийг бүрэн боловсруулж чадсангүй. Оператортой холбогдоно уу."


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.client = client or anthropic.AsyncAnthropic()

    # ------------------------------------------------------------------ classify
    async def classify(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        try:
            resp = await self.client.messages.parse(
                model=self.settings.classifier_model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": "low"},
            )
        except anthropic.APIError as e:
            raise LLMError(f"classifier call failed: {e}") from e
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise LLMError(f"classifier returned no parsed output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

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
        messages: list[dict[str, Any]] = list(history)
        messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
        if turn_context:
            messages.append({"role": "system", "content": turn_context})
        first_new = len(history)

        tool_defs = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "eager_input_streaming": True,
            }
            for t in sorted(tools, key=lambda t: t.name)
        ]

        texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        model = s.agent_model
        json_retries = 0
        iteration = 0

        while True:
            try:
                final = await self._stream_once(system, messages, tool_defs, emit)
            except anthropic.APIError as e:
                raise LLMError(str(e)) from e
            except ValueError as e:
                # Eagerly streamed tool input that the SDK could not parse at all.
                # There is no tool_use id to answer, so re-issue the request.
                json_retries += 1
                log.warning("unparseable streamed tool input (%s), retry %d", e, json_retries)
                if json_retries > 1:
                    raise LLMError("model produced unparseable tool input twice") from e
                continue

            model = final.model
            _add_usage(usage, final.usage)
            content = _sanitize_after_fallback([_to_param(b) for b in final.content])
            turn_text = "".join(b["text"] for b in content if b["type"] == "text")
            if turn_text.strip():
                texts.append(turn_text.strip())

            stop = final.stop_reason
            if stop == "refusal":
                category = getattr(final.stop_details, "category", None) if final.stop_details else None
                messages.append(_assistant(content, drop_tool_use=True, fallback_text=REFUSAL_TEXT))
                if not turn_text:
                    emit("text", {"delta": REFUSAL_TEXT})
                    texts.append(REFUSAL_TEXT)
                return self._result(texts, messages, first_new, "refusal", model, tool_calls, usage, category)

            if stop == "max_tokens":
                # A tool_use cut off mid-input may still parse; never run it.
                messages.append(_assistant(content, drop_tool_use=True, fallback_text=MAX_ITER_TEXT))
                return self._result(texts, messages, first_new, "max_tokens", model, tool_calls, usage)

            if stop == "tool_use":
                iteration += 1
                if iteration >= s.agent_max_iterations:
                    messages.append(_assistant(content, drop_tool_use=True, fallback_text=MAX_ITER_TEXT))
                    emit("text", {"delta": MAX_ITER_TEXT})
                    texts.append(MAX_ITER_TEXT)
                    return self._result(texts, messages, first_new, "max_iterations", model, tool_calls, usage)

                messages.append({"role": "assistant", "content": content})
                uses = [b for b in content if b["type"] == "tool_use"]
                outcomes = await asyncio.gather(*(execute_tool(u["name"], u.get("input") or {}) for u in uses))
                results = []
                for u, out in zip(uses, outcomes):
                    tool_calls.append({"name": u["name"], "input": u.get("input"), "is_error": out.is_error})
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": u["id"],
                            "content": out.content,
                            **({"is_error": True} if out.is_error else {}),
                        }
                    )
                # All results of one assistant turn go back in a single user message.
                messages.append({"role": "user", "content": results})
                continue

            if stop == "pause_turn":
                messages.append({"role": "assistant", "content": content})
                continue

            messages.append(_assistant(content, drop_tool_use=True, fallback_text=REFUSAL_TEXT))
            return self._result(texts, messages, first_new, "end", model, tool_calls, usage)

    async def _stream_once(self, system: str, messages: list, tool_defs: list, emit: EventSink):
        s = self.settings
        kwargs: dict[str, Any] = {
            "model": s.agent_model,
            "max_tokens": s.agent_max_tokens,
            "system": [{"type": "text", "text": system}],
            "messages": messages,
            "output_config": {"effort": s.agent_effort},
            "cache_control": {"type": "ephemeral"},
        }
        if tool_defs:
            kwargs["tools"] = tool_defs

        if s.llm_refusal_fallbacks:
            stream_ctx = self.client.beta.messages.stream(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            stream_ctx = self.client.messages.stream(**kwargs)

        async with stream_ctx as stream:
            async for event in stream:
                if event.type == "text":
                    emit("text", {"delta": event.text})
                elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                    emit("tool", {"name": event.content_block.name, "state": "start"})
            return await stream.get_final_message()

    @staticmethod
    def _result(texts, messages, first_new, stop, model, tool_calls, usage, category=None) -> TurnResult:
        return TurnResult(
            text="\n\n".join(texts).strip(),
            new_messages=messages[first_new:],
            stop=stop,
            model=model,
            tool_calls=tool_calls,
            usage=usage,
            refusal_category=category,
        )

    # -------------------------------------------------------------- trim_history
    def trim_history(self, history: list[dict[str, Any]], keep_turns: int) -> list[dict[str, Any]]:
        """Keep the last `keep_turns` user turns.

        Dropping earlier turns changes the prefix every later thinking block was
        bound to, so all thinking blocks are stripped at this one boundary; the
        conversation stays append-only from here on.
        """
        starts = [i for i, m in enumerate(history) if _is_user_turn_start(m)]
        if len(starts) <= keep_turns:
            return history
        kept = history[starts[-keep_turns]:]
        out = []
        for m in kept:
            if m["role"] == "assistant" and isinstance(m["content"], list):
                blocks = [b for b in m["content"] if b.get("type") not in ("thinking", "redacted_thinking")]
                m = {"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]}
            out.append(m)
        return out


# ---------------------------------------------------------------------- helpers
def _to_param(block: Any) -> dict[str, Any]:
    return block.model_dump(mode="json", by_alias=True, exclude_none=True)


def _sanitize_after_fallback(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the echo rules for a response that switched model mid-output.

    Before the last `fallback` marker only text blocks are echoed; the marker
    itself is an audit block and is dropped. Unknown block types are dropped too.
    """
    last = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
    out = []
    for i, b in enumerate(content):
        t = b.get("type")
        if t == "fallback" or t not in _KNOWN_BLOCKS:
            continue
        if i < last and t in _PRE_FALLBACK_DROP:
            continue
        out.append(b)
    return out


def _assistant(content: list[dict[str, Any]], *, drop_tool_use: bool, fallback_text: str) -> dict[str, Any]:
    """Final assistant message for the stored history.

    Unanswered tool_use blocks would make the next request invalid, so they are
    removed; a message must also end with visible text.
    """
    blocks = [b for b in content if not (drop_tool_use and b["type"] == "tool_use")]
    if not any(b["type"] == "text" and b["text"].strip() for b in blocks):
        blocks.append({"type": "text", "text": fallback_text})
    return {"role": "assistant", "content": blocks}


def _is_user_turn_start(m: dict[str, Any]) -> bool:
    if m["role"] != "user":
        return False
    c = m["content"]
    if isinstance(c, str):
        return True
    return any(b.get("type") == "text" for b in c)


def _add_usage(acc: dict[str, int], usage: Any) -> None:
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        v = getattr(usage, k, None)
        if isinstance(v, int):
            acc[k] = acc.get(k, 0) + v
