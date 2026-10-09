"""Vector indexes: in-process (dev/tests) and pgvector (production)."""

import math
from typing import Protocol

from app.rag.documents import Chunk


class VectorIndex(Protocol):
    async def known_hashes(self) -> dict[str, str]:
        """chunk_id -> content_hash of chunks already embedded."""
        ...

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    async def retain(self, chunk_ids: set[str]) -> None:
        """Delete every chunk not in `chunk_ids`."""
        ...

    async def search(self, vector: list[float], personas: list[str], k: int) -> list[tuple[str, float]]: ...


class InMemoryVectorIndex:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[str, str, list[float]]] = {}  # id -> (persona, hash, vec)

    async def known_hashes(self) -> dict[str, str]:
        return {cid: h for cid, (_, h, _) in self._rows.items()}

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for c, v in zip(chunks, vectors):
            self._rows[c.id] = (c.persona, c.content_hash, v)

    async def retain(self, chunk_ids: set[str]) -> None:
        for cid in list(self._rows):
            if cid not in chunk_ids:
                del self._rows[cid]

    async def search(self, vector: list[float], personas: list[str], k: int) -> list[tuple[str, float]]:
        scored = [
            (cid, _cosine(vector, v)) for cid, (p, _, v) in self._rows.items() if p in personas
        ]
        return sorted(scored, key=lambda x: -x[1])[:k]


class PgVectorIndex:
    """Stores chunk embeddings in `knowledge_chunks` (see db/schema.sql)."""

    def __init__(self, pool) -> None:  # psycopg_pool.AsyncConnectionPool
        self.pool = pool

    async def known_hashes(self) -> dict[str, str]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT id, content_hash FROM knowledge_chunks")).fetchall()
        return {r[0]: r[1] for r in rows}

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    """
                    INSERT INTO knowledge_chunks (id, doc_id, persona, title, content, content_hash, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                      doc_id = EXCLUDED.doc_id, persona = EXCLUDED.persona, title = EXCLUDED.title,
                      content = EXCLUDED.content, content_hash = EXCLUDED.content_hash,
                      embedding = EXCLUDED.embedding, updated_at = now()
                    """,
                    [
                        (c.id, c.doc_id, c.persona, c.title, c.text, c.content_hash, _vec_literal(v))
                        for c, v in zip(chunks, vectors)
                    ],
                )

    async def retain(self, chunk_ids: set[str]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM knowledge_chunks WHERE NOT (id = ANY(%s))", (list(chunk_ids),))

    async def search(self, vector: list[float], personas: list[str], k: int) -> list[tuple[str, float]]:
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, 1 - (embedding <=> %s::vector) AS score
                    FROM knowledge_chunks
                    WHERE persona = ANY(%s)
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (_vec_literal(vector), personas, _vec_literal(vector), k),
                )
            ).fetchall()
        return [(r[0], float(r[1])) for r in rows]


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
