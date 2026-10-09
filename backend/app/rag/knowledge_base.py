"""Knowledge base facade + the in-process hybrid index.

`KnowledgeBase` chunks documents and delegates indexing/search to a
`SearchIndex`:
- `QdrantHybridIndex` (default, rag/qdrant_index.py): BM25 sparse + dense
  vectors in Qdrant, fused server-side. Shared by all backend replicas.
- `LocalHybridIndex` (below): BM25 in process memory + an optional
  `VectorIndex` (pgvector or in-memory). Each replica holds its own BM25
  index, so admin edits only reach the replica that handled them.
"""

import asyncio
import hashlib
import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from app.guardrails.translit import key_tokens
from app.rag.documents import Chunk, KnowledgeDoc, chunk_doc
from app.rag.embeddings import Embedder
from app.rag.vector import VectorIndex

if TYPE_CHECKING:
    from app.rag.contextual import Contextualizer
    from app.rag.rerank import Reranker

log = logging.getLogger(__name__)

RRF_K = 60
_PREFIX = 5  # crude stemming for agglutinative Mongolian: захиалга/захиалгаа/захиалгын


@dataclass
class SearchHit:
    chunk_id: str
    doc_id: str
    doc_title: str
    title: str  # chunk title: "<doc title> — <section>"
    text: str
    score: float
    matched_by: list[str]


def features(text: str) -> list[str]:
    """Script-agnostic keyword features shared by every keyword index."""
    toks = key_tokens(text)
    return toks + [f"{t[:_PREFIX]}~" for t in toks if len(t) > _PREFIX]


class SearchIndex(Protocol):
    async def sync(self, chunks: list[Chunk]) -> dict[str, int]:
        """Make the index contain exactly `chunks`; re-embed only changed ones."""
        ...

    async def search(self, query: str, personas: list[str], k: int) -> list[SearchHit]: ...


PERSONA_KEYS = ("driver", "client", "customer")


