"""Core domain types shared across layers."""

from dataclasses import dataclass, field
from typing import Any, Literal

Persona = Literal["driver", "client", "customer"]
PERSONAS: tuple[Persona, ...] = ("driver", "client", "customer")


@dataclass(frozen=True)
class UserContext:
    """Identity of the person chatting, resolved from TokTok's own auth token.

    Tools read the user id from here, never from model output, so the model
    cannot be talked into fetching another user's orders or settlements.
    """

    persona: Persona
    user_id: str
    display_name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent:
    """One server-sent event pushed to the chat UI."""

    type: Literal[
        "meta",  # session / intent info
        "status",  # progress note ("searching knowledge", "checking order")
        "text",  # assistant text delta
        "tool",  # tool call started / finished
        "escalation",  # handed off to a human operator
        "sources",  # knowledge documents used
        "error",
        "done",
    ]
    data: dict[str, Any]
