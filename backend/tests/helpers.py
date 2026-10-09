"""Test doubles."""

from typing import Any

from app.llm.base import LLMError, ToolSpec, TurnResult
from app.routing.intents import RouteDecision


class ScriptedProvider:
    """Records what the agent receives; replies with canned text and optional tool calls.

    `classify` answers per schema name from `classify_results` (callable or object).
    """

    name = "scripted"
    supports_classify = True

    def __init__(self, reply: str = "OK", tool_calls: list[tuple[str, dict]] | None = None,
                 classify_results: dict[str, Any] | None = None, intent: str = "other"):
        self.reply = reply
        self.tool_calls = tool_calls or []
        self.classify_results = classify_results or {}
        self.intent = intent
        self.calls: list[dict[str, Any]] = []
        self.classify_calls: list[dict[str, Any]] = []
        self.completions: list[str] = []

    async def classify(self, *, system, user, schema, model=None, max_tokens=4096):
        self.classify_calls.append({"schema": schema.__name__, "system": system, "user": user, "model": model})
        if schema is RouteDecision:
            return RouteDecision(intent=self.intent, confidence=0.9, safety="ok", language="mn_cyrillic",
                                 normalized_query=user.split("<latest_message>\n")[-1].split("\n</latest")[0],
                                 needs_clarification=False)
        r = self.classify_results.get(schema.__name__)
        if r is None:
            raise LLMError(f"no scripted result for {schema.__name__}")
        return r(system, user) if callable(r) else r

    async def complete(self, *, system, user, max_tokens=1024):
        self.completions.append(user)
        return "КОНТЕКСТ: " + user[8:30]

    async def run_turn(self, *, system, history, user_text, turn_context, tools: list[ToolSpec], execute_tool, emit):
        self.calls.append({"system": system, "history": list(history), "user_text": user_text,
                           "turn_context": turn_context, "tools": [t.name for t in tools]})
        executed = []
        for name, args in self.tool_calls:
            out = await execute_tool(name, args)
            executed.append({"name": name, "input": args, "is_error": out.is_error, "output": out.content})
        emit("text", {"delta": self.reply})
        return TurnResult(text=self.reply, new_messages=[{"role": "user", "content": user_text},
                                                         {"role": "assistant", "content": self.reply}],
                          stop="end", model="scripted", tool_calls=executed)

    def trim_history(self, history, keep_turns):
        return history

    def reset_reasoning(self, history):
        return history