@dataclass
class KnowledgePack:
    """Everything one persona may know, rendered for the system prompt (full-context mode)."""

    id: str
    persona: str
    docs: dict[str, tuple[str, str]] = field(default_factory=dict)  # doc_id -> (title, body)
    text: str = ""
    est_tokens: int = 0

    @classmethod
    def build(cls, persona: str, docs: list[KnowledgeDoc]) -> "KnowledgePack":
        mine = {d.id: (d.title, d.body) for d in sorted(docs, key=lambda d: d.id) if d.persona in (persona, "all")}
        text = render_docs(mine)
        # Conservative estimate for Cyrillic Mongolian; only used to choose the mode.
        return cls(id=hashlib.sha256(text.encode()).hexdigest()[:20], persona=persona, docs=mine, text=text,
                   est_tokens=len(text) // 2)

    def to_json(self) -> dict:
        return {"id": self.id, "persona": self.persona, "docs": {k: list(v) for k, v in self.docs.items()}}

    @classmethod
    def from_json(cls, d: dict) -> "KnowledgePack":
        docs = {k: (v[0], v[1]) for k, v in d["docs"].items()}
        text = render_docs(docs)
        return cls(id=d["id"], persona=d["persona"], docs=docs, text=text, est_tokens=len(text) // 2)


def render_docs(docs: dict[str, tuple[str, str]]) -> str:
    parts = [f'<doc id="{i}" title="{t}">\n{b}\n</doc>' for i, (t, b) in docs.items()]
    return "<knowledge_base>\n" + "\n".join(parts) + "\n</knowledge_base>"


def diff_packs(old: KnowledgePack, new: KnowledgePack) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """(added or changed docs, removed doc titles)."""
    changed = {i: v for i, v in new.docs.items() if old.docs.get(i) != v}
    removed = [old.docs[i][0] for i in old.docs if i not in new.docs]
    return changed, removed


class KnowledgeBase:
    def __init__(self, index: SearchIndex, *, mode: Literal["auto", "full", "rag"] = "auto",
                 full_max_tokens: int = 50_000, reranker: "Reranker | None" = None,
                 contextualizer: "Contextualizer | None" = None):
        self.index = index
        self.mode = mode
        self.full_max_tokens = full_max_tokens
        self.reranker = reranker
        self.contextualizer = contextualizer
        self.docs: dict[str, KnowledgeDoc] = {}
        self.packs: dict[str, KnowledgePack] = {}  # persona -> current pack
        self.pack_cache: dict[str, KnowledgePack] = {}  # pack id -> pack (incl. older ones)
        self._lock = asyncio.Lock()

    async def rebuild(self, docs: list[KnowledgeDoc]) -> dict[str, int]:
        """(Re)index all public docs. `visibility: internal` docs never reach the index."""
        async with self._lock:
            public = [d for d in docs if d.visibility != "internal"]
            chunks: list[Chunk] = []
            for d in public:
                cs = chunk_doc(d)
                if self.contextualizer:
                    cs = await self.contextualizer.apply(d, cs)
                chunks += cs
            stats = await self.index.sync(chunks)
            self.docs = {d.id: d for d in public}
            self.packs = {p: KnowledgePack.build(p, public) for p in PERSONA_KEYS}
            for pack in self.packs.values():
                self.pack_cache[pack.id] = pack
            stats = {"docs": len(public), "chunks": len(chunks), **stats,
                     **{f"tokens_{p}": pk.est_tokens for p, pk in self.packs.items()}}
            log.info("knowledge indexed: %s", stats)
            return stats

    def mode_for(self, persona: str) -> Literal["full", "rag"]:
        if self.mode != "auto":
            return self.mode
        pack = self.packs.get(persona)
        return "full" if pack and pack.est_tokens <= self.full_max_tokens else "rag"

    async def search(self, query: str, persona: str, k: int = 5) -> list[SearchHit]:
        if not query.strip():
            return []
        if self.reranker:
            candidates = await self.index.search(query, [persona, "all"], max(20, k * 4))
            return await self.reranker.rerank(query, candidates, k)
        return await self.index.search(query, [persona, "all"], k)


# --------------------------------------------------------------- local index
class BM25:
    def __init__(self, k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: list[tuple[str, Counter, int]] = []
        self.df: Counter = Counter()
        self.avgdl = 0.0

    def build(self, items: list[tuple[str, str]]) -> None:
        self.docs, self.df = [], Counter()
        for cid, text in items:
            feats = features(text)
            tf = Counter(feats)
            self.docs.append((cid, tf, len(feats)))
            self.df.update(tf.keys())
        self.avgdl = sum(n for _, _, n in self.docs) / max(len(self.docs), 1)

    def search(self, query: str, allowed: set[str], k: int) -> list[tuple[str, float]]:
        q = set(features(query))
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


class LocalHybridIndex:
    def __init__(self, embedder: Embedder | None = None, vectors: VectorIndex | None = None):
        self.embedder = embedder
        self.vectors = vectors if embedder else None
        self.chunks: dict[str, Chunk] = {}
        self.bm25 = BM25()

    async def sync(self, chunks: list[Chunk]) -> dict[str, int]:
        self.chunks = {c.id: c for c in chunks}
        self.bm25.build([(c.id, c.index_text) for c in chunks])
        embedded = 0
        if self.embedder and self.vectors:
            known = await self.vectors.known_hashes()
            stale = [c for c in chunks if known.get(c.id) != c.content_hash]
            if stale:
                vecs = await self.embedder.embed([c.index_text for c in stale], "document")
                await self.vectors.upsert(stale, vecs)
                embedded = len(stale)
            await self.vectors.retain(set(self.chunks))
        return {"embedded": embedded}

    async def search(self, query: str, personas: list[str], k: int) -> list[SearchHit]:
        allowed = {cid for cid, c in self.chunks.items() if c.persona in personas}
        if not allowed:
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
        out = []
        for cid, score, by in fused:
            c = self.chunks[cid]
            out.append(SearchHit(cid, c.doc_id, c.doc_title, c.title, c.text, score, by))
        return out
