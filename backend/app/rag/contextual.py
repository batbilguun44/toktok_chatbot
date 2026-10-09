"""Contextual Retrieval: describe each chunk's place in its document before indexing.

A chunk like "2. Батарей хэмнэх горимыг унтраана" is hard to match on its own;
prefixed with "Order апп-д захиалга notification-оор ирэхгүй үеийн алхам" it is
found by both keyword and vector search. Results are cached by document +
chunk content, so only edited documents cost API calls.
"""

import hashlib
import logging
from dataclasses import replace

from app.llm.base import LLMProvider
from app.rag.documents import Chunk, KnowledgeDoc

log = logging.getLogger(__name__)

_SYSTEM = """You help index a Mongolian food-delivery company's support knowledge base.
Given the whole document and one chunk of it, write ONE short sentence in Cyrillic Mongolian
that situates the chunk within the document (topic + who it is for + situation), to improve
search retrieval. Output only that sentence.

<document title="{title}" audience="{persona}">
{body}
</document>"""


class Contextualizer:
    def __init__(self, llm: LLMProvider, cache: dict[str, str] | None = None):
        self.llm = llm
        self.cache: dict[str, str] = cache if cache is not None else {}

    async def apply(self, doc: KnowledgeDoc, chunks: list[Chunk]) -> list[Chunk]:
        if len(chunks) <= 1:  # a single-chunk doc is its own context
            return chunks
        system = _SYSTEM.format(title=doc.title, persona=doc.persona, body=doc.body)
        doc_key = hashlib.sha256(system.encode()).hexdigest()[:16]
        out = []
        for c in chunks:
            key = f"{doc_key}:{hashlib.sha256(c.text.encode()).hexdigest()[:16]}"
            ctx = self.cache.get(key)
            if ctx is None:
                try:
                    ctx = (await self.llm.complete(system=system, user=f"<chunk>\n{c.text}\n</chunk>",
                                                   max_tokens=512))[:300]
                    self.cache[key] = ctx
                except Exception as e:  # index without context now; retried on the next rebuild
                    log.warning("contextualization failed for %s: %s", c.id, e)
                    ctx = ""
            out.append(replace(c, context=ctx))
        return out
