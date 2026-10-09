"""Second-stage reranking for RAG mode (RERANK_PROVIDER=voyage)."""

import logging
from typing import Protocol

import httpx

from app.rag.knowledge_base import SearchHit

log = logging.getLogger(__name__)


class Reranker(Protocol):
    async def rerank(self, query: str, hits: list[SearchHit], k: int) -> list[SearchHit]: ...


class VoyageReranker:
    URL = "https://api.voyageai.com/v1/rerank"

    def __init__(self, api_key: str, model: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.model = model
        self._transport = transport

    async def rerank(self, query: str, hits: list[SearchHit], k: int) -> list[SearchHit]:
        if len(hits) <= 1:
            return hits[:k]
        try:
            async with httpx.AsyncClient(timeout=15, transport=self._transport) as c:
                r = await c.post(self.URL, headers={"Authorization": f"Bearer {self.api_key}"}, json={
                    "query": query, "model": self.model, "top_k": k,
                    "documents": [f"{h.title}\n{h.text}" for h in hits],
                })
                r.raise_for_status()
                data = r.json()["data"]
        except Exception as e:  # keep first-stage order rather than failing the turn
            log.warning("rerank failed, using first-stage order: %s", e)
            return hits[:k]
        out = []
        for d in sorted(data, key=lambda d: -d["relevance_score"])[:k]:
            h = hits[d["index"]]
            out.append(SearchHit(h.chunk_id, h.doc_id, h.doc_title, h.title, h.text,
                                 float(d["relevance_score"]), h.matched_by + ["rerank"]))
        return out
