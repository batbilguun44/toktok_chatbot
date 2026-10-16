"""Knowledge quality checks for editors: near-duplicate warnings and AI contradiction review.

- Similarity compares a draft with every published or in-progress document of the same user type
  (plus "all") on phonetic word keys, so Cyrillic / Latin spellings match and no AI call is needed.
- Contradictions are checked on demand with the classifier model against the most similar documents
  only; it flags differing amounts, deadlines, periods or procedures and never decides which is right.
"""

import math
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.guardrails.translit import key_tokens
from app.llm.base import LLMProvider

SIMILAR_MIN = 0.30      # below this documents are considered different topics
CONFLICT_CHECK_MAX = 3  # most similar documents sent to the model


@dataclass
class Candidate:
    doc_id: str
    title: str
    body: str
    persona: str
    state: str  # published | draft | in_review | rejected
    author: str = ""


def _vec(text: str) -> Counter:
    return Counter(t for t in key_tokens(text) if len(t) > 2)


def similarity(a: str, b: str) -> float:
    va, vb = _vec(a), _vec(b)
    if not va or not vb:
        return 0.0
    dot = sum(n * vb[t] for t, n in va.items())
    return dot / (math.sqrt(sum(n * n for n in va.values())) * math.sqrt(sum(n * n for n in vb.values())))


def find_similar(persona: str, title: str, body: str, candidates: list[Candidate], exclude_id: str | None = None,
                 limit: int = 5) -> list[dict]:
    """Documents that likely cover the same thing as the draft (highest first)."""
    text = f"{title}\n{title}\n{body}"  # the title says what the document is about: weigh it twice
    best: dict[str, dict] = {}  # a document may have a published and a draft version: keep the closer one
    for c in candidates:
        if c.doc_id == exclude_id or (persona != "all" and c.persona not in (persona, "all")):
            continue
        s = similarity(text, f"{c.title}\n{c.title}\n{c.body}")
        if s >= SIMILAR_MIN and s > best.get(c.doc_id, {}).get("score", 0):
            best[c.doc_id] = {"doc_id": c.doc_id, "title": c.title, "persona": c.persona, "state": c.state,
                              "author": c.author, "score": round(s, 2)}
    return sorted(best.values(), key=lambda x: x["score"], reverse=True)[:limit]


class Conflict(BaseModel):
    other_doc_id: str = Field(description="id of the existing document that disagrees")
    new_statement: str = Field(description="short quote from the NEW document")
    existing_statement: str = Field(description="short quote from the EXISTING document")
    explanation: str = Field(description="one sentence in Mongolian: what differs")


class ConflictReport(BaseModel):
    conflicts: list[Conflict]


_CONFLICT_SYSTEM = """\
You review the knowledge base of TokTok, a Mongolian food-delivery platform. Compare the NEW document
with each EXISTING document and list only real contradictions about the same thing: different amounts,
fees, percentages, deadlines, time periods, opening hours, phone numbers, required steps or rules.
Do not report differences in wording, extra details that only one document mentions, or placeholders
such as "[X₮]". Quote short statements exactly. Write the explanation in Mongolian.
Return an empty list when the documents agree."""


async def find_conflicts(llm: LLMProvider, title: str, body: str, others: list[Candidate]) -> list[dict]:
    if not others:
        return []
    existing = "\n\n".join(
        f'<existing id="{c.doc_id}" title="{c.title}">\n{c.body[:6000]}\n</existing>' for c in others)
    user = f'<new title="{title}">\n{body[:6000]}\n</new>\n\n{existing}'
    report = await llm.classify(system=_CONFLICT_SYSTEM, user=user, schema=ConflictReport)
    known = {c.doc_id: c.title for c in others}
    return [{**c.model_dump(), "other_title": known[c.other_doc_id]}
            for c in report.conflicts if c.other_doc_id in known]
