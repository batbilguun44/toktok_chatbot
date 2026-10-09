"""Hybrid retrieval: BM25 (script-agnostic phonetic keys) + vectors, fused with RRF."""

import asyncio
import logging
import math
from collections import Counter
from dataclasses import dataclass

from app.guardrails.translit import key_tokens
from app.rag.documents import Chunk, KnowledgeDoc, chunk_doc
from app.rag.embeddings import Embedder
from app.rag.vector import VectorIndex

log = logging.getLogger(__name__)

RRF_K = 60
_PREFIX = 5  # crude stemming for agglutinative Mongolian: захиалга/захиалгаа/захиалгын


@dataclass
class SearchHit:
    chunk_id: str
    doc_id: str
    title: str
    text: str
    score: float
    matched_by: list[str]


def _features(text: str) -> list[str]:
    toks = key_tokens(text)
    return toks + [f"{t[:_PREFIX]}~" for t in toks if len(t) > _PREFIX]


class BM25:
    def __init__(self, k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: list[tuple[str, Counter, int]] = []
        self.df: Counter = Counter()
        self.avgdl = 0.0

    def build(self, items: list[tuple[str, str]]) -> None:
        self.docs, self.df = [], Counter()
        for cid, text in items:
            feats = _features(text)
            tf = Counter(feats)
            self.docs.append((cid, tf, len(feats)))
            self.df.update(tf.keys())
        self.avgdl = sum(n for _, _, n in self.docs) / max(len(self.docs), 1)

    def search(self, query: str, allowed: set[str], k: int) -> list[tuple[str, float]]:
        q = set(_features(query))
        n = len(self.docs)
        out = []
        for cid, tf, dl in self.docs:
            if cid not in allowed:
                continue
            s = 0.0
            for term in q:
                f = tf.get(term)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
            if s > 0:
                out.append((cid, s))
        return sorted(out, key=lambda x: -x[1])[:k]


class KnowledgeBase:
    def __init__(self, embedder: Embedder | None, vectors: VectorIndex | None):
        self.embedder = embedder
        self.vectors = vectors if embedder else None
        self.chunks: dict[str, Chunk] = {}
        self.docs: dict[str, KnowledgeDoc] = {}
        self.bm25 = BM25()
        self._lock = asyncio.Lock()

    async def rebuild(self, docs: list[KnowledgeDoc]) -> dict[str, int]:
        """(Re)index all public docs. Only chunks whose content changed are re-embedded."""
        async with self._lock:
            public = [d for d in docs if d.visibility != "internal"]
            chunks = [c for d in public for c in chunk_doc(d)]
            self.docs = {d.id: d for d in public}
            self.chunks = {c.id: c for c in chunks}
            self.bm25.build([(c.id, f"{c.title}\n{c.text}") for c in chunks])
            embedded = 0
            if self.embedder and self.vectors:
                known = await self.vectors.known_hashes()
                stale = [c for c in chunks if known.get(c.id) != c.content_hash]
                if stale:
                    vecs = await self.embedder.embed([f"{c.title}\n{c.text}" for c in stale], "document")
                    await self.vectors.upsert(stale, vecs)
                    embedded = len(stale)
                await self.vectors.retain(set(self.chunks))
            log.info("knowledge indexed: %d docs, %d chunks, %d embedded", len(public), len(chunks), embedded)
            return {"docs": len(public), "chunks": len(chunks), "embedded": embedded}

    async def search(self, query: str, persona: str, k: int = 5) -> list[SearchHit]:
        personas = [persona, "all"]
        allowed = {cid for cid, c in self.chunks.items() if c.persona in personas}
        if not allowed or not query.strip():
            return []
        ranked: dict[str, list[tuple[int, str]]] = {}
        for rank, (cid, _) in enumerate(self.bm25.search(query, allowed, k=20)):
            ranked.setdefault(cid, []).append((rank, "bm25"))
        if self.embedder and self.vectors:
            try:
                qv = (await self.embedder.embed([query], "query"))[0]
                for rank, (cid, _) in enumerate(await self.vectors.search(qv, personas, k=20)):
                    if cid in self.chunks:
                        ranked.setdefault(cid, []).append((rank, "vector"))
            except Exception as e:  # degrade to keyword-only search
                log.warning("vector search failed: %s", e)
        fused = sorted(
            ((cid, sum(1.0 / (RRF_K + r + 1) for r, _ in hits), [m for _, m in hits]) for cid, hits in ranked.items()),
            key=lambda x: -x[1],
        )[:k]
        return [
            SearchHit(cid, self.chunks[cid].doc_id, self.chunks[cid].title, self.chunks[cid].text, score, by)
            for cid, score, by in fused
        ]
