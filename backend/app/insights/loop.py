"""Improvement loop: turn failed conversations into reviewed FAQ drafts.

1. Collect questions that went wrong (escalated, thumbs-down, judged ungrounded).
2. One model call clusters them by topic and checks each cluster against the
   current knowledge titles.
3. For uncovered topics the model drafts an FAQ. Facts it cannot know (fees,
   deadlines, phone numbers) are left as [ТОДРУУЛАХ: …] placeholders.
4. Drafts land in the CMS as normal drafts (source "ai-suggestion"); people
   edit, submit and approve them as usual. Nothing is published automatically.
"""

import logging
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.cms.service import CMSError, CMSService, DraftInput
from app.llm.base import LLMProvider
from app.memory.repository import Repository, new_id
from app.rag.knowledge_base import KnowledgeBase
from app.staff.auth import Principal

log = logging.getLogger(__name__)

_SYSTEM = """You analyse failed support conversations of TokTok, a Mongolian food-delivery platform,
to improve its knowledge base. Users are drivers, restaurant partners (client) or customers.

Group the questions into at most 15 topics (merge near-duplicates, including Latin-script Mongolian).
For each topic decide whether an existing knowledge document already covers it (give its id) or it
is a gap. For gaps, draft a short FAQ document in clear Cyrillic Mongolian:
- title: the question as users ask it
- body: Markdown with "## " sections; numbered steps where useful
- NEVER invent fees, amounts, deadlines, phone numbers, policies or app menu names. Write
  [ТОДРУУЛАХ: <what the Operation team must fill in>] instead.
- proposed_doc_id: lowercase latin slug starting with the persona, e.g. "customer-promo-code-expired".
If a covered topic still failed, explain in `notes` what the existing document is missing."""


class Cluster(BaseModel):
    persona: Literal["driver", "client", "customer"]
    topic: str
    turn_ids: list[str]
    covered_by_doc_id: str | None = Field(description="Existing doc id, or null for a gap.")
    proposed_doc_id: str | None
    proposed_title: str | None
    proposed_body: str | None
    notes: str


class InsightReport(BaseModel):
    clusters: list[Cluster]


async def run_insights(*, llm: LLMProvider, repo: Repository, cms: CMSService, kb: KnowledgeBase,
                       actor: Principal, model: str, days: int = 14, limit: int = 300) -> dict[str, Any]:
    qs = await repo.candidate_questions(time.time() - days * 86400, limit)
    run: dict[str, Any] = {"id": new_id("run"), "actor": actor.email, "clusters": [],
                           "stats": {"questions": len(qs), "days": days, "drafts": 0}}
    if not qs:
        await repo.save_insight_run(run)
        return run

    titles = "\n".join(f"- {d.id} [{d.persona}]: {d.title}" for d in sorted(kb.docs.values(), key=lambda d: d.id))
    lines = "\n".join(
        f'<q id="{q["turn_id"]}" persona="{q["persona"]}" reasons="{",".join(q["reasons"])}">'
        f'{q["user_text"][:400]}</q>' for q in qs
    )
    report = await llm.classify(
        system=_SYSTEM,
        user=f"<knowledge_documents>\n{titles}\n</knowledge_documents>\n\n<failed_questions>\n{lines}\n</failed_questions>",
        schema=InsightReport,
        model=model,
        max_tokens=16000,
    )

    valid_ids = {q["turn_id"] for q in qs}
    for c in report.clusters:
        c.turn_ids = [t for t in c.turn_ids if t in valid_ids]
        entry = c.model_dump() | {"count": len(c.turn_ids), "draft_created": False}
        if c.covered_by_doc_id is None and c.proposed_title and c.proposed_body and c.proposed_doc_id:
            doc_id = c.proposed_doc_id.lower().strip()
            try:
                existing = await cms.store.get_doc(doc_id)
                if not existing:
                    await cms.save_draft(
                        actor, doc_id,
                        DraftInput(persona=c.persona, title=c.proposed_title, body=c.proposed_body,
                                   tags=["ai-suggestion"]),
                        source="ai-suggestion",
                    )
                    entry["draft_created"] = True
                    run["stats"]["drafts"] += 1
            except CMSError as e:
                entry["notes"] += f" (draft not created: {e})"
        run["clusters"].append(entry)
    run["clusters"].sort(key=lambda c: -c["count"])
    await repo.save_insight_run(run)
    return run
