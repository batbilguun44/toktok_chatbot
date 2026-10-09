"""Provider-neutral LLM interface.

The orchestrator only talks to `LLMProvider`. Claude is the production
implementation (`anthropic_provider.py`); another vendor can be added by
implementing the same two methods. Conversation history is stored in the
provider's own wire format (it carries provider-specific blocks such as
Claude's thinking signatures), so a session stays on the provider it started on.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the provider cannot produce a response (network, quota, 5xx)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False


# (tool_name, raw_input) -> result. Validation and auth scoping live behind this.
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[ToolOutcome]]
# Receives UI events: ("text", {"delta": ...}), ("tool", {...}), ("status", {...})
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass
class TurnResult:
    text: str
    # Messages to append to the stored history, in provider wire format.
    new_messages: list[dict[str, Any]]
    stop: Literal["end", "refusal", "max_iterations", "max_tokens", "error"]
    model: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    refusal_category: str | None = None


class LLMProvider(Protocol):
    name: str

    async def classify(self, *, system: str, user: str, schema: type[SchemaT], model: str | None = None,
                       max_tokens: int = 4096) -> SchemaT:
        """Single structured-output call (routing, judging, clustering). Defaults to the fast model."""
        ...

    async def complete(self, *, system: str, user: str, max_tokens: int = 1024) -> str:
        """Small plain-text generation with the fast model (indexing helpers)."""
        ...

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
        """Run one user turn, including any tool-use loop, streaming text via `emit`.

        `system` must be identical for every turn of a session (prompt cache and
        thinking-block validity depend on it); per-turn facts go in `turn_context`.
        """
        ...

    def trim_history(self, history: list[dict[str, Any]], keep_turns: int) -> list[dict[str, Any]]:
        """Shorten a long history at a turn boundary in a provider-safe way."""
        ...

    def reset_reasoning(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop provider reasoning state bound to the old system prompt (one-time recovery)."""
        ...
