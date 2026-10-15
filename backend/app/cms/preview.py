"""Answer preview: how would the bot answer with this draft published?

Builds a throw-away knowledge base (published docs + the draft overlay) and
runs the real agent with only the knowledge-search tool, so editors can test
a change before it reaches users.
"""

from typing import Any

from app.cms.store import DocVersion
from app.domain import UserContext
from app.memory.session import SessionState
from app.orchestrator.graph import Services, _turn_context
from app.personas.prompts import system_prompt
from app.rag.knowledge_base import KnowledgeBase, LocalHybridIndex
from app.routing.router import route
from app.tools.registry import ToolContext, ToolRegistry


async def preview_answer(svc: Services, published, persona: str, question: str,
                         overlay: DocVersion | None, instructions: str | None = None) -> dict[str, Any]:
    """`instructions` previews a draft of the operation-team instructions (default: published)."""
    docs = {d.id: d for d in published}
    if overlay:
        docs[overlay.doc_id] = overlay.to_doc()
    s = svc.settings
    kb = KnowledgeBase(LocalHybridIndex(), mode=s.knowledge_mode, full_max_tokens=s.full_context_max_tokens)
    await kb.rebuild(list(docs.values()))
    mode = kb.mode_for(persona)
    pack = kb.packs[persona] if mode == "full" else None
    hits = [] if pack else await kb.search(question, persona, k=s.retrieval_top_k)

    user = UserContext(persona, "preview", "Preview")  # type: ignore[arg-type]
    session = SessionState("s_preview_000", persona, "preview", svc.llm.name)
    tools = ToolRegistry([t for t in svc.tools.for_persona(persona) if t.name == "search_knowledge"])
    sources: dict[str, str] = {h.doc_id: h.doc_title for h in hits}
    ctx = ToolContext(user=user, session=session, gateway=svc.gateway, repo=svc.repo, kb=kb, settings=s,
                      user_ref="preview", emit=lambda k, d: None, sources=sources, escalated=[])
    # Same router as live chat, so the detected language / intent hints match what users get.
    decision, _ = await route(svc.llm, persona, question, [])  # type: ignore[arg-type]
    state = {"decision": decision, "user": user, "flags": [], "hits": hits, "pack": pack, "kb_updates": ""}
    if instructions is None:
        instructions = svc.instructions.get(persona)
    system = system_prompt(persona, instructions, s.gateway_mode != "faq_only") + ("\n\n## Verified knowledge base\n" + pack.text if pack else "")
    result = await svc.llm.run_turn(
        system=system, history=[], user_text=question, turn_context=_turn_context(state),
        tools=tools.specs(persona), execute_tool=lambda n, i: tools.execute(ctx, n, i), emit=lambda k, d: None,
    )
    return {
        "answer": result.text, "mode": mode, "stop": result.stop, "model": result.model,
        "sources": [{"id": i, "title": t} for i, t in sources.items()],
        "overlay": {"doc_id": overlay.doc_id, "version": overlay.version} if overlay else None,
    }
