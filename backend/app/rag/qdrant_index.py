"""Qdrant hybrid index: BM25 sparse vectors + dense embeddings, fused in Qdrant.

- Keyword leg: our script-agnostic Mongolian features (Latin and Cyrillic map
  to the same keys) are hashed into a sparse vector. Qdrant applies IDF
  (`Modifier.IDF`); the document side carries BM25's saturated term frequency
  (length normalization b=0: chunks are capped at ~900 chars, and b=0 keeps
  stored vectors independent of corpus statistics, so unchanged chunks never
  need rewriting).
- Dense leg (optional): Voyage embeddings in the named vector "dense".
- Both legs run in one `query_points` call and are fused with RRF server-side.

Qdrant is the single shared index for all backend replicas, so an admin edit
is visible to every replica as soon as it is written.
"""

import logging
import uuid
import warnings
import zlib
from collections import Counter

from qdrant_client import AsyncQdrantClient, models

from app.rag.documents import Chunk
from app.rag.embeddings import Embedder
from app.rag.knowledge_base import SearchHit, features

log = logging.getLogger(__name__)

SPARSE = "bm25"
DENSE = "dense"
K1 = 1.4
_NS = uuid.UUID("6f1c3c5e-9d0b-4b8e-9a51-7a0f3c2d8e11")


def sparse_doc(text: str) -> models.SparseVector:
    tf = Counter(_hash(f) for f in features(text))
    idx = sorted(tf)
    return models.SparseVector(indices=idx, values=[tf[i] * (K1 + 1) / (tf[i] + K1) for i in idx])


def sparse_query(text: str) -> models.SparseVector:
    idx = sorted({_hash(f) for f in features(text)})
    return models.SparseVector(indices=idx, values=[1.0] * len(idx))


def _hash(feature: str) -> int:
    return zlib.crc32(feature.encode())


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NS, chunk_id))


class QdrantHybridIndex:
    def __init__(self, client: AsyncQdrantClient, collection: str, embedder: Embedder | None, dim: int):
        self.client = client
        self.collection = collection
        self.embedder = embedder
        self.dim = dim
        self._ready = False

    async def ensure_collection(self) -> None:
        want_dense = self.embedder is not None
        if await self.client.collection_exists(self.collection):
            info = await self.client.get_collection(self.collection)
            vectors = info.config.params.vectors or {}
            has_dense = isinstance(vectors, dict) and DENSE in vectors
            dim_ok = not want_dense or (has_dense and vectors[DENSE].size == self.dim)
            if has_dense == want_dense and dim_ok:
                self._ready = True
                return
            # Embedding provider or dimension changed: the collection is derived data, rebuild it.
            log.warning("qdrant collection %s has a different vector config; recreating", self.collection)
            await self.client.delete_collection(self.collection)
        await self.client.create_collection(
            self.collection,
            vectors_config={DENSE: models.VectorParams(size=self.dim, distance=models.Distance.COSINE)}
            if want_dense
            else {},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        with warnings.catch_warnings():  # embedded (dev) Qdrant ignores payload indexes and warns
            warnings.simplefilter("ignore", UserWarning)
            await self.client.create_payload_index(self.collection, "persona", models.PayloadSchemaType.KEYWORD)
        self._ready = True

    async def _existing(self) -> dict[str, tuple[str, str]]:
        """point id -> (chunk_id, content_hash)"""
        out: dict[str, tuple[str, str]] = {}
        offset = None
        while True:
            points, offset = await self.client.scroll(
                self.collection, limit=512, offset=offset, with_payload=["chunk_id", "content_hash"]
            )
            for p in points:
                out[str(p.id)] = (p.payload["chunk_id"], p.payload["content_hash"])
            if offset is None:
                return out

    async def sync(self, chunks: list[Chunk]) -> dict[str, int]:
        if not self._ready:
            await self.ensure_collection()
        existing = await self._existing()
        wanted = {point_id(c.id): c for c in chunks}
        stale = [c for pid, c in wanted.items() if existing.get(pid, (None, None))[1] != c.content_hash]
        if stale:
            texts = [f"{c.title}\n{c.text}" for c in stale]
            dense = await self.embedder.embed(texts, "document") if self.embedder else [None] * len(stale)
            points = []
            for c, text, vec in zip(stale, texts, dense):
                vector: dict = {SPARSE: sparse_doc(text)}
                if vec is not None:
                    vector[DENSE] = vec
                points.append(
                    models.PointStruct(
                        id=point_id(c.id),
                        vector=vector,
                        payload={
                            "chunk_id": c.id, "doc_id": c.doc_id, "doc_title": c.doc_title, "title": c.title,
                            "text": c.text, "persona": c.persona, "content_hash": c.content_hash,
                        },
                    )
                )
            for i in range(0, len(points), 256):
                await self.client.upsert(self.collection, points=points[i : i + 256])
        removed = [pid for pid in existing if pid not in wanted]
        if removed:
            await self.client.delete(self.collection, points_selector=models.PointIdsList(points=removed))
        return {"upserted": len(stale), "embedded": len(stale) if self.embedder else 0, "removed": len(removed)}

    async def search(self, query: str, personas: list[str], k: int) -> list[SearchHit]:
        if not self._ready:
            await self.ensure_collection()
        flt = models.Filter(must=[models.FieldCondition(key="persona", match=models.MatchAny(any=personas))])
        sq = sparse_query(query)
        dense = None
        if self.embedder:
            try:
                dense = (await self.embedder.embed([query], "query"))[0]
            except Exception as e:  # degrade to keyword-only search
                log.warning("query embedding failed: %s", e)
        if dense is not None:
            resp = await self.client.query_points(
                self.collection,
                prefetch=[
                    models.Prefetch(query=sq, using=SPARSE, filter=flt, limit=20),
                    models.Prefetch(query=dense, using=DENSE, filter=flt, limit=20),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=k,
                with_payload=True,
            )
            by = ["bm25", "vector"]
        else:
            if not sq.indices:
                return []
            resp = await self.client.query_points(
                self.collection, query=sq, using=SPARSE, query_filter=flt, limit=k, with_payload=True
            )
            by = ["bm25"]
        return [
            SearchHit(
                chunk_id=p.payload["chunk_id"], doc_id=p.payload["doc_id"], doc_title=p.payload["doc_title"],
                title=p.payload["title"], text=p.payload["text"], score=float(p.score), matched_by=by,
            )
            for p in resp.points
        ]
