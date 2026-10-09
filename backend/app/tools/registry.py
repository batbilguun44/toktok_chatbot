"""Tool registry: schema generation, input validation, auth-scoped execution."""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.domain import Persona, UserContext
from app.llm.base import ToolOutcome, ToolSpec
from app.memory.repository import Repository
from app.memory.session import SessionState
from app.rag.knowledge_base import KnowledgeBase
from app.tools.gateway import GatewayError, TokTokGateway

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    user: UserContext
    session: SessionState
    gateway: TokTokGateway
    repo: Repository
    kb: KnowledgeBase
    settings: Settings
    user_ref: str
    emit: Callable[[str, dict[str, Any]], None]
    # Filled by tools so the orchestrator can report sources / escalation.
    sources: list[str]
    escalated: list[str]


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Handler
    personas: frozenset[Persona]
    status_text: str  # progress note shown in the UI while the tool runs

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {t.name: t for t in tools}

    def for_persona(self, persona: Persona) -> list[Tool]:
        return sorted((t for t in self._tools.values() if persona in t.personas), key=lambda t: t.name)

    def specs(self, persona: Persona) -> list[ToolSpec]:
        return [t.spec() for t in self.for_persona(persona)]

    async def execute(self, ctx: ToolContext, name: str, raw_input: dict[str, Any]) -> ToolOutcome:
        tool = self._tools.get(name)
        if not tool or ctx.user.persona not in tool.personas:
            return ToolOutcome(_dump({"error": f"unknown tool {name}"}), is_error=True)
        try:
            args = tool.input_model.model_validate(raw_input)
        except ValidationError as e:
            # Covers truncated / malformed eagerly-streamed input: never run it.
            return ToolOutcome(
                _dump({"INVALID_INPUT": raw_input, "errors": e.errors(include_url=False)}), is_error=True
            )
        ctx.emit("status", {"text": tool.status_text, "tool": name})
        try:
            result = await tool.handler(ctx, args)
            ctx.emit("tool", {"name": name, "state": "done"})
            return ToolOutcome(_dump(result))
        except GatewayError as e:
            ctx.emit("tool", {"name": name, "state": "failed"})
            return ToolOutcome(_dump({"error": str(e)}), is_error=True)
        except Exception:
            log.exception("tool %s crashed", name)
            ctx.emit("tool", {"name": name, "state": "failed"})
            return ToolOutcome(
                _dump({"error": "Системийн алдаа гарлаа. Хэрэглэгчийг оператортой холбоно уу."}), is_error=True
            )


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ------------------------------------------------------------- confirmations
def confirmation_gate(ctx: ToolContext, key: str, summary: str, user_confirmed: bool) -> dict[str, Any] | None:
    """Two-step confirmation for state-changing actions.

    The first call registers a pending action and tells the model to ask the
    user. The action only runs on a call with user_confirmed=true in a *later*
    user turn, so the model cannot confirm on the user's behalf in one turn.
    Returns None when the action may proceed.
    """
    pending: dict[str, Any] = ctx.session.scratch.setdefault("pending_actions", {})
    p = pending.get(key)
    if user_confirmed and p and p["turn"] < ctx.session.turn_index:
        del pending[key]
        return None
    pending[key] = {"turn": ctx.session.turn_index, "summary": summary}
    return {
        "status": "confirmation_required",
        "instruction": "Do not claim the change is done. Ask the user to confirm exactly this, "
        "then call again with user_confirmed=true only after they agree in their next message.",
        "summary": summary,
    }
