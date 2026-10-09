"""Online evaluation: an LLM judge grades a sample of production answers.

The judge checks groundedness (every factual claim supported by the knowledge
base or tool data), whether the question was actually answered, and tone.
Results feed the admin dashboard, the improvement loop and Langfuse scores.
"""

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.llm.base import LLMProvider

log = logging.getLogger(__name__)

_SYSTEM = """You audit answers of TokTok's Mongolian support chatbot.
Judge ONLY against the evidence provided (knowledge base excerpts and tool results); do not use
outside knowledge about TokTok. Generic politeness, clarifying questions and escalation offers
need no evidence.

- grounded: "yes" if every factual claim (fees, rules, steps, order data, times) is supported by the
  evidence; "partial" if some are; "no" if key claims are unsupported or contradicted; "n/a" if the
  answer makes no factual claims (greeting, clarifying question, pure escalation).
- answered: true if the user's actual question was resolved or correctly escalated.
- score: 1-5 overall quality for a support agent (accuracy first, then brevity and clarity)."""


class Judgement(BaseModel):
    grounded: Literal["yes", "partial", "no", "n/a"]
    answered: bool
    tone_ok: bool
    score: int = Field(description="1-5")
    unsupported_claims: list[str] = Field(description="Claims not supported by the evidence (quote briefly).")
    rationale: str = Field(description="One or two sentences.")


async def judge_turn(llm: LLMProvider, *, persona: str, question: str, answer: str, evidence: list[str],
                     tool_outputs: list[dict]) -> Judgement:
    tools = "\n".join(f"- {t['name']}: {t.get('output', '')[:1500]}" for t in tool_outputs) or "(none)"
    ev = "\n\n".join(evidence) or "(none)"
    user = (f"<persona>{persona}</persona>\n<question>{question}</question>\n<answer>{answer}</answer>\n"
            f"<knowledge_evidence>\n{ev}\n</knowledge_evidence>\n<tool_results>\n{tools}\n</tool_results>")
    j = await llm.classify(system=_SYSTEM, user=user, schema=Judgement)
    j.score = max(1, min(5, j.score))
    return j


def to_record(j: Judgement, model: str) -> dict:
    return {**json.loads(j.model_dump_json()), "model": model}
